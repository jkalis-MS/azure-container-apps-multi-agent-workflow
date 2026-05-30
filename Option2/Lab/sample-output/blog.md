# Dynamic Sessions on Azure: Fast, isolated compute on demand

If you’ve ever needed to run untrusted or user-generated code safely, spin up an isolated workspace for a user in under a second, or fan out hundreds of short-lived interactive jobs without building your own orchestration layer, you’ve felt the pain dynamic sessions aim to solve. The promise is simple: give every request or user a secure, ephemeral compute environment that starts instantly, scales horizontally, and cleans itself up.

This article explains what dynamic sessions are in Azure, when to reach for them, and how to integrate them with the rest of your application architecture. Along the way, we’ll contrast compute “sessions” with authentication and web app session concepts so you don’t mix the metaphors, and we’ll share practical patterns you can adopt today.

## What are dynamic sessions?

In Azure Container Apps, dynamic sessions provide secure, sandboxed container environments that can be allocated in milliseconds using pre-warmed session pools, execute code or applications in isolation, and then automatically deprovision when they’re done. Each session is isolated from others (with Hyper-V isolation under the hood) and can optionally be fenced with network controls. Sessions scale elastically and are ideal for interactive workloads, running LLM-generated scripts, or multi-tenant apps that require per-user/process isolation without the cold-start penalty typical of on-demand containers. See the overview of [Dynamic sessions in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/sessions).

Key capabilities:
- Subsecond startup using prewarmed pools
- Strong isolation across sessions with managed lifecycle
- Horizontal scale with on-demand supply
- Optional network boundaries and policy enforcement

## Why you’d use them

Some scenarios where dynamic sessions shine:
- Run AI/LLM-generated code safely, without risking your app or data plane
- Provision per-user sandboxes for interactive notebooks, data wrangling, or training
- Embed “plugin islands” in a SaaS app so customer code can extend your product safely
- Execute short-lived pipelines or demos where startup time is a UX killer
- Isolate workloads by tenant for strict compliance boundaries

If you’ve been approximating this with pre-provisioned VMs or slow-to-start containers, sessions give you the same isolation with radically better time-to-ready.

## How dynamic sessions fit into your architecture

A simple mental model is “broker + pool + session endpoints”:
- A pool prewarms a number of session instances from a container image (your workload).
- Your app asks a broker (your service) to allocate a session when a user requests one.
- The broker binds the requester to the session endpoint and returns the connection info.
- Your app communicates with the session until the work completes or a timeout occurs.
- The session is released and the platform handles teardown and replenishment.

You can integrate this behind a web API, gateway, or even delegate access tokens to a front end. Keep secrets and credentials out of session images; fetch them via managed identity or scoped tokens per session if needed.

Example: A minimal Node.js broker pattern that allocates a session and forwards traffic to it. The allocate/deallocate calls are placeholders for your Container Apps Sessions integration; the proxy logic demonstrates how to bind a caller to a short-lived target.

```js
// server.js
import express from "express";
import { createProxyMiddleware } from "http-proxy-middleware";

const app = express();

// Allocate a new session for a user/request
async function allocateSession(userId) {
  // TODO: Call your session pool API and return an endpoint + auth
  // Example return shape:
  return { url: "https://session-123.yourapps.internal", token: "bearer abc" };
}

// Release session when done
async function releaseSession(sessionId) {
  // TODO: Call your sessions API to deallocate
}

app.post("/start", async (req, res) => {
  const userId = req.header("x-user-id");
  const sess = await allocateSession(userId);
  // Return connection details to the client or persist a mapping
  res.json({ endpoint: sess.url, token: sess.token });
});

// Optional: proxy endpoint to pin user traffic to their session
app.use("/session/:id", (req, res, next) => {
  const target = resolveSessionUrl(req.params.id); // your mapping
  createProxyMiddleware({
    target,
    changeOrigin: true,
    onProxyReq: (proxyReq) => {
      proxyReq.setHeader("Authorization", getSessionToken(req.params.id));
    },
  })(req, res, next);
});

app.listen(8080, () => console.log("Broker listening on 8080"));
```

Practical guidance:
- Size your prewarmed pool for baseline concurrency and allow burst scaling to keep latency low.
- Define an idle timeout and cooldown to auto-recover resources.
- Pin a user to one session at a time to simplify state management.
- Use network policies to restrict egress and prevent data exfiltration where required.
- Emit audit logs on allocation, access, and teardown.

## Don’t confuse compute sessions with auth or web sessions

“Session” means different things across the stack. Dynamic sessions are compute sandboxes. You’ll likely still have:
- Authentication sessions for users (tokens, cookies)
- Browser session controls and reverse-proxy inspection
- App-level stateful sessions

Here’s how they relate and where to look for controls.

