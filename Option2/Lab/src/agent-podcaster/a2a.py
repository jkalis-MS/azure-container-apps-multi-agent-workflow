"""A2A protocol helpers -- Agent Card and JSON-RPC response formatting."""
import os
from a2a_models import (
    AgentCard, AgentSkill, AgentCapabilities, SupportedInterface,
    AuthScheme, JsonRpcResponse, TaskResult, TaskStatus, TaskArtifact, MessagePart
)
from a2a_auth import A2A_AUTH_ENABLED

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8003")


def get_agent_card(base_url: str | None = None) -> dict:
    """Return the agent card, optionally overriding the base URL."""
    url = base_url or BASE_URL
    
    card = AgentCard(
        name="podcaster-agent",
        description="Generates conversational multi-voice audio podcasts from research briefs",
        url=f"{url}/a2a",
        version="1.0.0",
        protocolVersion="0.3.0",
        preferredTransport="JSONRPC",
        supportedInterfaces=[],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text/plain", "application/json"],
        defaultOutputModes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id="create-podcast",
                name="Create Conversational Podcast",
                description="Transforms a research brief into a two-host audio podcast with real source references. Async: returns 202 + task ID, poll GET /tasks/{id}.",
                tags=["podcast", "audio", "tts", "content"],
                inputModes=["application/json"],
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


def make_task_response(task_id: str, result: dict, request_id: str | None = None) -> dict:
    """Format a podcast result as an A2A JSON-RPC response."""
    response = JsonRpcResponse(
        result=TaskResult(
            id=task_id,
            status=TaskStatus(state="completed"),
            artifacts=[TaskArtifact(parts=[MessagePart(kind="data", data=result)])],
        ),
        id=request_id,
    )
    return response.model_dump(exclude_none=True)
