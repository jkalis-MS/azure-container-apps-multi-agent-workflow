using AgentCreator;
using AgentCreator.Models;
using System.Text.Json;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

// Load .env file (search current dir and up to Lab/)
LoadEnvFile();

var builder = WebApplication.CreateBuilder(args);

// Register the Content Creator Agent
builder.Services.AddSingleton<ContentCreatorAgent>();
builder.Services.AddSingleton<ContentFactoryWorkflow>();
builder.Services.AddSingleton<A2AAuthFilter>();

builder.Services.AddCors(options =>
    options.AddDefaultPolicy(policy =>
        policy.AllowAnyOrigin().AllowAnyMethod().AllowAnyHeader()));

// OpenTelemetry tracing (including gen_ai.* spans for App Insights Agents blade)
// Sources per https://learn.microsoft.com/en-us/agent-framework/agents/observability
var otelServiceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME");
if (string.IsNullOrEmpty(otelServiceName)) otelServiceName = "creator-agent";
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r.AddService(serviceName: otelServiceName))
    .WithTracing(tracing => tracing
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddSource("creator-agent")
        .AddSource("content-agent")
        .AddSource("content-factory-workflow")
        .AddSource("*Microsoft.Extensions.AI")
        .AddSource("*Microsoft.Agents.AI")
        .AddSource("*Microsoft.Extensions.Agents*")
        .AddOtlpExporter());

var app = builder.Build();
app.UseCors();

// A2A Agent Card (serve at both v0.3 and v1.0 well-known paths)
var cardJsonOptions = new JsonSerializerOptions
{
    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
};
app.MapGet("/.well-known/agent.json", (HttpContext ctx) =>
{
    var scheme = ctx.Request.Headers["X-Forwarded-Proto"].FirstOrDefault() ?? ctx.Request.Scheme;
    var baseUrl = $"{scheme}://{ctx.Request.Host}";
    return Results.Json(A2ACard.ForBaseUrl(baseUrl), cardJsonOptions);
});
app.MapGet("/.well-known/agent-card.json", (HttpContext ctx) =>
{
    var scheme = ctx.Request.Headers["X-Forwarded-Proto"].FirstOrDefault() ?? ctx.Request.Scheme;
    var baseUrl = $"{scheme}://{ctx.Request.Host}";
    return Results.Json(A2ACard.ForBaseUrl(baseUrl), cardJsonOptions);
});

