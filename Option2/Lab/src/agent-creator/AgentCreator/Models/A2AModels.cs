using System.ComponentModel.DataAnnotations;

namespace AgentCreator.Models;

// Agent Card models
public record AgentCard
{
    [Required] public required string Name { get; init; }
    [Required] public required string Description { get; init; }
    [Required, Url] public required string Url { get; init; }
    [Required] public required string Version { get; init; }
    [Required] public required string ProtocolVersion { get; init; }
    public string? PreferredTransport { get; init; }
    public AgentInterface[]? SupportedInterfaces { get; init; }
    public AgentCapabilities? Capabilities { get; init; }
    public string[]? DefaultInputModes { get; init; }
    public string[]? DefaultOutputModes { get; init; }
    public AgentSkill[]? Skills { get; init; }
    public Dictionary<string, AuthScheme>? SecuritySchemes { get; init; }
    public List<Dictionary<string, string[]>>? Security { get; init; }
}

public record AgentInterface
{
    [Required, Url] public required string Url { get; init; }
    [Required] public required string ProtocolBinding { get; init; }
    [Required] public required string ProtocolVersion { get; init; }
}

public record AgentSkill
{
    [Required] public required string Id { get; init; }
    [Required] public required string Name { get; init; }
    public string? Description { get; init; }
    public string[]? Tags { get; init; }
    public string[]? InputModes { get; init; }
    public string[]? OutputModes { get; init; }
}

public record AgentCapabilities
{
    public bool Streaming { get; init; }
    public bool PushNotifications { get; init; }
}

public record AuthScheme
{
    [Required] public required string Type { get; init; }
    public string? Scheme { get; init; }
    public string? BearerFormat { get; init; }
    public string? In { get; init; }
    public string? Name { get; init; }
}

// JSON-RPC models
public record JsonRpcRequest<T>
{
    [Required] public required string Jsonrpc { get; init; }
    [Required] public required string Method { get; init; }
    public T? Params { get; init; }
    public object? Id { get; init; }
}

public record JsonRpcResponse<T>
{
    [Required] public required string Jsonrpc { get; init; }
    public T? Result { get; init; }
    public JsonRpcError? Error { get; init; }
    public object? Id { get; init; }
}

public record JsonRpcError
{
    [Required] public required int Code { get; init; }
    [Required] public required string Message { get; init; }
    public object? Data { get; init; }
}

// A2A Message models
public record A2ATaskParams
{
    [Required] public required string Id { get; init; }
    [Required] public required A2AMessage Message { get; init; }
}

public record A2AMessage
{
    [Required] public required MessagePart[] Parts { get; init; }
}

public record MessagePart
{
    public string? Kind { get; init; }
    public string? Type { get; init; }
    public string? Text { get; init; }
    public object? Data { get; init; }
    // Prefer "kind" (A2A v1.0) but fall back to "type" for backward compat
    public string EffectiveKind => Kind ?? Type ?? "text";
}

public record TaskResult
{
    [Required] public required string Id { get; init; }
    public TaskStatus? Status { get; init; }
    public TaskArtifact[]? Artifacts { get; init; }
}

public record TaskStatus
{
    [Required] public required string State { get; init; }
}

public record TaskArtifact
{
    [Required] public required MessagePart[] Parts { get; init; }
}
