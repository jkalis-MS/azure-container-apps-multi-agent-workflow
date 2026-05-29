namespace BlogGenerator;

/// <summary>A2A Agent Card for discovery.</summary>
public static class A2ACard
{
    public static object ForBaseUrl(string baseUrl) => new
    {
        name = "blog-agent",
        description = "World Cup 2026 blog post generator — turns match simulation data into engaging HTML articles",
        url = $"{baseUrl}/a2a",
        version = "2.0.0",
        protocolVersion = "0.3.0",
        preferredTransport = "JSONRPC",
        capabilities = new { streaming = false, pushNotifications = false },
        defaultInputModes = new[] { "application/json" },
        defaultOutputModes = new[] { "application/json" },
        skills = new[]
        {
            new
            {
                id = "generate-blog",
                name = "Generate Match Report Blog",
                description = "Takes simulation JSON and produces an HTML match report blog post",
                tags = new[] { "soccer", "world-cup", "blog", "content-generation" },
                inputModes = new[] { "application/json" },
                outputModes = new[] { "application/json" }
            }
        }
    };
}
