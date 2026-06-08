"""Tests for A2A auth middleware — verify_a2a_token with FastAPI TestClient."""

import importlib
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI, Depends


def _make_app(auth_enabled: str, auth_token: str = "test-secret"):
    """Build a tiny FastAPI app with fresh a2a_auth module loaded under given env."""
    import os
    os.environ["A2A_AUTH_ENABLED"] = auth_enabled
    os.environ["A2A_AUTH_TOKEN"] = auth_token

    import a2a_auth
    importlib.reload(a2a_auth)

    app = FastAPI()

    @app.post("/a2a")
    async def endpoint(_auth: None = Depends(a2a_auth.verify_a2a_token)):
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure env vars are cleaned and a2a_auth module state is reset after each test."""
    yield
    monkeypatch.delenv("A2A_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    # Reset module-level state so subsequent tests see auth disabled
    import a2a_auth
    importlib.reload(a2a_auth)


@pytest.mark.asyncio
async def test_auth_disabled_passes():
    app = _make_app("false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_enabled_correct_bearer():
    app = _make_app("true", "secret123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json={}, headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_enabled_correct_api_key():
    app = _make_app("true", "secret123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json={}, headers={"X-API-Key": "secret123"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_enabled_wrong_token():
    app = _make_app("true", "secret123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json={}, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_enabled_no_token():
    app = _make_app("true", "secret123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json={})
    assert resp.status_code == 401