1) Authentication session lifetime and reauthentication  
Use Microsoft Entra Conditional Access to tune sign-in behavior across apps, including sign-in frequency and persistent browser sessions. The defaults aim for strong security with fewer prompts; changes in posture (password reset, device noncompliance, account disable) revoke tokens automatically. Learn about [adaptive session lifetime policies](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-session-lifetime) and how to [configure session lifetime policies](https://learn.microsoft.com/en-us/entra/identity/conditional-access/howto-conditional-access-session-lifetime). Session controls like sign-in frequency and persistent sessions are covered in [Conditional Access: Session controls](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-session).

When you need to immediately cut off access (compromise, termination), revoke tokens and sessions across the tenant as described in [Revoking user access](https://learn.microsoft.com/en-us/entra/identity/users/users-revoke-access). Token lifetime configuration for some clients is possible in limited cases; review [Configurable token lifetimes](https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes) and the note about newer Conditional Access-based session management. For MFA and reauthentication best practices, see [Reauthentication prompts and session lifetime](https://learn.microsoft.com/en-us/entra/identity/authentication/concepts-azure-multi-factor-authentication-prompts-session-lifetime).

2) Browser-based session control and real-time data protection  
If you need to restrict what users can do within SaaS or your own apps (for example, block downloads on unmanaged devices), integrate Conditional Access with Microsoft Defender for Cloud Apps. Conditional Access App Control provides a reverse-proxy layer that can apply access and session policies in real time for browser sessions. See [Conditional Access app control](https://learn.microsoft.com/en-us/defender-cloud-apps/proxy-intro-aad).

3) App state (“session state”) in your web app  
If you need to carry user-specific state between HTTP requests, use built-in session/state management in your framework. In ASP.NET Core, add and use session like this:

```csharp
// Program.cs
var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDistributedMemoryCache();
builder.Services.AddSession();
var app = builder.Build();
app.UseSession();
app.MapGet("/set", (HttpContext ctx) => {
    ctx.Session.SetString("color", "blue");
    return Results.Ok("set");
});
app.MapGet("/get", (HttpContext ctx) => {
    var color = ctx.Session.GetString("color") ?? "none";
    return Results.Ok(color);
});
app.Run();
```

This state is distinct from dynamic compute sessions. Review patterns in [Session and state management in ASP.NET Core](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/app-state) to choose the right storage mechanism.

## Observability and user experience

Dynamic sessions are infrastructure. You’ll still want to instrument UX and analyze usage:
- For your web front end, Microsoft Clarity can capture session recordings, heatmaps, and events to understand how users reach the “start session” flow and where they drop off. It’s easy to add with a small script tag; see [Clarity documentation](https://learn.microsoft.com/en-us/clarity/) and setup guidance in [Setup Clarity](https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-setup). You can emit custom tags and events via the [Clarity client API](https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-api). The FAQ summarizes benefits like no sampling and near-real-time insights ([Clarity FAQ](https://learn.microsoft.com/en-us/clarity/faq)).
- If you provide conversational agents that spawn dynamic sessions (e.g., a “Run Code” tool), evaluate engagement through your agent’s analytics. Microsoft Copilot Studio offers dashboards for usage and session-level insights; see the [Analytics overview](https://learn.microsoft.com/en-us/microsoft-copilot-studio/analytics-overview).

## Cost and scale considerations

Prewarmed capacity trades cost for latency. Start by sizing a small pool that meets your typical concurrency with headroom. Let the platform scale out during spikes; reevaluate after you have baseline traffic. If you manage other session-based infrastructure, autoscale policies can help—Azure Virtual Desktop’s scaling plans are a useful mental model, supporting dynamic autoscaling for pooled session hosts (in preview). See [Create and assign a scaling plan](https://learn.microsoft.com/en-us/azure/virtual-desktop/autoscale-create-assign-scaling-plan).

Operational tips:
- Keep your session image lean to minimize warmup times.
- Externalize configuration and secrets; avoid baking static credentials.
- Set explicit lifetimes and cooldowns; build idempotent cleanup.
- Log allocation, usage, and teardown for auditing and billing.
- Test failure modes: early termination, network deny, image pull failure.

## Bringing it together

Dynamic sessions give you the missing building block for safe, instant, per-request compute. Pair them with sensible authentication session policies, browser session controls when needed, and straightforward app state management. Instrument the UX so you know what users actually experience. With those pieces in place, you can confidently let users “click run” without sacrificing security, performance, or your weekend.

## Resources

- https://learn.microsoft.com/en-us/azure/container-apps/sessions
- https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-session
- https://learn.microsoft.com/en-us/entra/identity/conditional-access/howto-conditional-access-session-lifetime
- https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-session-lifetime
- https://learn.microsoft.com/en-us/entra/identity/users/users-revoke-access
- https://learn.microsoft.com/en-us/entra/identity-platform/configurable-token-lifetimes
- https://learn.microsoft.com/en-us/defender-cloud-apps/proxy-intro-aad
- https://learn.microsoft.com/en-us/aspnet/core/fundamentals/app-state
- https://learn.microsoft.com/en-us/entra/identity/authentication/concepts-azure-multi-factor-authentication-prompts-session-lifetime
- https://learn.microsoft.com/en-us/azure/virtual-desktop/autoscale-create-assign-scaling-plan
- https://learn.microsoft.com/en-us/clarity/
- https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-setup
- https://learn.microsoft.com/en-us/clarity/setup-and-installation/clarity-api
- https://learn.microsoft.com/en-us/clarity/faq
- https://learn.microsoft.com/en-us/microsoft-copilot-studio/analytics-overview
- https://learn.microsoft.com/en-us/powershell/scripting/security/remoting/running-remote-commands
- https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/invoke-command
- https://learn.microsoft.com/en-us/power-platform/admin/api-request-limits-allocations
- https://learn.microsoft.com/en-us/power-apps/maker/canvas-apps/sign-in-to-power-apps
- https://learn.microsoft.com/en-us/training/career-paths/solution-architect