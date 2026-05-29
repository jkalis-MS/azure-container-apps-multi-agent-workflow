/**
 * Orchestrator — Node.js/Express service that coordinates the multi-agent pipeline.
 *
 * Flow:
 *   1. Browser POSTs to /api/simulate with a prompt
 *   2. Orchestrator opens an SSE stream back to the browser
 *   3. Pre-warms all agents (health ping to avoid cold-start timeouts)
 *   4. Calls Simulator Agent → gets match simulation + sandbox metadata
 *   5. Calls Blog Agent + Narration Agent in parallel → gets article + audio
 *   6. Streams progress events to browser at each stage
 *
 * The orchestrator does NOT generate any AI content itself — it only coordinates.
 */
import "./instrumentation.js";
import express, { Request, Response } from "express";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { trace, SpanKind } from "@opentelemetry/api";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const tracer = trace.getTracer("wc-orchestrator");

const app = express();
app.use(express.json({ limit: "2mb" }));
app.use(express.static(join(__dirname, "..", "public")));

const SIMULATOR_URL = process.env.SIMULATOR_URL ?? "http://localhost:3001";
const BLOG_GEN_URL = process.env.BLOG_GEN_URL ?? "http://localhost:3002";
const NARRATION_GEN_URL = process.env.NARRATION_GEN_URL ?? "http://localhost:3003";

interface ProgressEvent {
  stage: string;
  service: "simulator" | "blog" | "narration";
  status: "started" | "progress" | "done" | "error";
  message: string;
  data?: unknown;
}

function sendEvent(res: Response, event: ProgressEvent) {
  res.write(`data: ${JSON.stringify(event)}\n\n`);
}

