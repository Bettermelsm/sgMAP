> 🌐 **中文** &nbsp;|&nbsp; [English](./README_EN.md)

# Multi-Agent 智能体协同平台 v3.0.0 — Cloud Hub

基于 Hermes (Ollama) 的多智能体统一管理平台。支持云端部署、API 鉴权、多机 Agent 接入、GitHub 知识库同步、文件中转与多 Agent 协同任务。

## 架构

```
┌─────────────────────────────────────────────────────┐
│              用户 / 管理员（浏览器看板）               │
└─────────────────────────┬───────────────────────────┘
                          │ HTTPS + WS
┌─────────────────────────▼───────────────────────────┐
│                云端 SGA Hub (main.py)                 │
│  鉴权 · 注册 · DAG调度 · 文件中转 · GitHub同步       │
└──┬──────────────────┬──────────────────┬─────────────┘
   │                  │                  │
   ▼                  ▼                  ▼
机器 A              机器 B              机器 C
agent_sdk           agent_sdk          agent_sdk
Coder Agent         GPU Agent          Writer Agent
Ollama              vLLM               Claude API
   │                  │                  │
   └──────────────────┼──────────────────┘
                      │ Git
              ┌───────▼────────┐
              │ GitHub 共享仓库 │
              │ knowledge/     │
              │ skills/        │
              └────────────────┘
```

## 快速开始

```bash
# 1. 安装依赖
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# 2. 启动 Hub
uvicorn main:app --reload --port 9527

# 3. 打开浏览器 → http://localhost:9527

# 4. 启动 Agent（另一个终端）
python demo_agents.py
```

## v3.0.0 新增功能

| 模块 | 新增 |
|------|------|
| **鉴权** | API Key 鉴权中间件，所有 /api/ 路由受保护 |
| **鉴权** | 前端自动提示输入 Key，localStorage 持久化 |
| **文件中转** | 文件上传/下载 API（multipart，500MB 上限）|
| **文件中转** | Agent SDK 支持 upload_file / download_file |
| **Skills** | GitHub 共享仓库 Skills 同步到本地数据库 |
| **Skills** | Skills 列表/查询/内容获取 API |
| **Skills** | Agent SDK 支持 get_skill / list_skills |
| **GitHub 同步** | git clone/pull/push 自动化 |
| **GitHub 同步** | Webhook 自动触发同步 |
| **调度增强** | Orchestrator 工作流完成/失败状态追踪 |
| **调度增强** | 上游任务失败自动取消下游依赖 |
| **调度增强** | required_capabilities 精确匹配 |
| **多机接入** | Agent SDK 支持 SGA_HUB_URL 远程连接 |
| **多机接入** | 所有请求自动附带鉴权头 |

## API 速查

```
# 智能体
POST /api/agents/register              注册智能体
POST /api/agents/{id}/heartbeat        心跳（含 Metrics）
GET  /api/agents/{id}/metrics          获取指标
POST /api/agents/{id}/message          发消息
GET  /api/agents/{id}/inbox            收件箱
POST /api/agents/{id}/log              写入日志
GET  /api/agents/{id}/logs             查询日志

# 任务
POST /api/tasks                        创建任务（含 depends_on）
POST /api/tasks/{id}/complete          完成任务
POST /api/tasks/{id}/pause             暂停任务
POST /api/tasks/{id}/resume            恢复任务
POST /api/tasks/{id}/retry             重试任务
PATCH /api/tasks/{id}/context          编辑上下文
POST /api/tasks/{id}/reassign          重新分配

# 工作流
POST /api/workflows                    创建工作流（支持 $N 依赖引用）
GET  /api/workflows/{id}               查看工作流（含进度）
POST /api/workflows/{id}/pause         暂停工作流
POST /api/workflows/{id}/resume        恢复工作流

# 文件中转
POST /api/files/upload                 上传文件（multipart）
GET  /api/files/{task_id}/{filename}   下载文件
GET  /api/files/{task_id}              列出任务文件
DELETE /api/files/{task_id}/{filename} 删除文件

# Skills
POST /api/shared/sync                  同步 GitHub 仓库
POST /api/shared/push                  推送到 GitHub
GET  /api/skills                       列出 Skills
GET  /api/skills/{name}                获取 Skill 内容

# 节点
POST /api/peers/join                   节点加入
GET  /api/peers                        列出节点
DELETE /api/peers/{id}                 移除节点
POST /api/peers/{id}/sync              同步远程 Agent

# 统计
GET  /api/stats                        全局快照
GET  /api/stats/tokens                 Token 消耗统计
GET  /api/events                       协同事件流
GET  /api/resources                    实时系统资源

# 知识库
GET  /api/knowledge                    知识库列表
POST /api/knowledge                    创建知识库
GET  /api/knowledge/global             跨节点知识库汇总
POST /api/knowledge/{id}/broadcast     广播到对等节点

# LLM / Webhook / 其他
POST /api/chat                         LLM 推理（支持流式）
POST /api/github/webhook               GitHub Webhook
GET  /api/models                       列出 Ollama 模型
GET  /health                           健康检查
WS   /ws                               实时推送
GET  /docs                             Swagger UI
```

