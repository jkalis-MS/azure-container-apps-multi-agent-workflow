"""
World Cup Match Narration Generator — FastAPI service with A2A protocol.

Uses GitHub Copilot SDK for multi-turn script generation (generate → critique → refine)
and Azure OpenAI TTS-HD for audio synthesis.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("narration")

# ---------------------------------------------------------------------------
# OpenTelemetry (graceful fallback)
# ---------------------------------------------------------------------------
try:
    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if conn_str:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(
            connection_string=conn_str,
            service_name=os.getenv("OTEL_SERVICE_NAME", "narration-agent"),
            instrumentation_options={
                "azure_sdk": {"enabled": False},
                "fastapi": {"enabled": True},
                "httpx": {"enabled": False},
            },
        )
        logger.info("OTEL → Azure Monitor (distro)")
    elif otlp_endpoint:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "narration-agent")})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        logger.info(f"OTEL → {otlp_endpoint}")

    # Auto-instrument OpenAI SDK for gen_ai.* spans (App Insights Agents blade)
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
        OpenAIInstrumentor().instrument()
    except Exception:
        pass

    from opentelemetry import trace
    _otel_ok = True
    _tracer = trace.get_tracer("narration-agent")
except Exception as _otel_err:
    logger.warning(f"OTEL init failed (non-fatal): {_otel_err}")
    _otel_ok = False
    import contextlib
    class _NoopTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, *a, **kw):
            yield None
    _tracer = _NoopTracer()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="World Cup Narration Agent", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# A2A Card
# ---------------------------------------------------------------------------
def get_agent_card(base_url: str) -> dict:
    return {
        "name": "narration-agent",
        "description": "World Cup 2026 podcast narration generator — Copilot SDK multi-turn script + TTS audio",
        "url": f"{base_url}/a2a",
        "version": "3.0.0",
        "protocolVersion": "0.3.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [{
            "id": "generate-narration",
            "name": "Generate Match Narration",
            "description": "Generates a passionate sports narration script with TTS audio from match simulation data",
            "tags": ["soccer", "world-cup", "narration", "tts", "podcast"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        }],
    }


# ---------------------------------------------------------------------------
# GitHub Copilot SDK — script generation (multi-turn)
# ---------------------------------------------------------------------------
import httpx
from json_repair import repair_json

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
API_KEY = os.environ.get("AZURE_OPENAI_KEY", "")
CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
TTS_DEPLOYMENT = os.environ.get("AZURE_OPENAI_TTS_DEPLOYMENT", "tts-hd")
API_VERSION = "2024-12-01-preview"


SYSTEM_PROMPT = """You are the most passionate and legendary soccer commentator in the world.
Your style combines the energy of Ray Hudson, the drama of Peter Drury, and the excitement of Andrés Cantor.
Write a narration of approximately 60 seconds (about 150-180 words) of the match.

NARRATION RULES:
- Each goal MUST have an explosive celebration: "GOAL! GOAL! GOAL! GOAL! GOAL! WHAT A GOAL!"
- After the celebration, repeat the scorer's name with raw emotion: "IT'S [NAME]! [NAME]! [NAME]!"
- Use UPPERCASE for moments of peak excitement and screaming
- Use onomatopoeia in CAPS: "BANG!", "BOOM!", "OH NO NO NO... YESSS!"
- Alternate between quiet suspense moments (lowercase, short phrases) and EXPLOSIVE BURSTS OF EMOTION IN CAPS
- Use ellipsis (...) to create dramatic pauses before key moments
- Use double and triple exclamation marks: UNBELIEVABLE!!!
- For goals: narrate the build-up with rising tension, THEN explode with the goal
- End with an epic, emotional closing line about the final result
- Write ONLY the narration text, no stage directions or instructions in brackets.
- Reference the flags and nations: "Mexico rises!", "The Czech Republic strikes back!"
"""

CRITIQUE_PROMPT = """Review the narration script you just generated. Rate it 1-10 on:
1. Does it cover ALL goals with proper build-up and celebration?
2. Is the energy contrast between quiet moments and EXPLOSIVE moments strong enough?
3. Is it approximately 150-180 words (good for 60 seconds of audio)?
4. Does the closing line feel epic and memorable?

