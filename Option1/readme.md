# ⚽ World Cup 2026 Match Simulator

A multi-agent demo showcasing **Azure Container Apps** and **Azure Container Apps Sandboxes**. Simulates a Mexico 🇲🇽 vs Czechia 🇨🇿 World Cup 2026 match using AI-generated code running in isolated sandbox microVMs.

![Azure](https://img.shields.io/badge/Azure-Container%20Apps-0078D4?logo=microsoftazure)
![OpenAI](https://img.shields.io/badge/Azure%20OpenAI-GPT--4o-412991?logo=openai)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)
![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6?logo=typescript)
![.NET](https://img.shields.io/badge/.NET-8.0-512BD4?logo=dotnet)

## What It Does

Enter a prompt like *"Predict the result if both Mexico and Czechia play with their predicted 2026 World Cup lineups"* and the system:

1. **🔬 Simulator Agent** — Uses GPT-4o to generate Python code that searches the web (Wikipedia API, Google) for current team data, then generates more code to run the simulation. All code executes in isolated sandboxes with an egress firewall.
2. **📝 Blog Agent** — Takes the simulation result and generates a match report article.
3. **🎙️ Narration Agent** — Generates an English-language sports commentary script and converts it to audio using TTS-HD.

All three agents run as separate microservices on Azure Container Apps.

## Architecture

See [architecture.md](architecture.md) for the full Mermaid diagram and detailed explanation.

```
┌──────────────┐       ┌─────────────────────────────────────────────────┐
│   Browser    │──SSE──│  Orchestrator (port 3000)                       │
└──────────────┘       └──────┬──────────────┬──────────────┬────────────┘
                              │              │              │
                    POST /run │    POST /gen │    POST /gen │
                              ▼              ▼              ▼
                    ┌──────────────┐ ┌────────────┐ ┌──────────────┐
                    │  Simulator   │ │  Blog Gen  │ │ Narration Gen│
                    │  + Managed ID│ │  (.NET)    │ │  (FastAPI)   │
                    └──────┬───────┘ └────────────┘ └──────────────┘
                           │
              GPT-4o generates Python code
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
     ┌────────────┐ ┌────────────┐ ┌────────────┐
     │ Sandbox 1  │ │ Sandbox 2  │ │ Sandbox N  │  ← microVMs with egress firewall
     │ (roster)   │ │ (research) │ │ (simulate) │
     └────────────┘ └────────────┘ └─────┬──────┘
            │                             │
     ✅ Wikipedia API             ┌───────▼────────┐
     ✅ Google Search             │ Egress Firewall│
     ✅ FIFA.com                  │ Transform:     │
     ✅ BBC Sport                 │ inject api-key │
     🚫 ESPN.com                  │ from secret ref│
     🚫 Sky Sports                └───────┬────────┘
     🚫 Marca                             ▼
                                  ✅ Azure OpenAI
```

> The simulator uses System-Assigned Managed Identity to authenticate with the Sandboxes API.

## Key Demo Points

| Azure Feature | How It's Showcased |
|---|---|
| **Container Apps** | 4 microservices deployed to a shared environment |
| **Sandboxes — Code Isolation** | AI-generated Python code runs safely in microVMs |
| **Sandboxes — Egress Firewall** | ESPN, Sky Sports, Marca blocked; Wikipedia, Google, FIFA, BBC allowed |
| **Sandboxes — Secure Key Injection** | API key injected via egress Transform rule from secret reference — code never sees it |
| **Sandboxes — Parallel Execution** | 4-6 sandboxes (roster + research + simulation) run simultaneously |
| **Azure OpenAI** | GPT-4o for code generation, simulation, blog writing; TTS-HD for narration audio |

## Project Structure

```
packages/
├── orchestrator/          # Node.js/Express — UI + SSE streaming + coordination
│   ├── public/index.html  # Single-page app (dark console theme)
│   └── src/index.ts       # SSE endpoint, pre-warms agents, calls services
├── simulator/             # Python/FastAPI — AI code generation + sandbox orchestration
│   ├── src/agent.py       # LangGraph pipeline: decompose → search → simulate
│   ├── src/main.py        # FastAPI endpoints (/run, /a2a, /health)
│   ├── src/sandbox_client.py  # Sandboxes data-plane API client
│   └── sandbox-scripts/   # Fallback scripts (used if AI-generated code fails)
│       ├── search.py      # Wikipedia + Google search + egress probing
│       └── simulate.py    # OpenAI-powered match simulation
├── blog-generator/        # .NET/ASP.NET Core — GPT-4o blog post generation
│   └── src/
└── narration-generator/   # Python/FastAPI — Copilot SDK script + TTS-HD audio
    └── src/main.py
```

## Prerequisites

- **Azure Subscription** with access to:
  - Azure Container Apps
  - Azure Container Apps Sandboxes (preview)
  - Azure OpenAI with `gpt-4o` and `tts-hd` deployments
- **Azure CLI** (`az`) logged in
- **Azure Container Registry** (ACR) with admin access enabled
- **Node.js 20+** and **Python 3.12+** for local development

## Deployment

### Quick Start (one script)

```powershell
# 1. Clone and cd into the repo
git clone <this-repo> && cd worldCupSimulator

# 2. Copy .env.example → .env and fill in your values
cp .env.example .env
# Edit: subscription ID, resource group, ACR name, sandbox group, OpenAI endpoint/key

# 3. Load env vars and run setup
Get-Content .env | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim()) } }
./infra/setup.ps1
```

The setup script handles everything: resource group, ACR, image builds, Bicep deployment (environment + sandbox group + 4 apps), RBAC for Managed Identity, and sandbox secret creation.

### What `setup.ps1` Does (step by step)

| Step | Action |
|------|--------|
| 1 | Creates resource group |
| 2 | Creates ACR with admin access |
| 3 | Builds all 4 container images |
| 4 | Creates sandbox group (via REST — preview API) |
| 5 | Deploys `infra/main.bicep` — Standard env (4 services, simulator with MI) + sandbox group |
| 6 | Assigns Contributor RBAC on the sandbox group to the simulator's Managed Identity |
| 7 | Creates sandbox group secret (`aoai-api-key`) for secure egress key injection |

### Prerequisites

Before running setup, you need:
- **Azure CLI** logged in (`az login`)
- **Azure OpenAI** resource with `gpt-4o` and `tts-hd` deployments (create manually or via Portal)
- Access to **Container Apps** and **Sandboxes** previews

### Subsequent Deployments

After initial setup, use `deploy.ps1` to rebuild images and update apps:

```powershell
# Rebuild and redeploy all services
./infra/deploy.ps1

# Or a single service
./infra/deploy.ps1 -Services simulator
```

## Environment Variables

| Service | Variable | Description |
|---|---|---|
| Orchestrator | `SIMULATOR_URL` | Simulator service URL (set by Bicep) |
| Orchestrator | `BLOG_GEN_URL` | Blog generator service URL (set by Bicep) |
| Orchestrator | `NARRATION_GEN_URL` | Narration generator service URL (set by Bicep) |
| Simulator | `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| Simulator | `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| Simulator | `ACA_SANDBOX_GROUP` | Sandbox group name |
| Simulator | `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| Simulator | `ACA_RESOURCE_GROUP` | Resource group name |
| Blog Gen | `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| Blog Gen | `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| Narration Gen | `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| Narration Gen | `AZURE_OPENAI_KEY` | Azure OpenAI API key |

> The simulator authenticates to the Sandboxes API via **Managed Identity** (system-assigned). No service principal env vars needed.

## Known Limitations

- **AI-generated code reliability** — the simulator generates Python code with GPT-4o; if it fails, it falls back to pre-written scripts transparently
- **Copilot SDK timeouts** — the narration agent's Copilot SDK session occasionally times out; it falls back to a direct Azure OpenAI call automatically
- **Cold starts** — the orchestrator pre-warms all agents at request time, but first request after idle may be slightly slower

## License

This is a demo project for showcasing Azure services. Not intended for production use.
