using AgentCreator.Models;

namespace AgentCreator;

/// <summary>A2A Agent Card for the Content Creator Agent.</summary>
public static class A2ACard
{
    public static AgentCard ForBaseUrl(string baseUrl)
    {
        var authEnabled = string.Equals(
            Environment.GetEnvironmentVariable("A2A_AUTH_ENABLED"),
            "true",
            StringComparison.OrdinalIgnoreCase);

        var card = new AgentCard
        {
            Name = "content-creator-agent",
            Description = "Transforms research briefs into multi-format content: blog, demo, social, presentation",
            Url = $"{baseUrl}/a2a",
            Version = "1.0.0",
            ProtocolVersion = "0.3.0",
            PreferredTransport = "JSONRPC",
            SupportedInterfaces = [],
            Capabilities = new AgentCapabilities { Streaming = false, PushNotifications = false },
            DefaultInputModes = ["text/plain", "application/json"],
            DefaultOutputModes = ["text/plain", "application/json"],
            Skills =
            [
                new AgentSkill
                {
                    Id = "create-content",
                    Name = "Create Multi-Format Content",
                    Description = "Generates blog post, demo project, social snippets, and presentation outline from a research brief",
                    Tags = ["content", "blog", "presentation", "social"],
                    InputModes = ["application/json"],
                    OutputModes = ["application/json"]
                },
                new AgentSkill
                {
                    Id = "revise-content",
                    Name = "Revise Content Based on Feedback",
                    Description = "Accepts quality feedback and produces revised content",
                    Tags = ["content", "revision", "editing"],
                    InputModes = ["application/json"],
                    OutputModes = ["application/json"]
                }
            ]
        };

        // Add security schemes only if auth is enabled
        if (authEnabled)
        {
            card = card with
            {
                SecuritySchemes = new Dictionary<string, AuthScheme>
                {
                    ["BearerAuth"] = new AuthScheme
                    {
                        Type = "http",
                        Scheme = "bearer",
                        BearerFormat = "SharedSecret"
                    },
                    ["ApiKeyAuth"] = new AuthScheme
                    {
                        Type = "apiKey",
                        In = "header",
                        Name = "X-API-Key"
                    }
                },
                Security =
                [
                    new Dictionary<string, string[]> { ["BearerAuth"] = [] },
                    new Dictionary<string, string[]> { ["ApiKeyAuth"] = [] }
                ]
            };
        }

        return card;
    }
}
