using System.Text.Json;
using Microsoft.Agents.AI.Workflows;

namespace AgentCreator.Executors;

/// <summary>
/// Entry executor that receives a research brief (JsonElement) and passes it
/// through the workflow. Validates that the brief contains a topic.
/// </summary>
internal sealed class BriefInputExecutor() : Executor<JsonElement, JsonElement>("BriefInput")
{
    public override ValueTask<JsonElement> HandleAsync(
        JsonElement brief, IWorkflowContext context, CancellationToken cancellationToken = default)
    {
        // Validate the brief has a topic
        var hasTopic = brief.TryGetProperty("topic", out var t)
            ? !string.IsNullOrWhiteSpace(t.GetString())
            : false;

        if (!hasTopic)
            throw new InvalidOperationException("Research brief must contain a 'topic' field");

        Console.WriteLine($"[Workflow] BriefInput received topic: {(brief.TryGetProperty("topic", out var topic) ? topic.GetString() : "unknown")}");
        return ValueTask.FromResult(brief);
    }
}
