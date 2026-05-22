> 🌐 [中文](./README.md) &nbsp;|&nbsp; **English**

# Multi-Agent Collaborative Platform v3.0.0 — Cloud Hub

A unified multi-agent management platform based on Hermes (Ollama). Supports cloud deployment, API authentication, multi-machine agent registration, GitHub knowledge sync, file relay, and multi-agent collaborative tasks.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              User / Admin (Browser Dashboard)         │
└─────────────────────────┬───────────────────────────┘
                          │ HTTPS + WS
┌─────────────────────────▼───────────────────────────┐
│                Cloud SGA Hub (main.py)                │
│  Auth · Registry · DAG Scheduler · File Store · Git  │
└──┬──────────────────┬──────────────────┬─────────────┘
   │                  │                  │
   ▼                  ▼                  ▼
Machine A           Machine B          Machine C
agent_sdk           agent_sdk          agent_sdk
Coder Agent         GPU Agent          Writer Agent
Ollama              vLLM               Claude API
   │                  │                  │
   └──────────────────┼──────────────────┘
                      │ Git
              ┌───────▼────────┐
              │ GitHub Shared   │
              │ knowledge/     │
              │ skills/        │
              └────────────────┘
```

## Quick Start

```bash
# 1. Install dependencies
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# 2. Start Hub
uvicorn main:app --reload --port 9527

# 3. Open browser → http://localhost:9527

# 4. Start agents (another terminal)
python demo_agents.py
```

## What's New in v3.0.0

| Module | Feature |
|--------|---------|
| **Auth** | API Key authentication middleware, all /api/ routes protected |
| **Auth** | Frontend auto-prompts for Key, localStorage persistence |
| **File Transfer** | File upload/download API (multipart, 500MB limit) |
| **File Transfer** | Agent SDK supports upload_file / download_file |
| **Skills** | GitHub shared repo Skills sync to local database |
| **Skills** | Skills list/query/content API |
| **Skills** | Agent SDK supports get_skill / list_skills |
| **GitHub Sync** | Automated git clone/pull/push |
| **GitHub Sync** | Webhook auto-triggers sync |
| **Scheduler** | Orchestrator workflow completion/failure tracking |
| **Scheduler** | Upstream failure auto-cancels downstream dependencies |
| **Scheduler** | required_capabilities precise matching |
| **Multi-machine** | Agent SDK supports SGA_HUB_URL remote connection |
| **Multi-machine** | All requests auto-attach auth headers |

## API Reference

```
# Agents
POST /api/agents/register              Register agent
POST /api/agents/{id}/heartbeat        Heartbeat (with Metrics)
GET  /api/agents/{id}/metrics          Get metrics
POST /api/agents/{id}/message          Send message
GET  /api/agents/{id}/inbox            Get inbox
POST /api/agents/{id}/log              Write log
GET  /api/agents/{id}/logs             Query logs

# Tasks
POST /api/tasks                        Create task (with depends_on)
POST /api/tasks/{id}/complete          Complete task
POST /api/tasks/{id}/pause             Pause task
POST /api/tasks/{id}/resume            Resume task
POST /api/tasks/{id}/retry             Retry task
PATCH /api/tasks/{id}/context          Edit context
POST /api/tasks/{id}/reassign          Reassign

# Workflows
POST /api/workflows                    Create workflow (supports $N refs)
GET  /api/workflows/{id}               View workflow (with progress)
POST /api/workflows/{id}/pause         Pause workflow
POST /api/workflows/{id}/resume        Resume workflow

# File Transfer
POST /api/files/upload                 Upload file (multipart)
GET  /api/files/{task_id}/{filename}   Download file
GET  /api/files/{task_id}              List task files
DELETE /api/files/{task_id}/{filename} Delete file

# Skills
POST /api/shared/sync                  Sync GitHub repo
POST /api/shared/push                  Push to GitHub
GET  /api/skills                       List Skills
GET  /api/skills/{name}                Get Skill content

# Peers
POST /api/peers/join                   Node join
GET  /api/peers                        List peers
DELETE /api/peers/{id}                 Remove peer
POST /api/peers/{id}/sync              Sync remote agents

# Stats
GET  /api/stats                        Global snapshot
GET  /api/stats/tokens                 Token consumption stats
GET  /api/events                       Collaboration events
GET  /api/resources                    Real-time system resources

# Knowledge
GET  /api/knowledge                    List knowledge bases
POST /api/knowledge                    Create knowledge base
GET  /api/knowledge/global             Cross-node KB summary
POST /api/knowledge/{id}/broadcast     Broadcast to peers

# LLM / Webhook / Misc
POST /api/chat                         LLM inference (supports streaming)
POST /api/github/webhook               GitHub Webhook
GET  /api/models                       List Ollama models
GET  /health                           Health check
WS   /ws                               Real-time push
GET  /docs                             Swagger UI
```

## SDK Quick Reference

```python
from agent_sdk import AgentClient, PlannerAgent, CoderAgent, AnalystAgent

