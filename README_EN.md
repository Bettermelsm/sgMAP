> 🌐 [中文](./README.md) &nbsp;|&nbsp; **English**

# Multi-Agent Collaborative Platform v2.0.0

A unified multi-agent management platform based on Hermes (Ollama), featuring real-time dashboard, DAG task scheduling, and multi-node networking.

## Architecture

```
Frontend Dashboard (index.html)
  7-page navigation · WebSocket real-time push · Chart.js · Command Center · i18n (ZH/EN)
        ↕ WS / REST
FastAPI Backend (main.py)
  SQLite persistence · 50+ API endpoints · DAG scheduler · Multi-node mesh · Streaming LLM
        ↕ HTTP
Ollama / Hermes          Your Agents (agent_sdk.py)
  Local LLM inference    Register · Heartbeat · Tasks · Multi-turn chat · Tool calls
```

## Quick Start

```bash
# 1. Install dependencies
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# 2. Start the platform
uvicorn main:app --reload --port 9527

# 3. Open browser
# http://localhost:9527

# 4. Start demo agents (another terminal)
python demo_agents.py
```

## What's New in v2.0.0

| Module | Feature |
|--------|---------|
| Orchestrator | DAG-based task dependency scheduling, automatic sequential execution |
| Orchestrator | Capability-based agent-task matching (greedy strategy) |
| Orchestrator | Timeout detection with automatic retry |
| Orchestrator | Task intervention: pause / resume / retry / reassign / edit context |
| Workflows | Workflow CRUD + batch pause / resume |
| Multi-node | Node discovery and auto-announce |
| Multi-node | Cross-node agent sync |
| Multi-node | Knowledge base broadcast to peer nodes |
| Dashboard | Token consumption bar chart |
| Dashboard | Real-time agent log stream (WebSocket-driven) |
| Dashboard | Command Center page: workflow management + task intervention + node monitoring |
| SDK | Cross-machine unique Agent ID (node fingerprint + UUID) |
| SDK | Standard AgentMetrics (Token / CPU / Memory) |
| SDK | Auto-collect process resources in heartbeat |

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
POST /api/workflows                    Create workflow
GET  /api/workflows/{id}               View workflow
POST /api/workflows/{id}/pause         Pause workflow
POST /api/workflows/{id}/resume        Resume workflow

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

# LLM / Misc
POST /api/chat                         LLM inference (supports streaming)
GET  /api/models                       List Ollama models
GET  /health                           Health check
WS   /ws                               Real-time push
GET  /docs                             Swagger UI
```

## SDK Quick Reference

```python
from agent_sdk import AgentClient, PlannerAgent, CoderAgent, AnalystAgent

agent = AgentClient(name="My Agent", role="analyzer")
await agent.register()

# Create tasks with dependencies
t1 = await agent.create_task("Data collection", priority="P1")
t2 = await agent.create_task("Data analysis", priority="P2")
# t2 will be auto-assigned by the orchestrator after t1 completes

# Single-shot LLM
answer = await agent.llm("Analyze this data...")

# Multi-turn conversation
await agent.llm("Question 1", remember=True)
await agent.llm("Follow-up question", remember=True)
agent.clear_history()

# Streaming output
async for token in agent.llm_stream("Generate a report..."):
    print(token, end="", flush=True)

# Messaging
await agent.send_message(other_agent_id, "Please help with this task")
msgs = await agent.get_inbox()
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama URL |
| `HERMES_MODEL` | `hermes3` | Default LLM model |
| `HB_TIMEOUT` | `60` | Heartbeat timeout (seconds) |
| `ORCHESTRATOR_ENABLED` | `true` | Enable scheduling engine |
| `ORCHESTRATOR_INTERVAL` | `5` | Scheduler tick interval (seconds) |
| `TASK_STALL_TIMEOUT` | `300` | Task timeout threshold (seconds) |
| `DEFAULT_MAX_RETRIES` | `3` | Default max retry count |
| `SGA_SEED_NODES` | `` | Seed nodes (comma-separated) |
| `SGA_PUBLIC_URL` | `` | This node's public URL |