app.post("/api/simulate", async (req: Request, res: Response) => {
  const { prompt, secure_egress } = req.body;
  if (!prompt) {
    res.status(400).json({ error: "Missing prompt" });
    return;
  }

  // Set up SSE
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });

  // Heartbeat to keep connection alive
  const heartbeat = setInterval(() => res.write(": heartbeat\n\n"), 10000);

  try {
    await tracer.startActiveSpan(
      "invoke_agent orchestrator",
      {
        kind: SpanKind.INTERNAL,
        attributes: {
          "gen_ai.operation.name": "invoke_agent",
          "gen_ai.agent.name": "orchestrator",
          "gen_ai.agent.id": "wc-orchestrator",
          "gen_ai.system": "azure.ai.openai",
        },
      },
      async (orchestratorSpan) => {
    // Pre-warm all agents (wake from cold start)
    await Promise.allSettled([
      fetch(`${SIMULATOR_URL}/health`).catch(() => {}),
      fetch(`${BLOG_GEN_URL}/health`).catch(() => {}),
      fetch(`${NARRATION_GEN_URL}/health`).catch(() => {}),
    ]);

    // Step 1: Run Simulator
    sendEvent(res, {
      stage: "simulation",
      service: "simulator",
      status: "started",
      message: "Starting match simulation...",
    });

    const simResult = await tracer.startActiveSpan(
      "invoke_agent simulator-agent",
      {
        kind: SpanKind.INTERNAL,
        attributes: {
          "gen_ai.operation.name": "invoke_agent",
          "gen_ai.agent.name": "simulator-agent",
          "gen_ai.agent.id": "simulator-agent",
          "gen_ai.system": "azure.ai.openai",
        },
      },
      async (agentSpan) => {
        return tracer.startActiveSpan(
      "execute_tool call_simulator",
      {
        kind: SpanKind.INTERNAL,
        attributes: {
          "gen_ai.operation.name": "execute_tool",
          "gen_ai.tool.name": "call_simulator",
          "gen_ai.tool.type": "function",
        },
      },
      async (simSpan) => {
        const simUrl = secure_egress
          ? `${SIMULATOR_URL}/run?secure_egress=true`
          : `${SIMULATOR_URL}/run`;
        const simAbort = new AbortController();
        const simTimer = setTimeout(() => simAbort.abort(), 300_000);
        const simResponse = await fetch(simUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt }),
          signal: simAbort.signal,
        });
        clearTimeout(simTimer);

        if (!simResponse.ok) {
          const err = await simResponse.text();
          simSpan.setStatus({ code: 2, message: err });
          simSpan.end();
          agentSpan.setStatus({ code: 2, message: err });
          agentSpan.end();
          throw new Error(`Simulator failed: ${err}`);
        }

        const result = await simResponse.json();
        simSpan.end();
        agentSpan.end();
        return result;
      },
        );
      },
    );

    const simulation = simResult.simulation;
    const sandboxLogs = simResult.sandboxLogs ?? [];
    const sandboxIds = simResult.sandboxIds ?? [];
    const researchQueries = simResult.researchQueries ?? [];
    const generatedSimCode = simResult.generatedSimCode ?? "";

    sendEvent(res, {
      stage: "simulation",
      service: "simulator",
      status: "done",
      message: `${simulation.homeTeam} ${simulation.homeScore} - ${simulation.awayScore} ${simulation.awayTeam}`,
      data: { simulation, sandboxLogs, sandboxIds, researchQueries, generatedSimCode },
    });

    // Step 2: Run Blog Generator and Narration Generator in parallel
    sendEvent(res, {
      stage: "content",
      service: "blog",
      status: "started",
      message: "Generating match report...",
    });
    sendEvent(res, {
      stage: "content",
      service: "narration",
      status: "started",
      message: "Generating narration...",
    });

    const [blogResult, narrationResult] = await Promise.allSettled([
      tracer.startActiveSpan(
        "invoke_agent blog-agent",
        {
          kind: SpanKind.INTERNAL,
          attributes: {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "blog-agent",
            "gen_ai.agent.id": "blog-agent",
            "gen_ai.system": "azure.ai.openai",
          },
        },
        async (blogAgentSpan) => {
          return tracer.startActiveSpan(
        "execute_tool call_blog_generator",
        {
          kind: SpanKind.INTERNAL,
          attributes: {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "call_blog_generator",
            "gen_ai.tool.type": "function",
          },
        },
        async (blogSpan) => {
          const ctrl = new AbortController();
          const timer = setTimeout(() => ctrl.abort(), 180_000);
          const r = await fetch(`${BLOG_GEN_URL}/generate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ simulation }),
            signal: ctrl.signal,
          });
          clearTimeout(timer);
          if (!r.ok) {
            const err = await r.text();
            blogSpan.setStatus({ code: 2, message: err });
            blogSpan.end();
            blogAgentSpan.setStatus({ code: 2, message: err });
            blogAgentSpan.end();
            throw new Error(err);
          }
          const result = await r.json();
          blogSpan.end();
          blogAgentSpan.end();
          return result;
        },
          );
        },
      ),
      tracer.startActiveSpan(
        "invoke_agent narration-agent",
        {
          kind: SpanKind.INTERNAL,
          attributes: {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "narration-agent",
            "gen_ai.agent.id": "narration-agent",
            "gen_ai.system": "azure.ai.openai",
          },
        },
        async (narrationAgentSpan) => {
          return tracer.startActiveSpan(
        "execute_tool call_narration_generator",
        {
          kind: SpanKind.INTERNAL,
          attributes: {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "call_narration_generator",
            "gen_ai.tool.type": "function",
          },
        },
        async (narrationSpan) => {
          const ctrl = new AbortController();
          const timer = setTimeout(() => ctrl.abort(), 180_000);
          const r = await fetch(`${NARRATION_GEN_URL}/generate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ simulation }),
            signal: ctrl.signal,
          });
          clearTimeout(timer);
          if (!r.ok) {
            const err = await r.text();
            narrationSpan.setStatus({ code: 2, message: err });
            narrationSpan.end();
            narrationAgentSpan.setStatus({ code: 2, message: err });
            narrationAgentSpan.end();
            throw new Error(err);
          }
          const result = await r.json();
          narrationSpan.end();
          narrationAgentSpan.end();
          return result;
        },
          );
        },
      ),
    ]);

    // Report blog result
    if (blogResult.status === "fulfilled") {
      sendEvent(res, {
        stage: "content",
        service: "blog",
        status: "done",
        message: "Blog post ready",
        data: blogResult.value,
      });
    } else {
      sendEvent(res, {
        stage: "content",
        service: "blog",
        status: "error",
        message: blogResult.reason?.message ?? "Blog generation failed",
      });
    }

    // Report narration result
    if (narrationResult.status === "fulfilled") {
      sendEvent(res, {
        stage: "content",
        service: "narration",
        status: "done",
        message: "Narration ready",
        data: narrationResult.value,
      });
    } else {
      sendEvent(res, {
        stage: "content",
        service: "narration",
        status: "error",
        message: narrationResult.reason?.message ?? "Narration generation failed",
      });
    }

    // Final event
    sendEvent(res, {
      stage: "complete",
      service: "simulator",
      status: "done",
      message: "All agents complete",
    });

    orchestratorSpan.end();
      }); // end orchestrator span
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Unknown error";
    sendEvent(res, {
      stage: "error",
      service: "simulator",
      status: "error",
      message,
    });
  } finally {
    clearInterval(heartbeat);
    res.end();
  }
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "orchestrator" });
});

const PORT = parseInt(process.env.PORT ?? "3000");
app.listen(PORT, () => console.log(`Orchestrator listening on :${PORT}`));
