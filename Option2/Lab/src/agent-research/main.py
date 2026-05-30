"""Tech Research Agent - A2A endpoint with LangGraph reasoning."""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import agent as _agent_module
from agent import run_research
from a2a import get_agent_card, make_task_response
from a2a_models import JsonRpcRequest
from a2a_auth import verify_a2a_token

# Load .env for local development (walk up to find Lab/.env)
# Does not overwrite vars already set (e.g. from Docker/CI)
for parent in [Path.cwd(), *Path.cwd().parents]:
    env_path = parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
        print(f"[ResearchAgent] Loaded env from {env_path}")
        break
else:
    print(f"[ResearchAgent] No .env file found (walked up from {Path.cwd()})")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

# Configure OpenTelemetry
resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "research-agent")})

# Traces
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Logs + Events (required for gen_ai prompt/response content capture)
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
set_logger_provider(logger_provider)

# Auto-instrument OpenAI / Azure OpenAI calls (emits gen_ai.* spans for the Agents blade)
OpenAIInstrumentor().instrument()

# Auto-instrument httpx (used for outbound calls)
HTTPXClientInstrumentor().instrument()

app = FastAPI(title="Tech Research Agent")

# Auto-instrument FastAPI (traces all inbound requests)
FastAPIInstrumentor.instrument_app(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    """A2A Agent Card for discovery (serves both v0.3 and v1.0 well-known paths)."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{scheme}://{request.headers.get('host', request.url.netloc)}"
    return get_agent_card(base_url)


@app.post("/a2a")
@app.post("/")
async def handle_task(request: Request, _auth: None = Depends(verify_a2a_token)):
    """A2A JSON-RPC endpoint for task submission."""
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
    task_id = params.get("id", "unknown")
    message = params.get("message", {})

    # Extract topic from message parts
    topic = None
    preferences = {}
    for part in message.get("parts", []):
        part_kind = part.get("kind") or part.get("type")
        if part_kind == "text":
            topic = part["text"]
        elif part_kind == "data":
            data = part.get("data", {})
            topic = topic or data.get("topic")
            preferences = data

    if not topic:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32602, "message": "No topic provided"}, "id": rpc_request.id},
            status_code=400,
        )

    try:
        result = await run_research(topic, preferences)
    except Exception as exc:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32603, "message": f"Internal error: {exc}"}, "id": rpc_request.id},
            status_code=500,
        )

    return JSONResponse(make_task_response(task_id, result, rpc_request.id))


@app.get("/health")
async def health():
    return {"status": "healthy", "agent": os.getenv("OTEL_SERVICE_NAME", "research-agent")}


@app.get("/status")
async def status():
    """Live status endpoint — polled by Dev UI to show sandbox progress before brief is ready."""
    return JSONResponse(_agent_module._live_status)
