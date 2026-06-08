using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;

namespace AgentCreator.Tests;

public class AgentCreatorTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public AgentCreatorTests(WebApplicationFactory<Program> factory)
    {
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task HealthEndpoint_ReturnsHealthy()
    {
        var response = await _client.GetAsync("/health");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var doc = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
        Assert.Equal("healthy", doc.RootElement.GetProperty("status").GetString());
        Assert.Equal("creator-agent", doc.RootElement.GetProperty("agent").GetString());
    }

    [Fact]
    public async Task AgentCard_HasRequiredFields()
    {
        var response = await _client.GetAsync("/.well-known/agent.json");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var doc = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
        var root = doc.RootElement;
        Assert.Equal("content-creator-agent", root.GetProperty("name").GetString());
        Assert.True(root.TryGetProperty("url", out _));
        Assert.True(root.TryGetProperty("skills", out var skills));
        Assert.True(skills.GetArrayLength() > 0);
        Assert.Equal("create-content", skills[0].GetProperty("id").GetString());
    }

    [Fact]
    public async Task A2A_RejectsInvalidMethod()
    {
        var payload = new
        {
            jsonrpc = "2.0",
            method = "tasks/invalid",
            @params = new { id = "t1", message = new { parts = new object[] { } } },
            id = "r1"
        };
        var response = await _client.PostAsJsonAsync("/a2a", payload);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var doc = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
        Assert.True(doc.RootElement.TryGetProperty("error", out var err));
        Assert.Equal(-32601, err.GetProperty("code").GetInt32());
    }

    [Fact]
    public async Task A2A_RejectsMissingData()
    {
        var payload = new
        {
            jsonrpc = "2.0",
            method = "tasks/send",
            @params = new
            {
                id = "t1",
                message = new
                {
                    parts = new object[]
                    {
                        new { type = "text", text = "hello" }
                    }
                }
            },
            id = "r2"
        };
        var response = await _client.PostAsJsonAsync("/a2a", payload);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task A2A_ProcessesValidTask()
    {
        var payload = new
        {
            jsonrpc = "2.0",
            method = "tasks/send",
            @params = new
            {
                id = "task-002",
                message = new
                {
                    parts = new object[]
                    {
                        new
                        {
                            type = "data",
                            data = new
                            {
                                research_brief = new
                                {
                                    topic = "Java 8 to Azure Container Apps",
                                    summary = "Test summary",
                                    sources = new[]
                                    {
                                        new { title = "Doc1", url = "https://learn.microsoft.com/test", type = "documentation", content = "Documentation content", description = "" },
                                        new { title = "Code Sample", url = "https://github.com/azure-samples/test", type = "code_sample", content = "", description = "Sample repo" }
                                    },
                                    sources_with_content = 1,
                                    total_sources = 2
                                }
                            }
                        }
                    }
                }
            },
            id = "req-002"
        };

        var response = await _client.PostAsJsonAsync("/a2a", payload);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var doc = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
        var root = doc.RootElement;
        Assert.Equal("2.0", root.GetProperty("jsonrpc").GetString());
        Assert.Equal("task-002", root.GetProperty("result").GetProperty("id").GetString());
        Assert.Equal("completed", root.GetProperty("result").GetProperty("status").GetProperty("state").GetString());
        Assert.True(root.GetProperty("result").GetProperty("artifacts").GetArrayLength() > 0);
    }

    [Fact]
    public void ParseBrief_ExtractsFieldsCorrectly()
    {
        var jsonStr = """{"research_brief":{"topic":"Test Topic","summary":"overview","audience":"developers","sources":[{"title":"Doc1","url":"https://example.com","type":"documentation","content":"Some content"}]}}""";
        var data = JsonDocument.Parse(jsonStr).RootElement;
        var brief = ContentCreatorAgent.ParseBrief(data);
        Assert.Equal("Test Topic", brief.Topic);
        Assert.Equal("overview", brief.Summary);
        Assert.Equal("developers", brief.Audience);
        Assert.Single(brief.Sources);
        Assert.Equal("Doc1", brief.Sources[0].Title);
    }

    [Fact]
    public void PackageResult_CreatesExpectedShape()
    {
        var blog = new ContentCreatorAgent.BlogResult { Markdown = "# Hello", WordCount = 1, SourcesUsed = 1 };
        var social = new ContentCreatorAgent.SocialResult { LinkedIn = "Post", Tweets = ["Tweet1"] };
        var result = ContentCreatorAgent.PackageResult("Topic", blog, social, 1);
        var json = JsonSerializer.Serialize(result);
        var doc = JsonDocument.Parse(json);
        Assert.True(doc.RootElement.TryGetProperty("content_package", out var pkg));
        Assert.Equal("Topic", pkg.GetProperty("blog_post").GetProperty("title").GetString());
        Assert.Equal("Post", pkg.GetProperty("social").GetProperty("linkedin").GetProperty("text").GetString());
    }
}
