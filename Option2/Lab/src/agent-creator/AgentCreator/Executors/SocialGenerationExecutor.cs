using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Workflows;
using Microsoft.Extensions.AI;

namespace AgentCreator.Executors;

/// <summary>
/// Executor that generates social media content using its own ChatClientAgent and AgentSession.
/// Follows the CustomAgentExecutors pattern from the MAF samples:
/// executor owns the agent, manages its session, and calls RunAsync(prompt, session) directly.
/// </summary>
internal sealed class SocialGenerationExecutor : Executor<ContentCreatorAgent.BlogResult, ContentCreatorAgent.ContentResults>
{
    private readonly AIAgent _agent;
    private AgentSession? _session;

    public SocialGenerationExecutor(IChatClient chatClient) : base("SocialGeneration")
    {
        _agent = new ChatClientAgent(
            chatClient,
            name: "creator-agent",
            instructions: "You are a developer advocate writing engaging social media content. Write original, insightful posts — never just summarize or list sources.")
            .AsBuilder()
            .UseOpenTelemetry(sourceName: "creator-agent", configure: c => c.EnableSensitiveData = true)
            .Build();
    }

    public override async ValueTask<ContentCreatorAgent.ContentResults> HandleAsync(
        ContentCreatorAgent.BlogResult blogResult, IWorkflowContext context, CancellationToken cancellationToken = default)
    {
        Console.WriteLine("[Workflow] SocialGeneration starting...");
        var topic = await context.ReadStateAsync<string>("topic", cancellationToken) ?? "unknown";

        string linkedIn;
        List<string> tweets;
        try
        {
            _session ??= await _agent.CreateSessionAsync(cancellationToken);
            var userPrompt = BuildSocialPrompt(topic, blogResult.Markdown);
            var response = await ContentCreatorAgent.AgentRunWithRetry(
                _agent, userPrompt, _session, cancellationToken);
            (linkedIn, tweets) = ContentCreatorAgent.ParseSocialResponse(response.Text ?? "");
            Console.WriteLine("[SocialWriter] Successfully generated social content via LLM");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[SocialWriter] Social LLM failed, using fallback: {ex.Message}");
            linkedIn = $"Deep dive into {topic}! Compiled from authoritative Azure sources " +
                "including Microsoft Learn, Azure Blog, and Azure-Samples. #Azure #CloudNative #AI";
            tweets =
            [
                $"Thread: {topic} (1/3)",
                $"Key takeaway: {topic} enables developers to build and deploy containerized apps without managing infrastructure. (2/3)",
                "Full article with all sources linked below! (3/3)"
            ];
        }

        var socialResult = new ContentCreatorAgent.SocialResult { LinkedIn = linkedIn, Tweets = tweets };
        var sourceCount = await context.ReadStateAsync<int>("sourceCount", cancellationToken);
        Console.WriteLine($"[Workflow] SocialGeneration complete (LinkedIn: {socialResult.LinkedIn.Length} chars, {socialResult.Tweets.Count} tweets)");
        return new ContentCreatorAgent.ContentResults
        {
            Blog = blogResult,
            Social = socialResult,
            Topic = topic,
            SourceCount = sourceCount
        };
    }

    private static string BuildSocialPrompt(string topic, string blogMarkdown)
    {
        return $"""
            Based on this blog article, generate social media content.
            Return EXACTLY in this format (no extra text before or after):

            LINKEDIN:
            <a compelling LinkedIn post of 150-250 characters about this topic, written for a professional developer audience. Include 3-5 relevant hashtags. Do NOT mention "compiled from X sources" — instead highlight a key insight or takeaway.>

            TWEET1:
            <an attention-grabbing opening tweet, max 280 chars>

            TWEET2:
            <a key technical insight or takeaway from the article, max 280 chars>

            TWEET3:
            <a call-to-action or forward-looking statement, max 280 chars>

            Article topic: {topic}

            Article content:
            {(blogMarkdown.Length > 3000 ? blogMarkdown[..3000] : blogMarkdown)}
            """;
    }
}
