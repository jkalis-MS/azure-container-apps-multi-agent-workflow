"""Integration tests for the Podcaster Agent FastAPI app (A2A protocol)."""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_sample_brief() -> dict:
    return json.loads((FIXTURES_DIR / "sample_brief.json").read_text())


@pytest.fixture
def sample_brief():
    return _load_sample_brief()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "healthy", "agent": "podcaster-agent"}


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------

class TestAgentCard:
    @pytest.mark.asyncio
    async def test_agent_card_endpoint(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/.well-known/agent.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "podcaster-agent"

    @pytest.mark.asyncio
    async def test_agent_card_structure(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/.well-known/agent.json")

        data = resp.json()
        assert "name" in data
        assert "url" in data
        assert "skills" in data
        assert len(data["skills"]) > 0
        skill = data["skills"][0]
        assert skill["id"] == "create-podcast"
        assert "inputModes" in skill
        assert "outputModes" in skill


# ---------------------------------------------------------------------------
# A2A Protocol
# ---------------------------------------------------------------------------

class TestA2ARejectsInvalidMethod:
    @pytest.mark.asyncio
    async def test_a2a_rejects_invalid_method(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/a2a", json={
                "jsonrpc": "2.0",
                "method": "tasks/cancel",
                "params": {"id": "t1"},
                "id": "req-1",
            })

        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601
        assert "Method not found" in data["error"]["message"]


class TestA2AAcceptsValidBrief:
    @pytest.mark.asyncio
    @patch("main.generate_podcast", new_callable=AsyncMock)
    async def test_a2a_accepts_valid_brief(self, mock_generate, sample_brief):
        mock_generate.return_value = {
            "podcast": {
                "topic": "Azure Container Apps",
                "audio_url": "https://blob.example.com/podcast.mp3",
                "duration_seconds": 120.0,
                "turn_count": 9,
                "chapters": [],
                "transcript": [],
            }
        }

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/a2a", json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "id": "test-task-1",
                    "message": {
                        "parts": [{"kind": "data", "data": sample_brief}],
                    },
                },
                "id": "req-1",
            })

        assert resp.status_code == 202
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "accepted"
        assert "poll_url" in data

    @pytest.mark.asyncio
    async def test_a2a_rejects_empty_message(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/a2a", json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {"id": "t2", "message": {"parts": []}},
                "id": "req-2",
            })

        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32602


class TestA2ATextBrief:
    """A2A endpoint should also accept a plain text brief."""

    @pytest.mark.asyncio
    @patch("main.generate_podcast", new_callable=AsyncMock)
    async def test_a2a_accepts_text_brief(self, mock_generate):
        mock_generate.return_value = {"podcast": {"topic": "test", "audio_url": None}}

        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/a2a", json={
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "id": "text-task",
                    "message": {
                        "parts": [{"kind": "text", "text": "Tell me about Kubernetes"}],
                    },
                },
                "id": "req-3",
            })

        assert resp.status_code == 202


class TestTaskPolling:
    """GET /tasks/{id} should return task status."""

    @pytest.mark.asyncio
    async def test_poll_unknown_task_returns_404(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/tasks/nonexistent-id")

        assert resp.status_code == 404
