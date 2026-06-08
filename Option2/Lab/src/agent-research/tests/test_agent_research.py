"""Tests for Agent 1 - Tech Research Agent."""

import importlib
import os
import pytest
import httpx
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_search_learn_returns_list():
    """search_learn should return a list of documentation results."""
    from tools.search_learn import search_learn
    results = await search_learn("Azure Container Apps")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_github_returns_list():
    """search_github_repos should return a list of repos."""
    from tools.search_github import search_github_repos
    results = await search_github_repos("Azure Container Apps")
    assert isinstance(results, list)


def test_agent_card_structure():
    """Agent card should have required A2A fields."""
    from a2a import get_agent_card
    card = get_agent_card()
    assert card["name"] == "research-agent"
    assert "url" in card
    assert "skills" in card
    assert len(card["skills"]) > 0
    assert card["skills"][0]["id"] == "research-topic"


def test_make_task_response_format():
    """A2A response should follow JSON-RPC format."""
    from a2a import make_task_response
    result = make_task_response("task-1", {"topic": "test", "summary": "ok"}, "req-1")
    assert result["jsonrpc"] == "2.0"
    assert result["result"]["id"] == "task-1"
    assert result["result"]["status"]["state"] == "completed"
    assert len(result["result"]["artifacts"]) > 0


@pytest.mark.asyncio
async def test_health_endpoint():
    """Health endpoint should return healthy status."""
    from main import app
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_agent_card_endpoint():
    """Agent card endpoint should return valid A2A card."""
    from main import app
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "research-agent"


@pytest.mark.asyncio
async def test_a2a_task_missing_topic():
    """A2A endpoint should reject tasks without a topic."""
    from main import app
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/a2a", json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "params": {"id": "t1", "message": {"parts": []}},
            "id": "r1",
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# A2A Card Validation
# ---------------------------------------------------------------------------

A2A_CARD_REQUIRED_FIELDS = [
    "name", "description", "url", "version", "protocolVersion",
    "preferredTransport", "capabilities", "defaultInputModes",
    "defaultOutputModes", "skills",
]


def test_agent_card_has_all_required_fields():
    """Agent card must contain every field required by the A2A spec."""
    from a2a import get_agent_card
    card = get_agent_card()
    for field in A2A_CARD_REQUIRED_FIELDS:
        assert field in card, f"Missing required field: {field}"


def test_agent_card_protocol_version():
    """protocolVersion must be 0.3.0."""
    from a2a import get_agent_card
    card = get_agent_card()
    assert card["protocolVersion"] == "0.3.0"


def test_agent_card_preferred_transport():
    """preferredTransport must be JSONRPC."""
    from a2a import get_agent_card
    card = get_agent_card()
    assert card["preferredTransport"] == "JSONRPC"


def test_agent_card_skills_non_empty():
    """skills array must have at least one entry."""
    from a2a import get_agent_card
    card = get_agent_card()
    assert len(card["skills"]) > 0


def test_agent_card_no_supported_interfaces():
    """Card must NOT contain the removed supportedInterfaces key."""
    from a2a import get_agent_card
    card = get_agent_card()
    assert "supportedInterfaces" not in card


def test_agent_card_security_when_auth_enabled():
    """securitySchemes must be present when A2A_AUTH_ENABLED=true."""
    os.environ["A2A_AUTH_ENABLED"] = "true"
    os.environ["A2A_AUTH_TOKEN"] = "test-secret"
    import a2a_auth
    importlib.reload(a2a_auth)
    import a2a
    importlib.reload(a2a)
    try:
        card = a2a.get_agent_card()
        assert "securitySchemes" in card
    finally:
        os.environ["A2A_AUTH_ENABLED"] = "false"
        importlib.reload(a2a_auth)
        importlib.reload(a2a)
        os.environ.pop("A2A_AUTH_ENABLED", None)
        os.environ.pop("A2A_AUTH_TOKEN", None)


def test_agent_card_no_security_when_auth_disabled():
    """securitySchemes must be absent when A2A_AUTH_ENABLED=false."""
    os.environ["A2A_AUTH_ENABLED"] = "false"
    import a2a_auth
    importlib.reload(a2a_auth)
    import a2a
    importlib.reload(a2a)
    try:
        card = a2a.get_agent_card()
        assert "securitySchemes" not in card
    finally:
        os.environ.pop("A2A_AUTH_ENABLED", None)


# ---------------------------------------------------------------------------
# JSON-RPC Parse Error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_json_returns_parse_error():
    """POST malformed JSON to /a2a must return 400 with -32700 parse error."""
    from main import app
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/a2a",
            content=b"{not valid json!!!",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["code"] == -32700
    assert "Parse error" in data["error"]["message"]
