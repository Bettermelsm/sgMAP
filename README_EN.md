<div align="center">

# sgMAP
### Sengene General Multi-Agent Platform

**Open-Source General Multi-Agent Platform**

*For building, orchestrating and scaling AI agents, workflows and research pipelines*

---

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/Version-V2.0.0-blue?style=flat-square)](https://github.com/Bettermelsm/sgMAP/releases)
[![Stars](https://img.shields.io/github/stars/Bettermelsm/sgMAP?style=flat-square)](https://github.com/Bettermelsm/sgMAP/stargazers)

[Quick Start](#-quick-start) · [Architecture](#-architecture) · [Core Features](#-core-features) · [API Reference](#-api-reference) · [Deployment](#-deployment) · [中文](#中文)

</div>

---

## Platform Overview

**sgMAP** is a **lightweight, ready-to-use** multi-agent collaboration platform. The design principles are:

> **Simple and Efficient · First Principles · Don't Reinvent the Wheel**

Not yet another heavy Agent framework — sgMAP does one thing well: **connect AI agents running on different machines, schedule them uniformly, share knowledge, and complete tasks collaboratively.**

```
You send a message → sgMAP understands intent → Routes to best node → Executes → Returns result
```

![sgMAP Architecture](SGAMultiagnetV1.png)

---

## ✨ Core Features

| Feature | Description |
|---------|-------------|
| 🖥️ **Real-time Dashboard** | Browser view of all agent statuses, task progress, token usage, and system resources |
| 🔀 **DAG Task Scheduling** | `capabilities`-based precise matching, automatic routing to optimal node, dependency chain support |
| 🧠 **Skills Sharing** | One agent learns a new skill → synced to GitHub repo → all nodes share it |
| 💬 **Communication Bridge** | Feishu Bot built-in; WeChat/Work-WeChat/Slack extendable via template |
| 📁 **File Relay** | Built-in upload/download (≤ 500MB), direct file transfer between agents |
| 🌐 **Multi-node Peer Mesh** | Automatic node discovery, state sync, knowledge broadcast |
| 🔐 **API Key Authentication** | All `/api/` routes protected by Key, frontend dashboard auto-persists login |
| 📡 **WebSocket Push** | Real-time task status changes pushed to frontend, no polling needed |
| 🤖 **LLM Inference Interface** | Built-in `/api/chat`, supports Ollama streaming inference |

---

## 📁 Project Structure

```
sgMAP/
├── main.py                # Hub core: FastAPI + WebSocket, all API endpoints
├── orchestrator.py        # DAG scheduler: capabilities matching, automatic task assignment
├── agent_sdk.py           # Agent SDK (749 lines): register, heartbeat, task, file, Skills
├── peer_mesh.py           # Multi-node Peer Mesh: discovery, state sync, knowledge broadcast
├── feishu_bot_agent.py    # Feishu Bot Agent: full communication bridge (with SQLite persistence)
├── hermes_orchestrator.py # Hermes Orchestrator (V2.1, LLM task planning)
├── worker_agent_template.py # Worker Agent script template (V2.1)
├── skcl/                  # SKCL knowledge base (V2.1, Obsidian compatible)
│   ├── AGENTS.md          #   - Node capability registry
│   ├── skills/            #   - Skills library
│   └── wiki/              #   - Knowledge pages
├── demo_agents.py         # Interface example: SDK usage demo (NOT for production)
├── index.html             # Frontend dashboard: pure HTML, no build dependencies
├── start.sh               # One-click startup script
├── config/                # Configuration directory
├── docs/                  # Documentation directory
└── scripts/               # Helper scripts
```

---

## 🚀 Quick Start

### Requirements

- Python 3.10+
- Any Linux / macOS / Windows (WSL2)
- Memory ≥ 512MB (Hub only)

### 5-Minute Minimum Setup

```bash
# 1. Clone the repo
git clone https://github.com/Bettermelsm/sgMAP.git
cd sgMAP

# 2. Install dependencies
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# 3. Start Hub
bash start.sh
# or manually: uvicorn main:app --host 0.0.0.0 --port 9527

# 4. Open dashboard
# Browser: http://localhost:9527
```

After Hub is running, start an Agent on another machine (or same machine):

```python
# my_agent.py
import asyncio
from agent_sdk import AgentClient

async def main():
    agent = AgentClient(
        name="My Node",
        role="analyzer",                         # planner / coder / analyzer / retriever / evaluator
        capabilities=["data-analysis", "report-generation"],
        platform_url="http://<Hub_IP>:9527",
        api_key="your-api-key",
        heartbeat_interval=30,
    )
    await agent.register()
    print("Registered to Hub!")

    while True:
        inbox = await agent.get_inbox()
        for task in inbox:
            # Add your actual execution logic here
            await agent.complete_task(task["task_id"], "Done", {})
        await asyncio.sleep(10)

asyncio.run(main())
```

```bash
python3 my_agent.py
```

Refresh the dashboard to see the node come online and receive tasks.

---

## 🏗 Architecture

### Layered Structure

```
User / Communication Tools (Feishu · WeChat · Slack)
    │ Webhook / Browser
    ▼
sgMAP Hub (main.py :9527)
  Auth · Registry · Task Routing · File Relay · Skills Sync · Dashboard
    │ WebSocket / REST API
    ├──── Orchestrator Agent (Hermes + Ollama, GPU node)
    │       NL parsing → Task planning → Route to best Worker
    ├──── Worker Agent A (analyzer, high-memory node)
    ├──── Worker Agent B (coder, GPU node)
    └──── Worker Agent C (evaluator, lightweight cloud node)
              │
        SKCL Knowledge Base (GitHub repo, Skills + Wiki)
              │ Obsidian visualization (desktop)
```

### Core Concepts

**Hub vs Agent Responsibilities**

| | Hub | Agent |
|---|---|---|
| LLM Inference | ❌ | ✅ Hermes / Ollama |
| Task Routing | ✅ by capabilities matching | ❌ |
| Actual Computation | ❌ | ✅ bioinfo/docking/DLC etc. |
| Large File Transfer | ❌ metadata only | ✅ Syncthing direct |
| State Display | ✅ Dashboard | ✅ Active reporting |

**5 Standard Roles**

| Role | Use Case | Specialized Class |
|------|----------|-------------------|
| `planner` | Task decomposition, planning | `PlannerAgent` |
| `coder` | Code generation, scripting | `CoderAgent` |
| `analyzer` | Data analysis, reports | `AnalystAgent` |
| `retriever` | Full-text search, RAG | `AgentClient` |
| `evaluator` | Quality assessment, validation | `AgentClient` |

---

## 📡 API Reference

```
# Agent
POST   /api/agents/register          Register Agent (with capabilities)
POST   /api/agents/{id}/heartbeat    Heartbeat
GET    /api/agents/{id}/inbox        Get pending task inbox
POST   /api/agents/{id}/message      Send message to another Agent

# Task & Workflow
POST   /api/tasks                    Create task (with depends_on chain)
POST   /api/tasks/{id}/complete      Complete task
POST   /api/workflows                Create DAG workflow
GET    /api/workflows/{id}           View workflow progress

# File Relay (≤ 500MB)
POST   /api/files/upload             Upload file
GET    /api/files/{task_id}/{name}   Download file

# Skills & GitHub Sync
POST   /api/shared/sync              Pull Skills from GitHub repo
POST   /api/shared/push              Push Skills to GitHub
GET    /api/skills                   List all Skills

# Peer Mesh
POST   /api/peers/join               Node joins mesh
POST   /api/knowledge/{id}/broadcast Broadcast knowledge to all nodes

# Monitor
GET    /api/stats                    Global snapshot
GET    /api/stats/tokens             Token usage statistics
GET    /api/resources                Real-time system resources (CPU / memory / disk)
WS     /ws                           WebSocket real-time push

# LLM & Webhook
POST   /api/chat                     LLM inference (Ollama streaming supported)
POST   /api/github/webhook           GitHub Webhook triggers Skills sync
GET    /docs                         Swagger UI (auto-generated)
```

---

## 📦 Deployment

### Production Deployment (systemd)

```bash
sudo tee /etc/systemd/system/sgmap-hub.service << EOF
[Unit]
Description=sgMAP Hub
After=network.target

[Service]
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(which python3) -m uvicorn main:app --host 0.0.0.0 --port 9527
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable sgmap-hub && sudo systemctl start sgmap-hub
```

### Multi-Node Deployment

1. **Identify Hub node**: prefer machine with fixed public IP
2. **Identify Orchestrator**: choose GPU (≥ 8GB VRAM) machine, deploy Hermes + Ollama
3. **Register each node by role**: see example above, modify `role` and `capabilities`
4. **Configure AGENTS.md**: record each node's capability boundary in `skcl/AGENTS.md`
5. **Connect communication tools**: Feishu uses `feishu_bot_agent.py` directly, others see extension docs

### Communication Tool Integration (Feishu)

```bash
export FEISHU_APP_ID="your_app_id"
export FEISHU_APP_SECRET="your_app_secret"
export FEISHU_VERIFY_TOKEN="your_verify_token"
export SGA_HUB_URL="http://<Hub_IP>:9527"
export SGA_API_KEY="your-api-key"

python3 feishu_bot_agent.py
```

### SKCL Knowledge Base Sync

```bash
# Initialize knowledge base (in sgMAP directory)
mkdir -p skcl/skills skcl/wiki skcl/raw

# Sync to Hub
curl -X POST http://<Hub_IP>:9527/api/shared/sync \
  -H "X-API-Key: your-api-key" \
  -d '{"github_repo": "https://github.com/Bettermelsm/sgMAP"}'
```

### Reverse Proxy (HTTPS, Optional)

```nginx
server {
    listen 443 ssl;
    server_name hub.yourdomain.com;
    ssl_certificate     /etc/letsencrypt/live/hub.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hub.yourdomain.com/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:9527;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## 🤖 Agent Onboarding Guide

> If you are an AI agent, after receiving this repo, please follow this workflow to guide the user through deployment.

**Step 1: Ask About Resources**

```
Q1: How many machines? OS / CPU / memory / GPU for each?
Q2: Which has a fixed public IP? (→ Deploy Hub)
Q3: Is Hermes Agent already running on any machine?
Q4: Which communication tool? (Feishu / WeChat / None)
Q5: Are there >100MB file transfer needs between agents?
```

**Step 2: Generate Deployment Plan Based on Answers**

- Machine with public IP → Hub
- GPU (≥ 8GB) machine → Orchestrator (Hermes + Ollama)
- High memory (≥ 64GB) machine → `analyzer` role
- Resource-constrained machine → `evaluator` or `planner`
- >100MB file transfer → additionally deploy Syncthing

**Step 3: Output Directly Executable Commands**, no placeholders left.

---

## 🔧 Agent SDK Quick Reference

```python
from agent_sdk import AgentClient  # or CoderAgent / AnalystAgent / PlannerAgent

agent = AgentClient(name, role, capabilities, platform_url, api_key)

# Lifecycle
await agent.register()
await agent.stop()

# Tasks
task_id = await agent.create_task(title, description, priority)   # P0-P3
await agent.complete_task(task_id, result, metadata)
inbox   = await agent.get_inbox()

# Files (≤ 500MB)
await agent.upload_file(task_id, file_path)
await agent.download_file(task_id, filename, save_path)

# Skills
skills = await agent.list_skills()
skill  = await agent.get_skill(skill_name)

# Messages
await agent.send_message(target_agent_id, content)
```

---

## 🗺️ Roadmap

- [x] Hub core (FastAPI + WebSocket)
- [x] DAG scheduler (orchestrator.py)
- [x] Agent SDK (agent_sdk.py, 749 lines)
- [x] Multi-node Peer Mesh (peer_mesh.py)
- [x] Feishu Bot integration (feishu_bot_agent.py)
- [x] File Relay API (≤ 500MB)
- [x] Skills GitHub sync
- [x] API Key authentication
- [x] Token usage statistics & system resource monitoring
- [x] Hermes Orchestrator integration script (`hermes_orchestrator.py`)
- [x] SKCL knowledge directory standardization (`skcl/` + skills/ + wiki/)
- [x] V2.0.0 Release ([GitHub Releases](https://github.com/Bettermelsm/sgMAP/releases))
- [ ] WeChat / Work-WeChat Bot integration
- [ ] Prometheus + Grafana full-cluster monitoring
- [ ] Syncthing large file transfer integration doc

---

## 🤝 Contributing

Issues and Pull Requests are welcome at [GitHub Issues](https://github.com/Bettermelsm/sgMAP/issues).

---

## 📄 License

MIT License · © 2026 [Sengene](https://sengene.top)

---

---

## 中文

**sgMAP**（Sengene General Multi-Agent Platform）是一个轻量、开源的多智能体协同平台。它把分散在多台机器上的 AI 智能体连接起来，实现统一调度、知识共享和协同任务执行。

### 核心功能

- **实时看板**：浏览器查看所有 Agent 状态、任务进度、Token 消耗和系统资源
- **DAG 调度**：根据 `capabilities` 精确匹配，自动将任务路由给最佳 Agent
- **Skills 共享**：一个 Agent 学到的技能通过 GitHub 同步到全平台
- **通讯接入**：飞书 Bot 已内置；微信/企业微信/Slack 可按模板扩展
- **文件中转**：内置上传/下载 API（≤ 500MB），用于 Agent 间文件传输
- **Peer Mesh**：多节点发现、状态同步、知识广播

### 快速开始

```bash
git clone https://github.com/Bettermelsm/sgMAP.git
cd sgMAP
pip install fastapi uvicorn httpx python-multipart psutil aiofiles
bash start.sh
# 浏览器打开 http://localhost:9527
```

### 5 个标准 Role

| Role | 适用场景 |
|------|----------|
| `planner` | 任务拆解、方案设计 |
| `coder` | 代码生成、脚本实现 |
| `analyzer` | 数据分析、报告生成 |
| `retriever` | 全文搜索、RAG 检索 |
| `evaluator` | 质量评估、结论验证 |

完整文档见 [docs/README_deploy.md](docs/README_deploy.md) 或上方的中文版。

---

<div align="center">
  <sub>Built with ❤️ by <a href="https://sengene.top">Sengene（三君科技）</a></sub>
</div>
