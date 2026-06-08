"""Tests for A2A Pydantic models — serialization, aliasing, field behavior."""

import pytest
from a2a_models import (
    AgentCard, AgentSkill, AgentCapabilities,
    AuthScheme, MessagePart, JsonRpcRequest, JsonRpcResponse,
    TaskResult, TaskStatus, TaskArtifact, JsonRpcError,
)


def _minimal_card(**overrides):
    defaults = dict(
        name="test-agent", description="desc", url="http://localhost/a2a",
        version="1.0", protocolVersion="0.3", preferredTransport="jsonrpc",
        capabilities=AgentCapabilities(), defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        skills=[AgentSkill(id="s1", name="Skill", description="d")],
    )
    defaults.update(overrides)
    return AgentCard(**defaults)


def test_agent_card_roundtrip():
    card = _minimal_card()
    data = card.model_dump(by_alias=True, exclude_none=True)
    assert data["name"] == "test-agent"
    assert "securitySchemes" not in data  # None fields excluded
    restored = AgentCard(**data)
    assert restored.name == card.name


def test_auth_scheme_in_alias():
    """The Python field `in_` must serialize to JSON key `in`."""
    scheme = AuthScheme(**{"type": "apiKey", "in": "header", "name": "X-API-Key"})
    data = scheme.model_dump(by_alias=True, exclude_none=True)
    assert "in" in data and "in_" not in data
    assert data["in"] == "header"


def test_auth_scheme_from_json_key():
    """Constructing from JSON key `in` should work (alias population)."""
    scheme = AuthScheme.model_validate({"type": "apiKey", "in": "header", "name": "X-API-Key"})
    assert scheme.in_ == "header"


def test_message_part_text_only():
    part = MessagePart(kind="text", text="hello")
    data = part.model_dump(by_alias=True, exclude_none=True)
    assert data == {"kind": "text", "text": "hello"}


def test_message_part_data_only():
    part = MessagePart(kind="data", data={"key": "val"})
    data = part.model_dump(by_alias=True, exclude_none=True)
    assert "text" not in data
    assert data["data"] == {"key": "val"}


def test_jsonrpc_request_defaults():
    req = JsonRpcRequest(method="tasks/send", id="r1")
    assert req.jsonrpc == "2.0"
    data = req.model_dump(by_alias=True, exclude_none=True)
    assert data["method"] == "tasks/send"


def test_jsonrpc_response_success():
    resp = JsonRpcResponse(
        result=TaskResult(
            id="t1", status=TaskStatus(state="completed"),
            artifacts=[TaskArtifact(parts=[MessagePart(kind="text", text="done")])],
        ),
        id="r1",
    )
    data = resp.model_dump(by_alias=True, exclude_none=True)
    assert data["result"]["status"]["state"] == "completed"
    assert "error" not in data


def test_jsonrpc_response_error():
    resp = JsonRpcResponse(error=JsonRpcError(code=-32601, message="Method not found"), id="r1")
    data = resp.model_dump(by_alias=True, exclude_none=True)
    assert data["error"]["code"] == -32601
    assert "result" not in data
