using System.Diagnostics;
using Microsoft.Agents.AI.Workflows;

namespace AgentCreator.Executors;

/// <summary>
/// Terminal executor that assembles blog + social results into the final
/// content_package output. Reads topic and sourceCount from workflow state.
/// </summary>
internal sealed class OutputExecutor() : Executor<ContentCreatorAgent.ContentResults, object>("PipelineOutput")
{
    private static readonly ActivitySource _activitySource = new("content-agent");

    /// <summary>Last result produced by this executor, for retrieval after workflow completes.</summary>
    public object? LastResult { get; private set; }

    public override ValueTask<object> HandleAsync(
        ContentCreatorAgent.ContentResults contentResults, IWorkflowContext context, CancellationToken cancellationToken = default)
    {
        using var activity = _activitySource.StartActivity("pipeline_output", ActivityKind.Internal);
        activity?.SetTag("gen_ai.operation.name", "pipeline_output");

        var result = ContentCreatorAgent.PackageResult(
            contentResults.Topic, contentResults.Blog, contentResults.Social, contentResults.SourceCount);

        Console.WriteLine("[Workflow] Pipeline output assembled");
        LastResult = result;
        return ValueTask.FromResult(result);
    }
}
