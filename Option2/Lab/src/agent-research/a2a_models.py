"""Pydantic v2 models for A2A protocol — minimal implementation."""

from pydantic import BaseModel, Field


class AuthScheme(BaseModel):
    type: str
    scheme: str | None = None
    name: str | None = None
    in_: str | None = Field(None, alias="in")


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    inputModes: list[str] = Field(default_factory=list)
    outputModes: list[str] = Field(default_factory=list)


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False


class SupportedInterface(BaseModel):
    url: str
    protocolBinding: str
    protocolVersion: str


class AgentCard(BaseModel):
    name: str
    description: str
    url: str
    version: str
    protocolVersion: str
    preferredTransport: str
    supportedInterfaces: list[SupportedInterface] | None = None
    capabilities: AgentCapabilities
    defaultInputModes: list[str]
    defaultOutputModes: list[str]
    skills: list[AgentSkill]
    securitySchemes: dict[str, AuthScheme] | None = None
    security: list[dict[str, list]] | None = None


class MessagePart(BaseModel):
    kind: str
    text: str | None = None
    data: dict | None = None


class A2AMessage(BaseModel):
    parts: list[MessagePart]


class JsonRpcError(BaseModel):
    code: int
    message: str


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict | None = None
    id: str | int | None = None


class TaskStatus(BaseModel):
    state: str


class TaskArtifact(BaseModel):
    parts: list[MessagePart]


class TaskResult(BaseModel):
    id: str
    status: TaskStatus
    artifacts: list[TaskArtifact] | None = None


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    result: TaskResult | None = None
    error: JsonRpcError | None = None
    id: str | int | None = None