// A2A JSON-RPC endpoint
app.MapPost("/a2a", async (HttpContext ctx, ContentFactoryWorkflow workflow) =>
{
    var jsonOptions = new JsonSerializerOptions
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    JsonRpcRequest<A2ATaskParams>? request;
    try
    {
        request = await ctx.Request.ReadFromJsonAsync<JsonRpcRequest<A2ATaskParams>>(jsonOptions);
    }
    catch
    {
        ctx.Response.StatusCode = 400;
        return Results.Json(new JsonRpcResponse<object>
        {
            Jsonrpc = "2.0",
            Error = new JsonRpcError { Code = -32700, Message = "Parse error" },
            Id = null
        }, jsonOptions);
    }

    if (request == null)
    {
        ctx.Response.StatusCode = 400;
        return Results.Json(new JsonRpcResponse<object>
        {
            Jsonrpc = "2.0",
            Error = new JsonRpcError { Code = -32600, Message = "Invalid Request" },
            Id = null
        }, jsonOptions);
    }

    var method = request.Method;
    if (method != "tasks/send" && method != "SendMessage" && method != "message/send")
    {
        ctx.Response.StatusCode = 400;
        return Results.Json(new JsonRpcResponse<object>
        {
            Jsonrpc = "2.0",
            Error = new JsonRpcError { Code = -32601, Message = "Method not found" },
            Id = request.Id
        }, jsonOptions);
    }

    var taskParams = request.Params;
    if (taskParams == null)
    {
        ctx.Response.StatusCode = 400;
        return Results.Json(new JsonRpcResponse<object>
        {
            Jsonrpc = "2.0",
            Error = new JsonRpcError { Code = -32602, Message = "Invalid params" },
            Id = request.Id
        }, jsonOptions);
    }

    var taskId = taskParams.Id ?? "unknown";
    var message = taskParams.Message;

    // Extract research brief from message parts
    JsonElement? dataPayload = null;
    foreach (var part in message.Parts)
    {
        if (part.EffectiveKind == "data" && part.Data != null)
        {
            var json = JsonSerializer.Serialize(part.Data);
            dataPayload = JsonDocument.Parse(json).RootElement;
            break;
        }
    }

    if (dataPayload == null)
    {
        ctx.Response.StatusCode = 400;
        return Results.Json(new JsonRpcResponse<object>
        {
            Jsonrpc = "2.0",
            Error = new JsonRpcError { Code = -32602, Message = "No data payload" },
            Id = request.Id
        }, jsonOptions);
    }

    // Accept { "research_brief": { ... } } or the brief directly
    JsonElement brief;
    if (dataPayload.Value.TryGetProperty("research_brief", out var nested))
        brief = nested;
    else
        brief = dataPayload.Value;

    var result = await workflow.RunAsync(brief);

    return Results.Json(new JsonRpcResponse<TaskResult>
    {
        Jsonrpc = "2.0",
        Result = new TaskResult
        {
            Id = taskId,
            Status = new AgentCreator.Models.TaskStatus { State = "completed" },
            Artifacts =
            [
                new TaskArtifact
                {
                    Parts = [new MessagePart { Kind = "data", Data = result }]
                }
            ]
        },
        Id = request.Id
    }, jsonOptions);
}).AddEndpointFilter<A2AAuthFilter>();

// Workflow pipeline endpoint (server-side orchestration)
app.MapPost("/pipeline", async (HttpContext ctx, ContentFactoryWorkflow workflow) =>
{
    JsonElement body;
    try
    {
        body = await ctx.Request.ReadFromJsonAsync<JsonElement>();
    }
    catch
    {
        ctx.Response.StatusCode = 400;
        return Results.Json(new { error = "Invalid JSON body" });
    }

    // Accept { "research_brief": { ... } } or the brief directly
    JsonElement brief;
    if (body.TryGetProperty("research_brief", out var nested))
        brief = nested;
    else
        brief = body;

    var result = await workflow.RunAsync(brief);
    return Results.Json(result);
});

// Health check
app.MapGet("/health", () => Results.Json(new { status = "healthy", agent = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME") ?? "creator-agent" }));

app.Run();

/// <summary>
/// Load a .env file into environment variables for local development.
/// Walks up from the current directory looking for a .env file.
/// Does NOT overwrite variables that are already set (e.g. from Docker/CI).
/// </summary>
static void LoadEnvFile()
{
    var dir = Directory.GetCurrentDirectory();
    string? envPath = null;

    // Walk up to find .env (handles running from AgentCreator/ or Lab/ or repo root)
    for (var d = new DirectoryInfo(dir); d != null; d = d.Parent)
    {
        var candidate = Path.Combine(d.FullName, ".env");
        if (File.Exists(candidate))
        {
            envPath = candidate;
            break;
        }
    }

    if (envPath == null)
    {
        Console.WriteLine("[ContentCreator] No .env file found (walked up from " + dir + ")");
        return;
    }

    Console.WriteLine($"[ContentCreator] Loading env vars from {envPath}");
    foreach (var line in File.ReadAllLines(envPath))
    {
        var trimmed = line.Trim();
        if (string.IsNullOrEmpty(trimmed) || trimmed.StartsWith('#'))
            continue;

        var eqIndex = trimmed.IndexOf('=');
        if (eqIndex <= 0) continue;

        var key = trimmed[..eqIndex].Trim();
        var value = trimmed[(eqIndex + 1)..].Trim();

        // Don't overwrite already-set variables (Docker, CI, etc.)
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable(key)))
        {
            Environment.SetEnvironmentVariable(key, value);
        }
    }
}

// Make the implicit Program class public for test access
public partial class Program { }
