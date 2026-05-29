# Architecture — World Cup 2026 Match Simulator

```mermaid
flowchart TB
    subgraph User["👤 User"]
        Browser["Browser"]
    end

    subgraph CAE["Azure Container Apps"]
        direction TB
        Orchestrator["🎯 Orchestrator<br/><i>Node.js / Express</i><br/>Port 3000<br/>─────────<br/>• Serves UI (HTML/JS)<br/>• SSE streaming to browser<br/>• Pre-warms all agents<br/>• Coordinates pipeline"]

        Simulator["🔬 Simulator Agent<br/><i>Python / FastAPI</i><br/>Port 3001<br/>─────────<br/>• Decomposes prompts (GPT-4o)<br/>• Generates Python code (GPT-4o)<br/>• Manages sandbox lifecycle<br/>• Applies egress policies<br/>• Fetches rosters via Wikipedia API"]

        BlogGen["📝 Blog Agent<br/><i>.NET / ASP.NET Core</i><br/>Port 3002<br/>─────────<br/>• Generates match report<br/>• Uses Azure OpenAI GPT-4o"]

        NarrationGen["🎙️ Narration Agent<br/><i>Python / FastAPI</i><br/>Port 3003<br/>─────────<br/>• Multi-turn script (Copilot SDK)<br/>• Fallback: direct Azure OpenAI<br/>• Text-to-Speech (tts-hd)<br/>• English, 'echo' voice"]
    end

    subgraph Sandboxes["Azure Container Apps Sandboxes"]
        direction TB
        SBR1["🇲🇽 Roster Sandbox<br/><i>Python 3.14 microVM</i><br/>Wikipedia API fetch"]
        SBR2["🇨🇿 Roster Sandbox<br/><i>Python 3.14 microVM</i><br/>Wikipedia API fetch"]
        SB1["🔍 Research Sandbox 1<br/><i>Python 3.14 microVM</i><br/>AI-generated code"]
        SBN["🔍 Research Sandbox N<br/><i>Python 3.14 microVM</i><br/>AI-generated code"]
        SBC["⚙️ Simulation Sandbox<br/><i>Python 3.14 microVM</i><br/>AI-generated code<br/>Calls Azure OpenAI"]
    end

    subgraph Egress["🔒 Sandbox Egress Firewall"]
        direction LR
        Allow["✅ ALLOWED<br/>─────────<br/>Wikipedia API<br/>Google Search<br/>FIFA.com<br/>BBC Sport<br/>Azure OpenAI"]
        Block["🚫 BLOCKED<br/>─────────<br/>ESPN.com<br/>Sky Sports<br/>Marca.com"]
        Transform["🔑 TRANSFORM<br/>─────────<br/>Inject api-key<br/>from secret ref<br/>(simulation sandbox)"]
    end

    subgraph AOAI["Azure OpenAI"]
        GPT4o["GPT-4o<br/><i>Chat completions</i>"]
        TTS["TTS-HD<br/><i>Text-to-Speech</i>"]
    end

    Browser -->|"POST /api/simulate"| Orchestrator
    Orchestrator -->|"SSE events"| Browser
    Orchestrator -->|"1️⃣ POST /run"| Simulator
    Orchestrator -->|"2️⃣ POST /generate<br/>(parallel)"| BlogGen
    Orchestrator -->|"2️⃣ POST /generate<br/>(parallel)"| NarrationGen

    Simulator -->|"Decompose prompt<br/>Generate code"| GPT4o
    Simulator -->|"Create & execute"| SBR1
    Simulator -->|"Create & execute"| SBR2
    Simulator -->|"Create & execute"| SB1
    Simulator -->|"Create & execute"| SBN
    Simulator -->|"Create & execute"| SBC

    SBR1 & SBR2 -->|"Wikipedia fetch"| Egress
    SB1 & SBN -->|"Web search"| Egress
    SBC -->|"OpenAI call"| Egress

    BlogGen -->|"Generate blog"| GPT4o
    NarrationGen -->|"Generate script"| GPT4o
    NarrationGen -->|"Speech synthesis"| TTS

    style CAE fill:#1a2332,stroke:#00cc33,color:#e0e0e0
    style Sandboxes fill:#1a1a2e,stroke:#00cccc,color:#e0e0e0
    style Egress fill:#1a1a1a,stroke:#666,color:#e0e0e0
    style AOAI fill:#2a1a2e,stroke:#cc66ff,color:#e0e0e0
    style Allow fill:#0a1a0a,stroke:#00cc33,color:#00ff41
    style Block fill:#1a0a0a,stroke:#ff3333,color:#ff3333
    style Transform fill:#1a1a0a,stroke:#cccc00,color:#ffff00
```

## Flow

1. **User** submits a prompt via the browser UI
2. **Orchestrator** pre-warms all agents, streams progress via SSE, and calls the Simulator
3. **Simulator** uses GPT-4o to decompose the prompt into research queries (+ mandatory roster queries)
4. **Simulator** uses GPT-4o to **generate Python code** for each research task
5. **Roster sandboxes** (2) fetch squad data directly from Wikipedia's API (Players section)
6. **Research sandboxes** (1-3 in parallel) execute AI-generated code in isolated microVMs
   - Each sandbox has an **egress firewall** — ESPN, Sky Sports, Marca are blocked (HTTP 403)
   - Wikipedia, Google, FIFA, BBC Sport are allowed
7. **Simulator** uses GPT-4o to **generate Python simulation code**
8. A **simulation sandbox** executes the code, calling Azure OpenAI
   - With **Secure Egress**: API key is injected via Transform rule from a secret reference — code never sees it
   - Without Secure Egress: API key is passed as env var (visible in `/tmp/openai_request.txt`)
9. **Orchestrator** sends simulation results to Blog and Narration agents in parallel
10. **Blog Agent** (.NET) generates a match report article via GPT-4o
11. **Narration Agent** generates a commentary script (Copilot SDK or direct AOAI fallback) and audio (TTS-HD)

## Key Demo Points

| Feature | How it's showcased |
|---|---|
| **Container Apps** | All 4 microservices deployed to a shared environment |
| **Sandboxes — Isolation** | AI-generated, untrusted Python code runs safely in microVMs |
| **Sandboxes — Egress Firewall** | ESPN, Sky Sports, Marca blocked; Wikipedia, Google, FIFA, BBC allowed |
| **Sandboxes — Secure Key Injection** | API key injected via egress Transform rule using secret reference — sandbox code never sees it |
| **Sandboxes — Parallel Execution** | 4-6 sandboxes (roster + research + simulation) run simultaneously |
| **Azure OpenAI** | GPT-4o for code generation, simulation, blog; TTS-HD for narration |

## Secure Egress — API Key Injection

When "Secure Egress" is enabled, the simulation sandbox's egress policy includes a **Transform rule** that injects the `api-key` header for requests to Azure OpenAI:

```json
{
  "name": "inject-aoai-key",
  "match": { "host": "<your-openai-resource>.openai.azure.com" },
  "action": {
    "type": "Transform",
    "headers": [{
      "operation": "Set",
      "name": "api-key",
      "valueRef": {
        "secretRef": {
          "secretId": "aoai-api-key",
          "secretKey": "api-key"
        }
      }
    }]
  }
}
```

The secret is stored at sandbox group level and resolved at egress time. The code inside the sandbox uses a placeholder value and the real key is never exposed.
