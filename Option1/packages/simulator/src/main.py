"""
World Cup Match Simulator — FastAPI service with A2A protocol and LangGraph agent.

A2A:   GET  /.well-known/agent.json  — Agent Card discovery
       POST /a2a                      — JSON-RPC task execution
Legacy: POST /run                     — Backward-compatible direct endpoint
Health: GET  /health                  — Liveness probe
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .a2a import get_agent_card, make_task_response, make_error_response
from .a2a_models import JsonRpcRequest
from .a2a_auth import verify_a2a_token

# Lazy import to surface startup errors in logs rather than crash silently
_agent_module = None
def _get_run_simulation():
    global _agent_module
    if _agent_module is None:
        from . import agent as _mod
        _agent_module = _mod
    return _agent_module.run_simulation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("simulator")

# ---------------------------------------------------------------------------
# OpenTelemetry setup (spans won't export without OTEL_EXPORTER_OTLP_ENDPOINT or APPLICATIONINSIGHTS_CONNECTION_STRING)
# ---------------------------------------------------------------------------
try:
    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if conn_str:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(
            connection_string=conn_str,
            service_name=os.getenv("OTEL_SERVICE_NAME", "simulator-agent"),
            instrumentation_options={
                "azure_sdk": {"enabled": False},
                "fastapi": {"enabled": True},
                "httpx": {"enabled": False},  # Don't instrument httpx — it mangles sandbox auth headers
            },
        )
        logger.info("OTEL → Azure Monitor (distro)")
    elif otlp_endpoint:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "simulator-agent")})
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
    _otel_ok = True
except Exception as _otel_err:
    logger.warning(f"OTEL init failed (non-fatal): {_otel_err}")
    _otel_ok = False

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="World Cup Simulator Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request model (legacy)
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    prompt: str


# ---------------------------------------------------------------------------
# A2A Endpoints
# ---------------------------------------------------------------------------

@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request):
    """A2A Agent Card for discovery."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    base_url = f"{scheme}://{request.headers.get('host', request.url.netloc)}"
    return get_agent_card(base_url)


@app.post("/a2a")
async def handle_a2a_task(request: Request, _auth: None = Depends(verify_a2a_token)):
    """A2A JSON-RPC endpoint for task submission."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            make_error_response(-32700, "Parse error", None), status_code=400
        )

    try:
        rpc_request = JsonRpcRequest(**body)
    except Exception:
        return JSONResponse(
            make_error_response(-32700, "Parse error", body.get("id")), status_code=400
        )

    if rpc_request.method not in ("tasks/send", "SendMessage", "message/send"):
        return JSONResponse(
            make_error_response(-32601, "Method not found", rpc_request.id), status_code=400
        )

    params = rpc_request.params or {}
    task_id = params.get("id", "unknown")
    message = params.get("message", {})

    # Extract prompt from message parts
    prompt = None
    for part in message.get("parts", []):
        part_kind = part.get("kind") or part.get("type")
        if part_kind == "text":
            prompt = part.get("text")
        elif part_kind == "data":
            data = part.get("data", {})
            prompt = prompt or data.get("prompt")

    if not prompt:
        return JSONResponse(
            make_error_response(-32602, "No prompt provided", rpc_request.id), status_code=400
        )

    # Check for secure_egress mode (query param or message data)
    secure_egress = request.query_params.get("secure_egress", "").lower() in ("true", "1")
    for part in message.get("parts", []):
        if (part.get("kind") or part.get("type")) == "data":
            data = part.get("data", {})
            if data.get("secure_egress"):
                secure_egress = True

    try:
        result = await _get_run_simulation()(prompt, secure_egress=secure_egress)
    except Exception as exc:
        logger.exception("A2A simulation failed")
        return JSONResponse(
            make_error_response(-32603, f"Internal error: {exc}", rpc_request.id), status_code=500
        )

    return JSONResponse(make_task_response(task_id, result, rpc_request.id))


# ---------------------------------------------------------------------------
# Legacy endpoint (backward-compatible with orchestrator)
# ---------------------------------------------------------------------------

@app.post("/run")
async def run_simulation_legacy(req: RunRequest, request: Request):
    """Legacy endpoint — same as before, calls the LangGraph agent internally."""
    secure_egress = request.query_params.get("secure_egress", "").lower() in ("true", "1")
    logger.info(f"=== Legacy /run (secure_egress={secure_egress}) ===\nPrompt: {req.prompt[:120]}")
    try:
        result = await _get_run_simulation()(req.prompt, secure_egress=secure_egress)
        return result
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.exception("Simulation failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}\n{tb[-500:]}")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy", "agent": os.getenv("OTEL_SERVICE_NAME", "simulator-agent")}


@app.get("/debug/token-test")
async def debug_token_test():
    """Test sandbox API connectivity from inside the container using actual auth flow."""
    import httpx as _httpx
    from .sandbox_client import SandboxClient, TOKEN_RESOURCE
    
    # Try to get token the same way the sandbox client does
    token_info = {}
    static_token = os.environ.get("ACA_SANDBOX_TOKEN", "")
    if static_token:
        token_info["method"] = "static_env_var"
        token_info["token_length"] = len(static_token)
        token = static_token
    else:
        try:
            from azure.identity import DefaultAzureCredential
            cred = DefaultAzureCredential()
            t = cred.get_token(f"{TOKEN_RESOURCE}/.default")
            token = t.token
            token_info["method"] = "DefaultAzureCredential"
            token_info["token_length"] = len(token)
        except Exception as e:
            token_info["method"] = "FAILED"
            token_info["error"] = str(e)
            return token_info

    base = (
        f"https://management.westcentralus.azuredevcompute.io"
        f"/subscriptions/{os.environ.get('AZURE_SUBSCRIPTION_ID', '')}"
        f"/resourceGroups/{os.environ.get('ACA_RESOURCE_GROUP', '')}"
        f"/sandboxGroups/{os.environ.get('ACA_SANDBOX_GROUP', '')}"
    )
    url_list = f"{base}/sandboxes?includeDebug=true"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    transport = _httpx.AsyncHTTPTransport(verify=False)
    async with _httpx.AsyncClient(transport=transport, verify=False, timeout=30) as client:
        get_resp = await client.get(url_list, headers=headers)
    return {
        **token_info,
        "GET_status": get_resp.status_code,
        "GET_response": get_resp.text[:200],
    }
