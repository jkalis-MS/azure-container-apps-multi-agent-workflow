"""Podcaster agent — FastAPI A2A endpoint with async task model (202 + polling)."""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env for local development (walk up to find Lab/.env)
# Does not overwrite vars already set (e.g. from Docker/CI)
for _parent in [Path.cwd(), *Path.cwd().parents]:
    _env_path = _parent / ".env"
    if _env_path.is_file():
        load_dotenv(_env_path, override=False)
        print(f"[PodcasterAgent] Loaded env from {_env_path}")
        break
else:
    print(f"[PodcasterAgent] No .env file found (walked up from {Path.cwd()})")

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from a2a import get_agent_card, make_task_response
from a2a_models import JsonRpcRequest
from a2a_auth import verify_a2a_token
from agent import generate_podcast, get_task

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._events import EventLoggerProvider
from opentelemetry._events import set_event_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry setup
# ---------------------------------------------------------------------------
resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "podcaster-agent")})

# Traces
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Logs + Events (required for gen_ai prompt/response content capture)
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
set_logger_provider(logger_provider)
set_event_logger_provider(EventLoggerProvider(logger_provider))

# Auto-instrument httpx for outbound TTS/fetch/OpenAI calls.
HTTPXClientInstrumentor().instrument()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Podcaster Agent")

FastAPIInstrumentor.instrument_app(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track background tasks so they aren't garbage-collected
_background_tasks: set[asyncio.Task] = set()


@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    """A2A Agent Card for discovery (serves both v0.3 and v1.0 well-known paths)."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{scheme}://{request.headers.get('host', request.url.netloc)}"
    return get_agent_card(base_url)


@app.post("/a2a")
async def handle_task(request: Request, _auth: None = Depends(verify_a2a_token)):
    """A2A JSON-RPC endpoint — accepts a research brief and returns 202 with a poll URL."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status_code=400,
        )

    try:
        rpc_request = JsonRpcRequest(**body)
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": body.get("id")},
            status_code=400,
        )

    if rpc_request.method not in ("tasks/send", "SendMessage", "message/send"):
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": rpc_request.id},
            status_code=400,
        )

    params = rpc_request.params or {}
    task_id = str(uuid.uuid4())
    message = params.get("message", {})

    # Extract research brief from message parts
    research_brief: dict | None = None
    for part in message.get("parts", []):
        part_kind = part.get("kind") or part.get("type")
        if part_kind == "data" and isinstance(part.get("data"), dict):
            data = part["data"]
            # Unwrap if the UI wrapped the brief inside a `research_brief` key
            research_brief = data.get("research_brief", data) if "research_brief" in data else data
            break
        if part_kind == "text":
            # Try parsing text part as JSON
            try:
                parsed = json.loads(part["text"])
                research_brief = parsed.get("research_brief", parsed) if isinstance(parsed, dict) and "research_brief" in parsed else parsed
            except (json.JSONDecodeError, TypeError):
                research_brief = {"topic": part["text"], "summary": part["text"], "sources": []}

    if not research_brief:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32602, "message": "No research brief provided"}, "id": rpc_request.id},
            status_code=400,
        )

    # Store the request_id so we can build a proper A2A response later
    from agent import _tasks
    _tasks[task_id] = {"status": "accepted", "progress": "queued", "result": None, "request_id": rpc_request.id}

    # Launch podcast generation as a background asyncio task
    bg_task = asyncio.create_task(generate_podcast(task_id, research_brief))
    _background_tasks.add(bg_task)
    bg_task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        {"task_id": task_id, "status": "accepted", "poll_url": f"/tasks/{task_id}"},
        status_code=202,
    )


@app.get("/tasks/{task_id}")
async def poll_task(task_id: str, _auth: None = Depends(verify_a2a_token)):
    """Poll task status. Returns consistent format for both in-progress and completed tasks."""
    task = get_task(task_id)
    if task is None:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    return JSONResponse({
        "task_id": task_id,
        "status": task["status"],
        "progress": task.get("progress"),
        "result": task.get("result"),
        "error": task.get("error"),
    })


@app.get("/health")
async def health():
    return {"status": "healthy", "agent": os.getenv("OTEL_SERVICE_NAME", "podcaster-agent")}
