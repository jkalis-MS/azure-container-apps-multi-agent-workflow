# Run Locally

## Prerequisites

- Python 3.11+
- .NET 10 SDK
- Git

## Setup

```bash
cd Lab
cp .env.example .env
# Edit .env with your Azure OpenAI credentials (optional for basic demo)
```

## Start Agent 1 (Research)

```bash
cd Lab/src/agent-research
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -e ".[dev]"
uvicorn main:app --host 0.0.0.0 --port 8001
```

Runs on http://localhost:8001

## Start Agent 2 (Content Creator)

In a second terminal:

```bash
cd Lab/src/agent-creator/AgentCreator
dotnet run --no-launch-profile --urls "http://localhost:8002"
```

Runs on http://localhost:8002

## Start Agent 3 (Podcaster) — Optional

The podcaster agent converts research into a two-voice conversational podcast using TTS.

In **lab mode** (default), it uses the **Azure OpenAI TTS** deployment (`tts-1`) — no GPU or local TTS server needed. In **full mode**, it uses a self-hosted GPU XTTS-v2 server with Azure OpenAI as fallback.

### Start the Podcaster Agent

In a third terminal:

```bash
cd Lab/src/agent-podcaster
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -e ".[dev]"
uvicorn main:app --host 0.0.0.0 --port 8003
```

Runs on http://localhost:8003

### Enable the Podcaster

Set `CONTENT_FACTORY_MODE=full` in `Lab/.env` to include the podcaster in the pipeline:

```
CONTENT_FACTORY_MODE=full
```

### (Optional) Self-hosted GPU TTS Server

Only needed if you want to use XTTS-v2 instead of Azure OpenAI TTS. Requires a CUDA-capable GPU.

In a fifth terminal:

```bash
cd Lab/src/tts-server
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -e ".[dev]"
uvicorn main:app --host 0.0.0.0 --port 8004 --workers 1
```

Add these to `Lab/.env`:

```
TTS_SERVER_URL=http://localhost:8004
TTS_HOST_VOICE=host-female
TTS_GUEST_VOICE=guest-male
TTS_TIMEOUT_BUDGET_SECONDS=300
```

## Start Dev UI

In a fourth terminal:

```bash
cd Lab/src/dev-ui
python -m http.server 8080 --bind 0.0.0.0
```

Open http://localhost:8080

## Verify

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health   # if podcaster enabled
```

All should return `{"status":"healthy"}`.

## Use

1. Open http://localhost:8080
2. Enter a topic (e.g. "Migrating Java 8 to Azure Container Apps")
3. Click "Run Pipeline"
4. Agent 1 researches real sources (Microsoft Learn, GitHub, Stack Overflow)
5. Agent 2 generates blog post, demo project, social content from those sources
6. Agent 3 (if enabled) creates a conversational podcast episode with TTS audio