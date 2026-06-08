"""XTTS-v2 TTS inference server for GPU workload on ACA."""

import io
import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import model_loader

# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

resource = Resource.create(
    {"service.name": os.getenv("OTEL_SERVICE_NAME", "tts-server")}
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="TTS Server (XTTS-v2)")

FastAPIInstrumentor.instrument_app(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def load_model_and_voices_on_startup():
    """Eagerly load model + voice samples so health returns 200 and ACA routes traffic."""
    model_loader.load_voice_samples()
    logger.info("Voice samples loaded, now loading XTTS-v2 model (this may take several minutes)…")
    model_loader.get_model()
    logger.info("Model loaded — server is ready.")


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------
class SynthesizeRequest(BaseModel):
    text: str
    speaker_wav: str
    language: str = Field(default="en")
    speed: float = Field(default=1.0)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Liveness / readiness probe. Returns 503 until model is loaded."""
    try:
        import torch
        gpu_available = torch.cuda.is_available()
    except ImportError:
        gpu_available = False

    model_loaded = model_loader._model is not None
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={
            "status": "ready" if model_loaded else "starting",
            "model": "xtts-v2",
            "gpu_available": gpu_available,
            "voices": model_loader.get_loaded_voices(),
        },
        status_code=200 if model_loaded else 503,
    )


@app.post("/warmup")
async def warmup():
    """Pre-load model and voice samples so first synthesis is fast."""
    import asyncio
    await asyncio.to_thread(model_loader.get_model)
    voices = model_loader.load_voice_samples()
    return {"status": "ready", "voices_loaded": sorted(voices.keys())}


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """Generate speech from text using XTTS-v2."""
    # --- Validate inputs ---------------------------------------------------
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text must be non-empty")

    try:
        speaker_path = model_loader.get_voice_path(req.speaker_wav)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # --- Synthesize --------------------------------------------------------
    tmp_path: str | None = None
    try:
        import asyncio
        model = await asyncio.to_thread(model_loader.get_model)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(tmp_fd)

        with tracer.start_as_current_span("tts.synthesize") as span:
            span.set_attribute("tts.text_length", len(req.text))
            span.set_attribute("tts.language", req.language)
            span.set_attribute("tts.speaker_wav", req.speaker_wav)

            await asyncio.to_thread(
                model.tts_to_file,
                text=req.text,
                file_path=tmp_path,
                speaker_wav=speaker_path,
                language=req.language,
                speed=req.speed,
            )

        audio_bytes = Path(tmp_path).read_bytes()
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=speech.wav"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