Return ONLY a JSON object: {"score": <int 1-10>, "feedback": "<brief issues to fix>"}
If the script is good (score >= 7), set feedback to empty string."""


async def _generate_script_copilot(simulation: dict) -> tuple[str, str]:
    """Use Copilot SDK to generate narration with multi-turn refinement.
    
    Returns (final_script, critique_text).
    """
    from copilot import CopilotClient, ProviderConfig, SubprocessConfig
    from copilot.session import PermissionHandler

    sim_text = json.dumps(simulation, indent=2)

    client = CopilotClient(SubprocessConfig(use_logged_in_user=False))
    await client.start()

    try:
        session = await client.create_session(
            on_permission_request=PermissionHandler.approve_all,
            model=CHAT_DEPLOYMENT,
            provider=ProviderConfig(
                type="azure",
                base_url=ENDPOINT,
                api_key=API_KEY,
                azure={"api_version": API_VERSION},
            ),
            system_message={"content": SYSTEM_PROMPT},
            infinite_sessions={"enabled": False},
        )

        # Turn 1: Generate initial narration
        event = await session.send_and_wait(
            f"Narrate this World Cup match:\n\n{sim_text}",
            timeout=120.0,
        )
        if not event or not event.data.content:
            raise ValueError("No response from Copilot SDK")
        script = event.data.content.strip()
        logger.info(f"Turn 1 — Script: {script[:80]}...")

        # Turn 2: Self-critique (multi-turn, same session)
        critique_event = await session.send_and_wait(
            CRITIQUE_PROMPT,
            timeout=60.0,
        )
        critique_text = ""
        if critique_event and critique_event.data.content:
            critique_text = critique_event.data.content.strip()
            logger.info(f"Turn 2 — Critique: {critique_text[:80]}...")

            # Parse critique score
            try:
                raw = critique_text
                if "```" in raw:
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                    raw = raw.strip()
                repaired = repair_json(raw, return_objects=False)
                critique_data = json.loads(repaired)
                score = critique_data.get("score", 10)
                feedback = critique_data.get("feedback", "")

                if score < 7 and feedback:
                    # Turn 3: Refine based on critique
                    refine_prompt = (
                        f"The script scored {score}/10. Issues: {feedback}\n\n"
                        "Please rewrite the narration addressing these issues. "
                        "Output ONLY the improved narration text."
                    )
                    refine_event = await session.send_and_wait(
                        refine_prompt,
                        timeout=120.0,
                    )
                    if refine_event and refine_event.data.content:
                        script = refine_event.data.content.strip()
                        logger.info(f"Turn 3 — Refined: {script[:80]}...")
            except Exception as parse_err:
                logger.warning(f"Critique parse failed (keeping original): {parse_err}")

        await session.disconnect()
        return script, critique_text

    finally:
        await client.stop()


# ---------------------------------------------------------------------------
# TTS (direct Azure OpenAI call — Copilot SDK doesn't handle audio)
# ---------------------------------------------------------------------------
async def _tts(text: str) -> bytes:
    """Call Azure OpenAI TTS-HD to produce MP3 audio."""
    url = f"{ENDPOINT}openai/deployments/{TTS_DEPLOYMENT}/audio/speech?api-version={API_VERSION}"
    async with httpx.AsyncClient(verify=False, timeout=120.0) as client:
        resp = await client.post(url, headers={"api-key": API_KEY, "Content-Type": "application/json"},
                                 json={"model": TTS_DEPLOYMENT, "voice": "echo", "input": text, "response_format": "mp3", "speed": 1.15})
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Fallback: Direct Azure OpenAI call (when Copilot SDK times out)
# ---------------------------------------------------------------------------
async def _generate_script_direct(simulation: dict) -> str:
    """Generate narration script via direct Azure OpenAI API call."""
    sim_text = json.dumps(simulation, indent=2)
    url = f"{ENDPOINT}openai/deployments/{CHAT_DEPLOYMENT}/chat/completions?api-version={API_VERSION}"
    
    async with httpx.AsyncClient(verify=False, timeout=120.0) as client:
        resp = await client.post(
            url,
            headers={"api-key": API_KEY, "Content-Type": "application/json"},
            json={
                "model": CHAT_DEPLOYMENT,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Narrate this World Cup match:\n\n{sim_text}"},
                ],
                "temperature": 0.9,
                "max_tokens": 800,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Main narration pipeline
# ---------------------------------------------------------------------------
async def generate_narration(simulation: dict) -> dict[str, Any]:
    """Multi-turn narration via Copilot SDK: generate → critique → refine → TTS.
    Falls back to direct Azure OpenAI if Copilot SDK times out."""

    # Script generation — try Copilot SDK, fallback to direct AOAI
    with _tracer.start_as_current_span(
        "invoke_agent narration-agent",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "narration-agent",
            "gen_ai.agent.id": "narration-agent",
            "gen_ai.system": "azure.ai.openai",
        },
    ):
        with _tracer.start_as_current_span(
            "execute_tool script_generation",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "script_generation",
                "gen_ai.tool.type": "function",
            },
        ):
            try:
                logger.info("Generating script via GitHub Copilot SDK (multi-turn)")
                script, critique = await _generate_script_copilot(simulation)
                logger.info(f"Script ready ({len(script.split())} words)")
            except Exception as sdk_err:
                logger.warning(f"Copilot SDK failed ({sdk_err}), falling back to direct Azure OpenAI")
                script = await _generate_script_direct(simulation)
                critique = ""
                logger.info(f"Script ready via fallback ({len(script.split())} words)")

        # TTS synthesis
        with _tracer.start_as_current_span(
            "execute_tool tts_synthesis",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "tts_synthesis",
                "gen_ai.tool.type": "function",
            },
        ):
            logger.info("TTS synthesis")
            audio_bytes = await _tts(script)
            audio_base64 = base64.b64encode(audio_bytes).decode()
            logger.info(f"Audio: {len(audio_bytes)} bytes")

        return {
            "script": script,
            "audioBase64": audio_base64,
            "audioMimeType": "audio/mpeg",
            "critique": critique,
        }


# ---------------------------------------------------------------------------
# A2A Endpoints
# ---------------------------------------------------------------------------

@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{scheme}://{request.headers.get('host', request.url.netloc)}"
    return get_agent_card(base_url)


@app.post("/a2a")
async def handle_a2a(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}, status_code=400)

    method = body.get("method", "")
    if method not in ("tasks/send", "SendMessage", "message/send"):
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": body.get("id")}, status_code=400)

    params = body.get("params", {})
    task_id = params.get("id", "unknown")
    message = params.get("message", {})

    # Extract simulation from message parts
    simulation = None
    for part in message.get("parts", []):
        kind = part.get("kind") or part.get("type")
        if kind == "data" and part.get("data"):
            data = part["data"]
            simulation = data.get("simulation", data)
            break

    if not simulation:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32602, "message": "No simulation data"}, "id": body.get("id")}, status_code=400)

    try:
        result = await generate_narration(simulation)
    except Exception as exc:
        logger.exception("Narration failed")
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}, "id": body.get("id")}, status_code=500)

    return JSONResponse({
        "jsonrpc": "2.0",
        "result": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "data", "data": result}]}],
        },
        "id": body.get("id"),
    })


# ---------------------------------------------------------------------------
# Legacy endpoint
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    simulation: dict


@app.post("/generate")
async def generate_legacy(req: GenerateRequest):
    logger.info("Legacy /generate called")
    try:
        result = await generate_narration(req.simulation)
        return result
    except Exception as exc:
        logger.exception("Narration failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "narration-generator", "framework": "github-copilot-sdk"}
