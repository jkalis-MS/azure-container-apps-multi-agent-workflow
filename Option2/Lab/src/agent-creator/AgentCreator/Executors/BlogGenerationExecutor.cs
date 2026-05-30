using System.Text;
using System.Text.Json;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;

namespace AgentCreator.Executors;

/// <summary>
/// Executor that generates a blog article using its own ChatClientAgent and AgentSession.
/// Follows the CustomAgentExecutors pattern from the MAF samples:
/// executor owns the agent, manages its session, and calls RunAsync(prompt, session) directly.
/// </summary>
internal sealed class BlogGenerationExecutor : Executor<JsonElement, ContentCreatorAgent.BlogResult>
{
    private readonly AIAgent _agent;
    private AgentSession? _session;

    public BlogGenerationExecutor(IChatClient chatClient) : base("BlogGeneration")
    {
        // Instructions are generic here; the user prompt injects audience-specific guidance
        _agent = new ChatClientAgent(
            chatClient,
            name: "creator-agent",
            instructions: """
                You are an expert technical writer creating original content.
                You synthesize research materials into brand-new, well-structured articles written in your own voice.
                Tailor depth, terminology, and examples for the target audience specified in each request.
                CRITICAL RULES:
                - NEVER copy or repeat source text verbatim. Always rewrite ideas in your own words.
                - DO NOT list sources one by one with "According to [source]..." patterns.
                - Synthesize information across sources into a coherent narrative.
                - Add your own analysis, practical guidance, and insights appropriate for the target audience.
                - Use source URLs only as inline reference links, not as the structure of the article.
                """)
            .AsBuilder()
            .UseOpenTelemetry(sourceName: "creator-agent", configure: c => c.EnableSensitiveData = true)
            .Build();
    }

    public override async ValueTask<ContentCreatorAgent.BlogResult> HandleAsync(
        JsonElement brief, IWorkflowContext context, CancellationToken cancellationToken = default)
    {
        Console.WriteLine("[Workflow] BlogGeneration starting...");
        var parsed = ContentCreatorAgent.ParseBrief(brief);

        // Store parsed fields in workflow state for downstream executors
        await context.QueueStateUpdateAsync("topic", parsed.Topic, cancellationToken);
        await context.QueueStateUpdateAsync("sourceCount", parsed.Sources.Count, cancellationToken);

        string blogMarkdown;
        try
        {
            _session ??= await _agent.CreateSessionAsync(cancellationToken);
            var userPrompt = BuildBlogPrompt(parsed);
            var response = await ContentCreatorAgent.AgentRunWithRetry(
                _agent, userPrompt, _session, cancellationToken);
            blogMarkdown = response.Text
                ?? throw new InvalidOperationException("LLM returned empty response");
            Console.WriteLine($"[BlogWriter] LLM generated {blogMarkdown.Split(' ').Length} words");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[BlogWriter] *** LLM FAILED — falling back to template: {ex.Message}");
            blogMarkdown = ContentCreatorAgent.CompileFallback(parsed.Topic, parsed.Summary, parsed.Sources);
        }

        var result = new ContentCreatorAgent.BlogResult
        {
            Markdown = blogMarkdown,
            WordCount = blogMarkdown.Split(' ').Length,
            SourcesUsed = parsed.Sources.Count
        };

        Console.WriteLine($"[Workflow] BlogGeneration complete ({result.WordCount} words)");
        return result;
    }

    private static string BuildBlogPrompt(ContentCreatorAgent.ParsedBrief parsed)
    {
        var sourceMaterial = new StringBuilder();
        foreach (var src in parsed.Sources.Take(25))
        {
            sourceMaterial.AppendLine($"--- SOURCE: {src.Title} ({src.Type}) ---");
            sourceMaterial.AppendLine($"URL: {src.Url}");
            sourceMaterial.AppendLine(src.Content);
            sourceMaterial.AppendLine();
        }

        return $"""
            Write a comprehensive, well-structured article about: {parsed.Topic}

            Target audience: {parsed.Audience}
            Tailor depth, terminology, and examples for this audience.

            Use the following source materials as background research. Synthesize the information
            into a coherent, ORIGINAL article — do NOT just summarize or list the sources.
            Write as if you are a knowledgeable advocate explaining this topic to {parsed.Audience}.

            Research summary: {parsed.Summary}

            Source materials:
            {sourceMaterial}

            Requirements:
            - Write in markdown format starting with # title, then ## sections
            - Start with an engaging introduction that hooks the reader
            - Organize by theme/concept, NOT by source
            - Include practical examples and code snippets where relevant
            - Weave in references as markdown links [text](url) naturally in context
            - End with a "Resources" section listing all source URLs
            - Target 800-1200 words
            - Professional but accessible tone tailored for {parsed.Audience}
            """;
    }
}
