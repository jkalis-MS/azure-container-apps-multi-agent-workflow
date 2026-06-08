using System.ClientModel;
using System.Text;
using System.Text.Json;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

namespace AgentCreator;

/// <summary>
/// Content Creator Agent using the Microsoft Agent Framework.
/// See https://github.com/microsoft/agent-framework
/// Provides the shared IChatClient and utility methods used by workflow executors.
/// </summary>
public class ContentCreatorAgent
{
    private readonly IConfiguration _config;
    private const int MaxRetries = 3;
    public IChatClient? ChatClient { get; private set; }

    public ContentCreatorAgent(IConfiguration config)
    {
        _config = config;

        try
        {
            var endpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT");
            var key = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY");
            var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT")
                ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT_NAME")
                ?? "gpt-4o";

            if (!string.IsNullOrEmpty(endpoint) && !string.IsNullOrEmpty(key))
            {
                var credential = new System.ClientModel.ApiKeyCredential(key);
                var aoaiClient = new Azure.AI.OpenAI.AzureOpenAIClient(new Uri(endpoint), credential);
                ChatClient = aoaiClient.GetChatClient(deployment)
                    .AsIChatClient()
                    .AsBuilder()
                    .UseOpenTelemetry(sourceName: "creator-agent", configure: c => c.EnableSensitiveData = true)
                    .Build();
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[ContentCreator] IChatClient initialization failed: {ex.GetType().Name}: {ex.Message}");
        }
    }

    /// <summary>Parse research brief JSON into typed fields.</summary>
    public static ParsedBrief ParseBrief(JsonElement researchData)
    {
        var brief = researchData.TryGetProperty("research_brief", out var b) ? b : researchData;
        var topic = brief.TryGetProperty("topic", out var t) ? t.GetString() ?? "unknown" : "unknown";
        var summary = brief.TryGetProperty("summary", out var s) ? s.GetString() ?? "" : "";
        var audience = brief.TryGetProperty("audience", out var aud) ? aud.GetString() ?? "technical professionals" : "technical professionals";

        var sources = ExtractArray(brief, "sources");
        var sourceTexts = new List<SourceMaterial>();

        foreach (var src in sources)
        {
            var title = GetStr(src, "title");
            if (string.IsNullOrEmpty(title)) title = GetStr(src, "name");
            var url = GetStr(src, "url");
            var content = GetStr(src, "content");
            var desc = GetStr(src, "description");
            var srcType = GetStr(src, "type");

            var text = !string.IsNullOrEmpty(content) ? content : desc;
            if (!string.IsNullOrEmpty(text))
            {
                sourceTexts.Add(new SourceMaterial
                {
                    Title = title,
                    Url = url,
                    Content = text.Length > 2000 ? text[..2000] : text,
                    Type = srcType
                });
            }
        }

        return new ParsedBrief { Topic = topic, Summary = summary, Audience = audience, Sources = sourceTexts };
    }

    /// <summary>Package blog + social results into the final content_package shape.</summary>
    public static object PackageResult(string topic, BlogResult blog, SocialResult social, int sourceCount)
    {
        return new
        {
            content_package = new
            {
                blog_post = new
                {
                    title = topic,
                    markdown = blog.Markdown,
                    word_count = blog.WordCount,
                    sources_used = sourceCount
                },
                social = new
                {
                    linkedin = new { text = social.LinkedIn, char_count = social.LinkedIn.Length },
                    twitter_thread = social.Tweets.ToArray()
                }
            }
        };
    }

    /// <summary>Retry wrapper for session-based RunAsync. Handles 429 rate limits.</summary>
    internal static async Task<AgentResponse> AgentRunWithRetry(AIAgent agent, string prompt, AgentSession session, CancellationToken ct = default)
    {
        for (int attempt = 1; attempt <= MaxRetries; attempt++)
        {
            try
            {
                return await agent.RunAsync(prompt, session, cancellationToken: ct);
            }
            catch (ClientResultException ex) when (ex.Status == 429 && attempt < MaxRetries)
            {
                var waitSeconds = 60 * attempt;
                if (ex.Message.Contains("retry after", StringComparison.OrdinalIgnoreCase))
                {
                    var match = System.Text.RegularExpressions.Regex.Match(ex.Message, @"retry after (\d+) seconds");
                    if (match.Success && int.TryParse(match.Groups[1].Value, out var parsed))
                        waitSeconds = parsed + 5;
                }
                Console.WriteLine($"[ContentCreator] Rate limited (429). Retrying in {waitSeconds}s (attempt {attempt}/{MaxRetries})...");
                await Task.Delay(TimeSpan.FromSeconds(waitSeconds), ct);
            }
        }
        return await agent.RunAsync(prompt, session, cancellationToken: ct);
    }

    internal static string ExtractSection(string text, string marker)
    {
        var idx = text.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
        if (idx < 0) return "";

        var start = idx + marker.Length;
        // Find the next section marker or end of string
        var nextMarkers = new[] { "LINKEDIN:", "TWEET1:", "TWEET2:", "TWEET3:" };
        var end = text.Length;
        foreach (var m in nextMarkers)
        {
            if (m.Equals(marker, StringComparison.OrdinalIgnoreCase)) continue;
            var mIdx = text.IndexOf(m, start, StringComparison.OrdinalIgnoreCase);
            if (mIdx >= 0 && mIdx < end) end = mIdx;
        }

        return text[start..end].Trim();
    }

    /// <summary>Parse structured social media response (LINKEDIN/TWEET sections).</summary>
    internal static (string linkedIn, List<string> tweets) ParseSocialResponse(string text)
    {
        var linkedIn = ExtractSection(text, "LINKEDIN:");
        var tweet1 = ExtractSection(text, "TWEET1:");
        var tweet2 = ExtractSection(text, "TWEET2:");
        var tweet3 = ExtractSection(text, "TWEET3:");

        if (string.IsNullOrWhiteSpace(linkedIn))
            throw new InvalidOperationException("Failed to parse LinkedIn content from LLM response");

        var tweets = new List<string>();
        if (!string.IsNullOrWhiteSpace(tweet1)) tweets.Add(tweet1);
        if (!string.IsNullOrWhiteSpace(tweet2)) tweets.Add(tweet2);
        if (!string.IsNullOrWhiteSpace(tweet3)) tweets.Add(tweet3);
        if (tweets.Count == 0)
            throw new InvalidOperationException("Failed to parse tweet content from LLM response");

        return (linkedIn, tweets);
    }

    public static string CompileFallback(string topic, string summary, List<SourceMaterial> sources)
    {
        var blog = new StringBuilder();
        var total = sources.Count;

        // Group sources by type
        var docs = sources.Where(s => s.Type == "documentation").ToList();
        var blogs = sources.Where(s => s.Type == "blog").ToList();
        var code = sources.Where(s => s.Type == "code_sample").ToList();
        var updates = sources.Where(s => s.Type == "update").ToList();

        // Proper introduction with summary from research brief
        blog.AppendLine($"# {topic}");
        blog.AppendLine();
        blog.AppendLine(string.IsNullOrEmpty(summary)
            ? $"Understanding {topic} is essential for developers and architects working with modern cloud platforms."
            : summary);
        blog.AppendLine();

        var sourceTypes = new List<string>();
        if (docs.Count > 0) sourceTypes.Add("Microsoft Learn documentation");
        if (blogs.Count > 0) sourceTypes.Add("Azure Blog posts");
        if (code.Count > 0) sourceTypes.Add("GitHub samples");
        if (updates.Count > 0) sourceTypes.Add("Azure Updates");
        var sourceList = sourceTypes.Count > 0 ? string.Join(", ", sourceTypes) : "authoritative sources";

        blog.AppendLine($"This article brings together insights from {total} authoritative sources including {sourceList} to provide a comprehensive overview of {topic}.");
        blog.AppendLine();

        // Documentation section as narrative paragraphs with inline links
        if (docs.Count > 0)
        {
            blog.AppendLine("## Official Documentation and Guidance");
            blog.AppendLine();
            blog.AppendLine($"Microsoft Learn provides {docs.Count} resource{(docs.Count != 1 ? "s" : "")} covering official guidance and architectural patterns for {topic}.");
            blog.AppendLine();
            foreach (var d in docs)
            {
                if (!string.IsNullOrEmpty(d.Content))
                {
                    var trimmed = d.Content.Length > 300 ? d.Content[..300] + "..." : d.Content;
                    blog.AppendLine($"According to [{d.Title}]({d.Url}), {trimmed}");
                }
                else
                {
                    blog.AppendLine($"The [{d.Title}]({d.Url}) resource provides additional context on this topic.");
                }
                blog.AppendLine();
            }
        }

        // Blog posts section as narrative paragraphs
        if (blogs.Count > 0)
        {
            blog.AppendLine("## Insights and Announcements");
            blog.AppendLine();
            blog.AppendLine($"The Azure Blog and Tech Community offer {blogs.Count} post{(blogs.Count != 1 ? "s" : "")} with practical insights and recent announcements related to {topic}.");
            blog.AppendLine();
            foreach (var b in blogs)
            {
                if (!string.IsNullOrEmpty(b.Content))
                {
                    var trimmed = b.Content.Length > 300 ? b.Content[..300] + "..." : b.Content;
                    blog.AppendLine($"In [{b.Title}]({b.Url}), the author explains: {trimmed}");
                }
                else
                {
                    blog.AppendLine($"The post [{b.Title}]({b.Url}) covers related developments.");
                }
                blog.AppendLine();
            }
        }

        // Code samples section as narrative paragraphs
        if (code.Count > 0)
        {
            blog.AppendLine("## Implementation Patterns and Code Samples");
            blog.AppendLine();
            blog.AppendLine($"Developers can explore {code.Count} code sample{(code.Count != 1 ? "s" : "")} on GitHub that demonstrate practical implementation patterns for {topic}.");
            blog.AppendLine();
            foreach (var c in code)
            {
                if (!string.IsNullOrEmpty(c.Content))
                {
                    var trimmed = c.Content.Length > 300 ? c.Content[..300] + "..." : c.Content;
                    blog.AppendLine($"The [{c.Title}]({c.Url}) sample shows how to get started: {trimmed}");
                }
                else
                {
                    blog.AppendLine($"The [{c.Title}]({c.Url}) repository provides a working reference implementation.");
                }
                blog.AppendLine();
            }
        }

        // Updates section as narrative paragraphs
        if (updates.Count > 0)
        {
            blog.AppendLine("## Latest Developments");
            blog.AppendLine();
            blog.AppendLine($"Azure Updates tracks {updates.Count} recent development{(updates.Count != 1 ? "s" : "")} related to {topic}.");
            blog.AppendLine();
            foreach (var u in updates)
            {
                if (!string.IsNullOrEmpty(u.Content))
                {
                    var trimmed = u.Content.Length > 300 ? u.Content[..300] + "..." : u.Content;
                    blog.AppendLine($"A recent update on [{u.Title}]({u.Url}) reports: {trimmed}");
                }
                else
                {
                    blog.AppendLine($"The update [{u.Title}]({u.Url}) signals ongoing investment in this area.");
                }
                blog.AppendLine();
            }
        }

        // Key Takeaways section
        blog.AppendLine("## Key Takeaways");
        blog.AppendLine();
        blog.AppendLine($"Based on {total} sources reviewed:");
        blog.AppendLine();
        if (docs.Count > 0)
            blog.AppendLine($"- {docs.Count} documentation article{(docs.Count != 1 ? "s" : "")} from Microsoft Learn provide official guidance");
        if (blogs.Count > 0)
            blog.AppendLine($"- {blogs.Count} blog post{(blogs.Count != 1 ? "s" : "")} offer practical insights and announcements");
        if (code.Count > 0)
            blog.AppendLine($"- {code.Count} code sample{(code.Count != 1 ? "s" : "")} on GitHub demonstrate implementation patterns");
        if (updates.Count > 0)
            blog.AppendLine($"- {updates.Count} Azure Update{(updates.Count != 1 ? "s" : "")} track the latest developments");
        blog.AppendLine();

        // Resources section with all source links
        blog.AppendLine("## Resources");
        blog.AppendLine();
        foreach (var s in sources)
            blog.AppendLine($"- [{s.Title}]({s.Url})");

        return blog.ToString();
    }

    private static List<JsonElement> ExtractArray(JsonElement parent, string property)
    {
        if (parent.TryGetProperty(property, out var arr) && arr.ValueKind == JsonValueKind.Array)
            return arr.EnumerateArray().ToList();
        return new List<JsonElement>();
    }

    private static string GetStr(JsonElement el, string prop)
    {
        if (el.TryGetProperty(prop, out var v))
        {
            return v.ValueKind switch
            {
                JsonValueKind.String => v.GetString() ?? "",
                JsonValueKind.Number => v.GetRawText(),
                _ => v.GetRawText()
            };
        }
        return "";
    }

    public record SourceMaterial
    {
        public string Title { get; init; } = "";
        public string Url { get; init; } = "";
        public string Content { get; init; } = "";
        public string Type { get; init; } = "";
    }

    public record ParsedBrief
    {
        public string Topic { get; init; } = "";
        public string Summary { get; init; } = "";
        public string Audience { get; init; } = "technical professionals";
        public List<SourceMaterial> Sources { get; init; } = [];
    }

    public record BlogResult
    {
        public string Markdown { get; init; } = "";
        public int WordCount { get; init; }
        public int SourcesUsed { get; init; }
    }

    public record SocialResult
    {
        public string LinkedIn { get; init; } = "";
        public List<string> Tweets { get; init; } = [];
    }

    /// <summary>Combined blog + social results flowing through the workflow pipeline.</summary>
    public record ContentResults
    {
        public required BlogResult Blog { get; init; }
        public required SocialResult Social { get; init; }
        public string Topic { get; init; } = "";
        public int SourceCount { get; init; }
    }
}
