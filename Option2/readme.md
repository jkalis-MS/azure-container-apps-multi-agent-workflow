
# Option 2: Technical content writer and podcaster


Enter a topic (e.g. *"Write a comprehensive blog post about Azure Container Apps for developers"*). Three agents collaborate:

1. **Agent 1 — Tech Research** (LangGraph / Python) — Searches Microsoft Learn, Azure Blog, Tech Community, Azure Updates, and GitHub. Uses AI for intent detection, ranks sources by relevance, fetches full content from top hits, follows depth-1 links from trusted domains, and synthesizes a research brief. Can optionally fetch the top sources inside ACA Sandboxes with per-domain egress policies so the Dev UI can show isolated sandbox status and blocked outbound requests.
2. **Agent 2 — Content Creator** (Microsoft Agent Framework / .NET) — Transforms the research brief into an original blog post, LinkedIn post, and Twitter thread, all grounded in real sources.
3. **Agent 3 — Podcaster** (GitHub Copilot SDK / Python) — Creates an engaging podcast script and generates audio. Can use Azure OpenAI TTS or a custom XTTS-v2 server on serverless GPUs.

**Dev UI** — A lightweight HTML frontend for submitting topics and viewing results from all three agents. When sandbox mode is enabled, it can surface sandbox status tiles before the research brief is ready.

Agents communicate via the **A2A (Agent-to-Agent) protocol** — each exposes a `/.well-known/agent.json` card for discovery and a `/a2a` JSON-RPC endpoint for task submission. Each agent runs as a separate container on Azure Container Apps.

## Deploy to Azure

```bash
azd up
```

This creates: Azure AI Foundry (GPT-4o + TTS), ACR, ACA Environment, Log Analytics, Storage Account, and Container Apps for each agent plus the Dev UI.

## Run Locally

With Docker Compose:

```bash
cd Lab
cp .env.example .env  # fill in your Azure OpenAI credentials
docker compose up     # starts agents 1-3 + dev-ui
```

Without Docker, see the step-by-step guide: [Lab/docs/how-to-run-locally.md](Lab/docs/how-to-run-locally.md).

### Verify

```bash
curl http://localhost:8001/health   # {"status":"healthy","agent":"research-agent"}
curl http://localhost:8002/health   # {"status":"healthy","agent":"creator-agent"}
curl http://localhost:8003/health   # {"status":"healthy","agent":"podcaster-agent"}
```

## Project Structure

```
azure.yaml                  # Azure Developer CLI (azd) configuration
Lab/
  docker-compose.yml         # Local multi-container setup
  docs/                      # Architecture docs & run-locally guide
  sample-output/             # Example blog and podcast transcript
  src/
    agent-research/          # Agent 1: Python, LangGraph, FastAPI
    agent-creator/           # Agent 2: .NET 10, Microsoft Agent Framework
    agent-podcaster/         # Agent 3: Python, GitHub Copilot SDK, TTS
    tts-server/              # GPU XTTS-v2 server (full mode only)
    dev-ui/                  # Static HTML + nginx (port 8080)
infra/
  main.bicep                 # Azure deployment (Foundry, ACR, ACA, Storage, GPU)
  main.parameters.json       # azd parameter wiring
  pre-rendered/              # Standalone Bicep for lab vendor provisioning
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent 1 — Research | Python 3.11+, LangGraph, LangChain, FastAPI, httpx, BeautifulSoup |
| Agent 2 — Creator | .NET 10, Microsoft Agent Framework, ASP.NET Minimal APIs |
| Agent 3 — Podcaster | Python 3.10+, GitHub Copilot SDK, pydub, httpx |
| TTS (lab mode) | Azure OpenAI `tts-1` |
| TTS (full mode) | Coqui XTTS-v2 on GPU, Azure OpenAI fallback |
| Protocol | A2A (Agent-to-Agent) |
| Infrastructure | Azure Container Apps, Azure Container Registry, Bicep |
| Observability | OpenTelemetry, Azure Application Insights |
| LLM | Azure OpenAI GPT-4o |

## Questions?

Create an issue in the repo with your question or concern.

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.