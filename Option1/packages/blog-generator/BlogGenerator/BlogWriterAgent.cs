using System.ClientModel;
using System.Text.Json;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace BlogGenerator;

/// <summary>
/// Blog Writer Agent using the Microsoft Agent Framework (MAF).
/// Uses IChatClient + ChatClientAgent + AgentSession for multi-turn generation.
/// </summary>
public class BlogWriterAgent
{
    private readonly AIAgent? _agent;
    private AgentSession? _session;

    private const string Instructions = """
        You are a professional sports journalist writing for a popular football blog.
        Write engaging, vivid match reports that bring the game to life for readers.
        Use a narrative style with dramatic descriptions of key moments.
        Output valid HTML with semantic tags (h1, h2, p, blockquote, etc.).
        Include a catchy headline, match summary, detailed play-by-play of goals, and a closing analysis.
        Do NOT include <html>, <head>, or <body> tags — just the article content.
        """;

    public BlogWriterAgent()
    {
        var endpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT");
        var key = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY");
        var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT") ?? "gpt-4o";

        if (!string.IsNullOrEmpty(endpoint) && !string.IsNullOrEmpty(key))
        {
            var credential = new ApiKeyCredential(key);
            var aoaiClient = new AzureOpenAIClient(new Uri(endpoint), credential);
            IChatClient chatClient = aoaiClient.GetChatClient(deployment)
                .AsIChatClient()
                .AsBuilder()
                .UseOpenTelemetry(sourceName: "blog-agent", configure: c => c.EnableSensitiveData = true)
                .Build();

            _agent = new ChatClientAgent(chatClient, name: "blog-agent", instructions: Instructions)
                .AsBuilder()
                .UseOpenTelemetry(sourceName: "blog-agent", configure: c => c.EnableSensitiveData = true)
                .Build();
        }
    }

    public async Task<string> GenerateBlogAsync(JsonElement simulation)
    {
        if (_agent == null)
            return "<p>Blog generation unavailable — Azure OpenAI not configured.</p>";

        _session ??= await _agent.CreateSessionAsync();

        var userPrompt = $"""
            Write a match report blog post for the following World Cup 2026 simulation:

            {simulation.GetRawText()}

            Make it exciting and dramatic. Include details about each goal, the flow of the match,
            and what this result means for both teams in the group stage.
            """;

        var response = await _agent.RunAsync(userPrompt, _session);
        return response.Text ?? "<p>No content generated</p>";
    }
}
