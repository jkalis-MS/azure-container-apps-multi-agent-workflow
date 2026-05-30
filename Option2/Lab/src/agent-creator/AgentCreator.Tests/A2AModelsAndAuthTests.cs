using System.Net;
using System.Text.Json;
using AgentCreator.Models;
using Microsoft.AspNetCore.Mvc.Testing;

namespace AgentCreator.Tests;

public class A2AModelsAndAuthTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public A2AModelsAndAuthTests(WebApplicationFactory<Program> factory)
    {
        _client = factory.CreateClient();
    }

    // --- Model serialization ---

    [Fact]
    public void AgentCard_SerializesCamelCase()
    {
        var card = new AgentCard
        {
            Name = "test", Description = "d", Url = "http://localhost/a2a",
            Version = "1.0", ProtocolVersion = "0.3"
        };
        var opts = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
        var json = JsonSerializer.Serialize(card, opts);
        var doc = JsonDocument.Parse(json);
        Assert.True(doc.RootElement.TryGetProperty("protocolVersion", out _));
        Assert.False(doc.RootElement.TryGetProperty("ProtocolVersion", out _));
    }

    [Fact]
    public void JsonRpcError_SerializesCamelCase()
    {
        var err = new JsonRpcError { Code = -32601, Message = "Method not found" };
        var opts = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
        var json = JsonSerializer.Serialize(err, opts);
        Assert.Contains("\"code\":-32601", json);
        Assert.Contains("\"message\":\"Method not found\"", json);
    }

    [Fact]
    public void AuthScheme_IncludesInField()
    {
        var scheme = new AuthScheme { Type = "apiKey", In = "header", Name = "X-API-Key" };
        var opts = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull };
        var json = JsonSerializer.Serialize(scheme, opts);
        var doc = JsonDocument.Parse(json);
        Assert.Equal("header", doc.RootElement.GetProperty("in").GetString());
        Assert.False(doc.RootElement.TryGetProperty("scheme", out _)); // null excluded
    }

    // --- A2ACard.ForBaseUrl ---

    [Fact]
    public void A2ACard_ForBaseUrl_ReturnsValidCard()
    {
        // Auth disabled by default (no env var set)
        var card = A2ACard.ForBaseUrl("http://localhost:5000");
        Assert.Equal("content-creator-agent", card.Name);
        Assert.Equal("http://localhost:5000/a2a", card.Url);
        Assert.NotNull(card.Skills);
        Assert.True(card.Skills.Length > 0);
        Assert.Null(card.SecuritySchemes); // disabled → no schemes
    }

    // --- A2AAuthFilter ---

    [Fact]
    public async Task AuthFilter_DisabledPassesThrough()
    {
        // Default env has A2A_AUTH_ENABLED unset (=disabled) → requests pass
        var resp = await _client.PostAsync("/a2a",
            new StringContent("{\"jsonrpc\":\"2.0\",\"method\":\"tasks/send\",\"params\":{\"id\":\"t1\",\"message\":{\"parts\":[{\"type\":\"text\",\"text\":\"hi\"}]}},\"id\":\"r1\"}",
                System.Text.Encoding.UTF8, "application/json"));
        // Should not be 401 — auth filter should not block
        Assert.NotEqual(HttpStatusCode.Unauthorized, resp.StatusCode);
    }

    [Fact]
    public void AuthFilter_EnabledWithoutToken_Throws()
    {
        Environment.SetEnvironmentVariable("A2A_AUTH_ENABLED", "true");
        Environment.SetEnvironmentVariable("A2A_AUTH_TOKEN", "");
        try
        {
            Assert.Throws<InvalidOperationException>(() => new A2AAuthFilter());
        }
        finally
        {
            Environment.SetEnvironmentVariable("A2A_AUTH_ENABLED", null);
            Environment.SetEnvironmentVariable("A2A_AUTH_TOKEN", null);
        }
    }

    [Fact]
    public void AuthFilter_EnabledWithToken_Constructs()
    {
        Environment.SetEnvironmentVariable("A2A_AUTH_ENABLED", "true");
        Environment.SetEnvironmentVariable("A2A_AUTH_TOKEN", "my-secret");
        try
        {
            var filter = new A2AAuthFilter(); // should not throw
            Assert.NotNull(filter);
        }
        finally
        {
            Environment.SetEnvironmentVariable("A2A_AUTH_ENABLED", null);
            Environment.SetEnvironmentVariable("A2A_AUTH_TOKEN", null);
        }
    }
}