## SDK 速查

```python
from agent_sdk import AgentClient, PlannerAgent, CoderAgent, AnalystAgent

# 连接远程 Hub
agent = AgentClient(name="我的智能体", role="analyzer",
                    platform_url="https://your-hub:9527")
await agent.register()

# 文件操作
await agent.upload_file(task_id, "/path/to/result.md")
await agent.download_file(task_id, "input.csv", "/tmp/input.csv")
files = await agent.list_task_files(task_id)

# Skills
skills = await agent.list_skills()
skill_content = await agent.get_skill("code_review")

# 任务 + LLM
answer = await agent.llm("分析这段数据...")
async for token in agent.llm_stream("生成报告..."):
    print(token, end="", flush=True)
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SGA_API_KEY` | 空（无鉴权） | API 鉴权密钥 |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama 地址 |
| `HERMES_MODEL` | `hermes3` | 默认 LLM 模型 |
| `HB_TIMEOUT` | `60` | 心跳超时（秒）|
| `ORCHESTRATOR_ENABLED` | `true` | 启用调度引擎 |
| `ORCHESTRATOR_INTERVAL` | `5` | 调度轮询间隔（秒）|
| `TASK_STALL_TIMEOUT` | `300` | 任务超时判定（秒）|
| `DEFAULT_MAX_RETRIES` | `3` | 默认最大重试次数 |
| `SGA_SHARED_REPO` | `` | GitHub 共享仓库 URL |
| `SGA_SHARED_DIR` | `./shared` | 本地共享目录 |
| `SGA_FILES_DIR` | `./task_files` | 文件存储目录 |
| `GITHUB_WEBHOOK_SECRET` | `` | Webhook 签名密钥 |
| `MAX_UPLOAD_SIZE_MB` | `500` | 最大上传文件（MB）|
| `SGA_SEED_NODES` | `` | 种子节点列表（逗号分隔）|
| `SGA_PUBLIC_URL` | `` | 本节点公网 URL |

## 项目结构

```
├── main.py              # Hub 主服务（FastAPI）
├── orchestrator.py      # DAG 任务调度引擎
├── peer_mesh.py         # 多节点互联模块
├── agent_sdk.py         # Agent SDK（Python）
├── feishu_bot_agent.py  # 飞书 Bot Agent（群聊接入）
├── index.html           # 看板前端
├── demo_agents.py       # 演示 Agent 脚本
├── start.sh             # 一键启动脚本
├── config/
│   ├── hub.env          # Hub 端配置模板
│   └── agent.env        # Agent 端配置模板
├── scripts/
│   ├── start_hub.sh     # 云端 Hub 启动
│   └── start_agent.sh   # 本地 Agent 启动
└── docs/
    ├── QUICKSTART.md             # 5 分钟快速上手
    └── feishu_bot_deploy.md      # 飞书 Bot 部署指南
```

---

## 使用指南

### 1. 部署 Hub（只需一台服务器）

```bash
git clone https://github.com/Bettermelsm/SGA_Multi-Agent.git
cd SGA_Multi-Agent
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# 局域网使用（无鉴权）
uvicorn main:app --host 0.0.0.0 --port 9527

# 公网部署（开启鉴权）
export SGA_API_KEY=your-secret-key
uvicorn main:app --host 0.0.0.0 --port 9527
# 浏览器打开 http://your-server:9527
```

### 2. 智能体注册（任意机器）

**只需 `agent_sdk.py` 一个文件**，不需要 clone 整个仓库。

```python
from agent_sdk import AgentClient

agent = AgentClient(
    name="我的智能体",
    role="coder",
    capabilities=["python", "code_review"],
    platform_url="http://your-server:9527",  # Hub 地址
)
await agent.register()  # 自动生成跨机器唯一 ID 并注册
```

或通过环境变量：
```bash
export SGA_HUB_URL=http://your-server:9527
export SGA_API_KEY=your-secret-key
python demo_agents.py
```

**不同机器只需改 `platform_url` 指向同一个 Hub 即可。**

### 3. 飞书群聊接入

```bash
pip install httpx websockets fastapi uvicorn psutil

# 配置飞书应用凭证
export SGA_HUB_URL=http://your-server:9527
export SGA_API_KEY=your-secret-key
export FEISHU_APP_ID=cli_xxxxx
export FEISHU_APP_SECRET=xxxxx
export FEISHU_VERIFY_TOKEN=xxxxx

# 启动 Bot
python feishu_bot_agent.py
```

详细部署步骤见 [docs/feishu_bot_deploy.md](docs/feishu_bot_deploy.md)。

飞书群内指令：
- `/task 分析这段代码` — 创建单个任务
- `/workflow 完整代码审查流程` — 创建多步工作流
- `/agents` — 查看在线智能体
- `/status <工作流ID>` — 查询进度

### 4. 工作方式

```
用户发指令 → Hub 创建任务 → Orchestrator 匹配最优空闲 Agent
                                         ↓
                              Agent 执行任务 → 完成后 Hub 广播结果
                                         ↓
                              飞书 Bot/看板 实时收到通知
```
