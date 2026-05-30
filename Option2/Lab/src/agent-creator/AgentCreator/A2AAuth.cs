namespace AgentCreator;

public class A2AAuthFilter : IEndpointFilter
{
    private readonly bool _enabled;
    private readonly string? _token;

    public A2AAuthFilter()
    {
        var enabledVar = Environment.GetEnvironmentVariable("A2A_AUTH_ENABLED");
        _enabled = string.Equals(enabledVar, "true", StringComparison.OrdinalIgnoreCase);
        _token = Environment.GetEnvironmentVariable("A2A_AUTH_TOKEN");

        // Fail-fast: if auth enabled but no token, crash on startup
        if (_enabled && string.IsNullOrWhiteSpace(_token))
        {
            throw new InvalidOperationException("A2A_AUTH_ENABLED=true but A2A_AUTH_TOKEN is empty. Set the token or disable auth.");
        }
    }

    public async ValueTask<object?> InvokeAsync(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
    {
        // Auth disabled? Pass through
        if (!_enabled)
        {
            return await next(context);
        }

        var httpContext = context.HttpContext;
        var authHeader = httpContext.Request.Headers.Authorization.FirstOrDefault();
        var apiKeyHeader = httpContext.Request.Headers["X-API-Key"].FirstOrDefault();

        string? providedToken = null;

        // Check Bearer token
        if (!string.IsNullOrEmpty(authHeader) && authHeader.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
        {
            providedToken = authHeader["Bearer ".Length..].Trim();
        }
        // Check API Key
        else if (!string.IsNullOrEmpty(apiKeyHeader))
        {
            providedToken = apiKeyHeader.Trim();
        }

        // Validate
        if (string.IsNullOrEmpty(providedToken) || providedToken != _token)
        {
            httpContext.Response.StatusCode = 401;
            return Results.Json(new { error = "Unauthorized", message = "Invalid or missing authentication token" });
        }

        return await next(context);
    }
}
