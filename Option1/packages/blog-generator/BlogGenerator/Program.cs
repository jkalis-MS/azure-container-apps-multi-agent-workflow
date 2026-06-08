using System.ClientModel;
using System.Text.Json;
using System.Text.Json.Serialization;
using Azure.AI.OpenAI;
using BlogGenerator;
using BlogGenerator.Models;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using Azure.Monitor.OpenTelemetry.Exporter;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddSingleton<BlogWriterAgent>();
builder.Services.AddCors(options =>
    options.AddDefaultPolicy(policy =>
        policy.AllowAnyOrigin().AllowAnyMethod().AllowAnyHeader()));

// OpenTelemetry
var otelServiceName = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME") ?? "blog-agent";
var appInsightsConnStr = Environment.GetEnvironmentVariable("APPLICATIONINSIGHTS_CONNECTION_STRING");
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r.AddService(serviceName: otelServiceName))
    .WithTracing(tracing =>
    {
        tracing
            .AddAspNetCoreInstrumentation()
            .AddHttpClientInstrumentation()
            .AddSource("blog-agent");
        if (!string.IsNullOrEmpty(appInsightsConnStr))
            tracing.AddAzureMonitorTraceExporter(o => o.ConnectionString = appInsightsConnStr);
        else
            tracing.AddOtlpExporter();
    });

var app = builder.Build();
app.UseCors();

var jsonOptions = new JsonSerializerOptions
{
    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    PropertyNameCaseInsensitive = true
};

// ---------------------------------------------------------------------------
// A2A Agent Card
// ---------------------------------------------------------------------------
app.MapGet("/.well-known/agent.json", (HttpContext ctx) =>
{
    var scheme = ctx.Request.Headers["X-Forwarded-Proto"].FirstOrDefault() ?? ctx.Request.Scheme;
    var baseUrl = $"{scheme}://{ctx.Request.Host}";
    return Results.Json(A2ACard.ForBaseUrl(baseUrl), jsonOptions);
});

app.MapGet("/.well-known/agent-card.json", (HttpContext ctx) =>
{
    var scheme = ctx.Request.Headers["X-Forwarded-Proto"].FirstOrDefault() ?? ctx.Request.Scheme;
    var baseUrl = $"{scheme}://{ctx.Request.Host}";
    return Results.Json(A2ACard.ForBaseUrl(baseUrl), jsonOptions);
});

// ---------------------------------------------------------------------------
// A2A JSON-RPC endpoint
// ---------------------------------------------------------------------------
app.MapPost("/a2a", async (HttpContext ctx, BlogWriterAgent agent) =>
{
    JsonRpcRequest? request;
    try
    {
        request = await ctx.Request.ReadFromJsonAsync<JsonRpcRequest>(jsonOptions);
    }
    catch
    {
        return Results.Json(new { jsonrpc = "2.0", error = new { code = -32700, message = "Parse error" }, id = (string?)null }, jsonOptions, statusCode: 400);
    }

    if (request == null)
        return Results.Json(new { jsonrpc = "2.0", error = new { code = -32600, message = "Invalid Request" }, id = (string?)null }, jsonOptions, statusCode: 400);

    if (request.Method != "tasks/send" && request.Method != "SendMessage" && request.Method != "message/send")
        return Results.Json(new { jsonrpc = "2.0", error = new { code = -32601, message = "Method not found" }, id = request.Id }, jsonOptions, statusCode: 400);

    var taskParams = request.Params;
    if (taskParams == null)
        return Results.Json(new { jsonrpc = "2.0", error = new { code = -32602, message = "Invalid params" }, id = request.Id }, jsonOptions, statusCode: 400);

    // Extract simulation data from message parts
    JsonElement? simulationData = null;
    if (taskParams.Message?.Parts != null)
    {
        foreach (var part in taskParams.Message.Parts)
        {
            if ((part.Kind == "data" || part.Type == "data") && part.Data != null)
            {
                simulationData = part.Data;
                break;
            }
        }
    }

    if (simulationData == null)
        return Results.Json(new { jsonrpc = "2.0", error = new { code = -32602, message = "No simulation data provided" }, id = request.Id }, jsonOptions, statusCode: 400);

    // Extract the simulation object
    JsonElement simulation;
    if (simulationData.Value.TryGetProperty("simulation", out var sim))
        simulation = sim;
    else
        simulation = simulationData.Value;

    var html = await agent.GenerateBlogAsync(simulation);

    var result = new
    {
        jsonrpc = "2.0",
        result = new
        {
            id = taskParams.Id ?? "unknown",
            status = new { state = "completed" },
            artifacts = new[]
            {
                new
                {
                    parts = new object[]
                    {
                        new { kind = "data", data = new { html } }
                    }
                }
            }
        },
        id = request.Id
    };

    return Results.Json(result, jsonOptions);
});

// ---------------------------------------------------------------------------
// Legacy endpoint (backward-compat with orchestrator)
// ---------------------------------------------------------------------------
app.MapPost("/generate", async (HttpContext ctx, BlogWriterAgent agent) =>
{
    JsonElement body;
    try { body = await ctx.Request.ReadFromJsonAsync<JsonElement>(); }
    catch { return Results.Json(new { error = "Invalid JSON" }, statusCode: 400); }

    JsonElement simulation;
    if (body.TryGetProperty("simulation", out var s))
        simulation = s;
    else
        return Results.Json(new { error = "Missing simulation data" }, statusCode: 400);

    var html = await agent.GenerateBlogAsync(simulation);
    return Results.Json(new { html });
});

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------
app.MapGet("/health", () => Results.Json(new { status = "ok", service = "blog-generator", framework = "dotnet" }));

app.Run();
