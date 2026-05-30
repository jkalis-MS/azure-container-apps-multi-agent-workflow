using System.Diagnostics;
using System.Text.Json;
using AgentCreator.Executors;
using Microsoft.Agents.AI.Workflows;

namespace AgentCreator;

/// <summary>
/// Server-side orchestration for the Content Factory pipeline using
/// Microsoft Agent Framework Workflows.
///
/// Pipeline: BriefInput → BlogGeneration → SocialGeneration → Output
///
/// Each LLM call is a separate executor with its own OTel span,
/// all reporting under the unified "creator-agent" identity.
/// </summary>
public class ContentFactoryWorkflow
{
    private static readonly ActivitySource _activitySource = new("content-agent");
    private readonly ContentCreatorAgent _agent;

    public ContentFactoryWorkflow(ContentCreatorAgent agent)
    {
        _agent = agent;
    }

    /// <summary>
    /// Build and execute the content creation workflow for a given research brief.
    /// </summary>
    /// <param name="researchBrief">The research brief JSON from the Research Agent</param>
    /// <returns>The content package (blog post + social media)</returns>
    public async Task<object> RunAsync(JsonElement researchBrief)
    {
        using var activity = _activitySource.StartActivity(
            "workflow content-factory-pipeline", ActivityKind.Internal);
        activity?.SetTag("gen_ai.operation.name", "workflow");
        activity?.SetTag("gen_ai.agent.name", "content-factory-pipeline");

        // If Azure OpenAI is not configured, use template-based fallback
        if (_agent.ChatClient is null)
        {
            Console.WriteLine("[Workflow] No Azure OpenAI configured — using template fallback");
            var parsed = ContentCreatorAgent.ParseBrief(researchBrief);
            var blogMarkdown = ContentCreatorAgent.CompileFallback(parsed.Topic, parsed.Summary, parsed.Sources);
            var blog = new ContentCreatorAgent.BlogResult { Markdown = blogMarkdown, WordCount = blogMarkdown.Split(' ').Length, SourcesUsed = parsed.Sources.Count };
            var social = new ContentCreatorAgent.SocialResult
            {
                LinkedIn = $"Deep dive into {parsed.Topic}! #Azure #CloudNative #AI",
                Tweets = [$"Thread: {parsed.Topic} (1/3)", $"Key takeaway from {parsed.Topic} (2/3)", "Full article linked below! (3/3)"]
            };
            return ContentCreatorAgent.PackageResult(parsed.Topic, blog, social, parsed.Sources.Count);
        }

        // Create executor instances — each LLM call is a separate step with its own agent + session
        var chatClient = _agent.ChatClient;

        var briefInput = new BriefInputExecutor();
        var blogGeneration = new BlogGenerationExecutor(chatClient);
        var socialGeneration = new SocialGenerationExecutor(chatClient);
        var output = new OutputExecutor();

        // Build the workflow graph: brief → blog-writer → social-writer → output
        var workflowBuilder = new WorkflowBuilder(briefInput)
            .AddEdge(briefInput, blogGeneration)
            .AddEdge(blogGeneration, socialGeneration)
            .AddEdge(socialGeneration, output)
            .WithOutputFrom(output);

        var workflow = workflowBuilder.Build();

        // Execute the workflow
        Console.WriteLine("[Workflow] Starting content factory pipeline...");
        await using var run = await InProcessExecution.RunStreamingAsync(workflow, researchBrief);

        object? result = null;
        await foreach (var evt in run.WatchStreamAsync())
        {
            if (evt is WorkflowOutputEvent outputEvent)
            {
                result = outputEvent.Data;
            }
        }

        // WorkflowOutputEvent may not fire in all MAF versions; use executor's captured result
        result ??= output.LastResult;

        if (result == null)
            throw new InvalidOperationException("Workflow completed without producing output");

        Console.WriteLine("[Workflow] Content factory pipeline complete");
        return result;
    }
}
