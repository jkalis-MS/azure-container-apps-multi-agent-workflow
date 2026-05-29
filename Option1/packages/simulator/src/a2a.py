"""A2A protocol helpers — Agent Card and JSON-RPC response formatting."""

import os
from .a2a_models import (
    AgentCard, AgentSkill, AgentCapabilities,
    AuthScheme, JsonRpcResponse, TaskResult, TaskStatus, TaskArtifact, MessagePart
)
from .a2a_auth import A2A_AUTH_ENABLED

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:3001")


def get_agent_card(base_url: str | None = None) -> dict:
    """Return the simulator agent card."""
    url = base_url or BASE_URL

    card = AgentCard(
        name="simulator-agent",
        description="World Cup 2026 match simulator using AI-generated code in Azure Container Apps Sandboxes",
        url=f"{url}/a2a",
        version="2.0.0",
        protocolVersion="0.3.0",
        preferredTransport="JSONRPC",
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text/plain", "application/json"],
        defaultOutputModes=["application/json"],
        skills=[
            AgentSkill(
                id="simulate-match",
                name="Simulate World Cup Match",
                description="Simulates a Mexico vs Czechia World Cup 2026 match based on user prompt, using web research and AI-generated code in sandboxes",
                tags=["soccer", "world-cup", "simulation", "sandboxes", "ai-code-gen"],
                inputModes=["text/plain"],
                outputModes=["application/json"],
            )
        ],
    )

    if A2A_AUTH_ENABLED:
        card.securitySchemes = {
            "BearerAuth": AuthScheme(type="http", scheme="bearer"),
            "ApiKeyAuth": AuthScheme(type="apiKey", **{"in": "header"}, name="X-API-Key"),
        }
        card.security = [{"BearerAuth": []}, {"ApiKeyAuth": []}]

    return card.model_dump(by_alias=True, exclude_none=True)


def make_task_response(task_id: str, result: dict, request_id: str | int | None = None) -> dict:
    """Format a simulation result as an A2A JSON-RPC response."""
    response = JsonRpcResponse(
        result=TaskResult(
            id=task_id,
            status=TaskStatus(state="completed"),
            artifacts=[TaskArtifact(parts=[MessagePart(kind="data", data=result)])],
        ),
        id=request_id,
    )
    return response.model_dump(exclude_none=True)


def make_error_response(code: int, message: str, request_id: str | int | None = None) -> dict:
    """Format an error as an A2A JSON-RPC response."""
    from .a2a_models import JsonRpcError
    response = JsonRpcResponse(
        error=JsonRpcError(code=code, message=message),
        id=request_id,
    )
    return response.model_dump(exclude_none=True)
