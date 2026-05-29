# Copilot Instructions

World Cup Match Simulator — multi-agent demo for Azure Container Apps + Sandboxes.

## Build & Test

```bash
# TypeScript services (orchestrator)
npm install          # from repo root — installs all workspaces
npm run build -w packages/orchestrator

# Python service (simulator)
cd packages/simulator && pip install -r requirements.txt

# Run locally (each in a separate terminal)
npm start -w packages/orchestrator        # port 3000
cd packages/simulator && uvicorn src.main:app --port 3001
cd packages/blog-generator && dotnet run   # port 3002
cd packages/narration-generator && uvicorn src.main:app --port 3003
```

## Architecture

Monorepo with 4 microservices deployed to Azure Container Apps:

- **Orchestrator** (TS/Express, port 3000) — Web UI + SSE streaming, fans out to agents
- **Simulator** (Python/FastAPI, port 3001) — Uses Sandboxes for parallel web search + computation
- **Blog Generator** (.NET/ASP.NET Core, port 3002) — GPT-4o blog post generation
- **Narration Generator** (Python/FastAPI, port 3003) — GPT-4o script + tts-hd audio

Data flow: User → Orchestrator → Simulator (first) → Blog Gen + Narration Gen (parallel)

## Conventions

- TypeScript services use Express + OpenAI SDK
- Simulator uses FastAPI + httpx for async Sandboxes REST API
- All services expose `/health` for readiness checks
- Environment config via `.env` files (see `.env.example`)
- Azure OpenAI endpoint: set via `AZURE_OPENAI_ENDPOINT` env var
- Container Apps region: westcentralus
