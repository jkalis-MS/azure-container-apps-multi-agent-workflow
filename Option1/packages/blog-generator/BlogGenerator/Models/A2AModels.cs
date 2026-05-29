using System.Text.Json;
using System.Text.Json.Serialization;

namespace BlogGenerator.Models;

public class JsonRpcRequest
{
    [JsonPropertyName("jsonrpc")]
    public string Jsonrpc { get; set; } = "2.0";

    [JsonPropertyName("id")]
    public string? Id { get; set; }

    [JsonPropertyName("method")]
    public string Method { get; set; } = "";

    [JsonPropertyName("params")]
    public A2ATaskParams? Params { get; set; }
}

public class A2ATaskParams
{
    [JsonPropertyName("id")]
    public string? Id { get; set; }

    [JsonPropertyName("message")]
    public A2AMessage? Message { get; set; }
}

public class A2AMessage
{
    [JsonPropertyName("role")]
    public string? Role { get; set; }

    [JsonPropertyName("parts")]
    public List<MessagePart>? Parts { get; set; }
}

public class MessagePart
{
    [JsonPropertyName("kind")]
    public string? Kind { get; set; }

    [JsonPropertyName("type")]
    public string? Type { get; set; }

    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("data")]
    public JsonElement? Data { get; set; }
}