# Connect to remote Hub
agent = AgentClient(name="My Agent", role="analyzer",
                    platform_url="https://your-hub:9527")
await agent.register()

# File operations
await agent.upload_file(task_id, "/path/to/result.md")
await agent.download_file(task_id, "input.csv", "/tmp/input.csv")
files = await agent.list_task_files(task_id)

# Skills
skills = await agent.list_skills()
skill_content = await agent.get_skill("code_review")

# Tasks + LLM
answer = await agent.llm("Analyze this data...")
async for token in agent.llm_stream("Generate a report..."):
    print(token, end="", flush=True)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SGA_API_KEY` | empty (no auth) | API authentication key |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama URL |
| `HERMES_MODEL` | `hermes3` | Default LLM model |
| `HB_TIMEOUT` | `60` | Heartbeat timeout (seconds) |
| `ORCHESTRATOR_ENABLED` | `true` | Enable scheduler |
| `ORCHESTRATOR_INTERVAL` | `5` | Scheduler tick interval (seconds) |
| `TASK_STALL_TIMEOUT` | `300` | Task timeout threshold (seconds) |
| `DEFAULT_MAX_RETRIES` | `3` | Default max retry count |
| `SGA_SHARED_REPO` | `` | GitHub shared repo URL |
| `SGA_SHARED_DIR` | `./shared` | Local shared directory |
| `SGA_FILES_DIR` | `./task_files` | File storage directory |
| `GITHUB_WEBHOOK_SECRET` | `` | Webhook signing secret |
| `MAX_UPLOAD_SIZE_MB` | `500` | Max upload file size (MB) |
| `SGA_SEED_NODES` | `` | Seed nodes (comma-separated) |
| `SGA_PUBLIC_URL` | `` | This node's public URL |

## Project Structure

```
├── main.py              # Hub service (FastAPI)
├── orchestrator.py      # DAG task scheduler
├── peer_mesh.py         # Multi-node mesh
├── agent_sdk.py         # Agent SDK (Python)
├── index.html           # Dashboard frontend
├── demo_agents.py       # Demo agent script
├── start.sh             # One-click startup
├── config/
│   ├── hub.env          # Hub config template
│   └── agent.env        # Agent config template
├── scripts/
│   ├── start_hub.sh     # Cloud Hub startup
│   └── start_agent.sh   # Local Agent startup
├── feishu_bot_agent.py  # Feishu Bot Agent
└── docs/
    ├── QUICKSTART.md             # 5-minute quickstart
    └── feishu_bot_deploy.md      # Feishu Bot deploy guide
```

---

## Usage Guide

### 1. Deploy Hub (one server only)

```bash
git clone https://github.com/Bettermelsm/SGA_Multi-Agent.git
cd SGA_Multi-Agent
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# LAN use (no auth)
uvicorn main:app --host 0.0.0.0 --port 9527

# Public deployment (with auth)
export SGA_API_KEY=your-secret-key
uvicorn main:app --host 0.0.0.0 --port 9527
# Open browser → http://your-server:9527
```

### 2. Agent Registration (any machine)

**Only `agent_sdk.py` is needed** — no need to clone the entire repo.

```python
from agent_sdk import AgentClient

agent = AgentClient(
    name="My Agent",
    role="coder",
    capabilities=["python", "code_review"],
    platform_url="http://your-server:9527",  # Hub address
)
await agent.register()  # Auto-generates cross-machine unique ID
```

Or via environment variables:
```bash
export SGA_HUB_URL=http://your-server:9527
export SGA_API_KEY=your-secret-key
python demo_agents.py
```

**Machines on different platforms just need to point `platform_url` to the same Hub.**

### 3. Feishu Group Chat Integration

```bash
pip install httpx websockets fastapi uvicorn psutil

# Configure Feishu app credentials
export SGA_HUB_URL=http://your-server:9527
export SGA_API_KEY=your-secret-key
export FEISHU_APP_ID=cli_xxxxx
export FEISHU_APP_SECRET=xxxxx
export FEISHU_VERIFY_TOKEN=xxxxx

# Start Bot
python feishu_bot_agent.py
```

See [docs/feishu_bot_deploy.md](docs/feishu_bot_deploy.md) for detailed deployment steps.

Feishu group commands:
- `/task analyze this code` — create a single task
- `/workflow full code review process` — create a multi-step workflow
- `/agents` — view online agents
- `/status <workflow_id>` — query progress

### 4. How It Works

```
User sends command → Hub creates task → Orchestrator matches best idle Agent
                                                    ↓
                                         Agent executes task → Hub broadcasts result
                                                    ↓
                                         Feishu Bot / Dashboard receive real-time notification
```
