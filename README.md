> 🌐 **中文** &nbsp;|&nbsp; [English](./README_EN.md)

# Multi-Agent 智能体协同平台 v2.0.0

基于 Hermes (Ollama) 的多智能体统一管理平台，带实时看板、DAG 任务调度与多机互联。

## 架构

```
前端看板 (index.html)
  7 页导航 · WebSocket 实时推送 · Chart.js · 指挥台页面 · 中英文切换
        ↕ WS / REST
FastAPI 后端 (main.py)
  SQLite 持久化 · 50+ API 端点 · DAG 调度引擎 · 多节点互联 · 流式 LLM
        ↕ HTTP
Ollama / Hermes          你的智能体 (agent_sdk.py)
  本地 LLM 推理          注册 · 心跳 · 任务 · 多轮对话 · 工具调用
```

## 快速开始

```bash
# 1. 安装依赖
pip install fastapi uvicorn httpx python-multipart psutil aiofiles

# 2. 启动平台
uvicorn main:app --reload --port 9527

# 3. 打开浏览器
# http://localhost:9527

# 4. 启动演示智能体（另一个终端）
python demo_agents.py
```

## v2.0.0 新增功能

| 模块 | 新增 |
|------|------|
| 调度中枢 | DAG 任务依赖编排，自动按序执行 |
| 调度中枢 | 基于能力的智能体-任务匹配（贪心策略）|
| 调度中枢 | 超时检测与自动重试 |
| 调度中枢 | 任务干预：暂停/恢复/重试/重分配/编辑上下文 |
| 工作流 | 工作流 CRUD + 批量暂停/恢复 |
| 多机互联 | 节点发现与自动宣告 |
| 多机互联 | 跨节点 Agent 同步 |
| 多机互联 | 知识库广播到对等节点 |
| 看板 | Token 消耗统计柱状图 |
| 看板 | 实时 Agent 日志流（WebSocket 驱动）|
| 看板 | 指挥台页面：工作流管理 + 任务干预 + 节点监控 |
| SDK | 跨机器唯一 Agent ID（节点指纹 + UUID）|
| SDK | AgentMetrics 标准指标（Token/CPU/内存）|
| SDK | 心跳自动采集进程资源 |

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
POST /api/workflows                    创建工作流
GET  /api/workflows/{id}               查看工作流
POST /api/workflows/{id}/pause         暂停工作流
POST /api/workflows/{id}/resume        恢复工作流

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

# LLM / 其他
POST /api/chat                         LLM 推理（支持流式）
GET  /api/models                       列出 Ollama 模型
GET  /health                           健康检查
WS   /ws                               实时推送
GET  /docs                             Swagger UI
```

## SDK 速查

```python
from agent_sdk import AgentClient, PlannerAgent, CoderAgent, AnalystAgent

agent = AgentClient(name="我的智能体", role="analyzer")
await agent.register()

# 创建带依赖的任务
t1 = await agent.create_task("数据采集", priority="P1")
t2 = await agent.create_task("数据分析", priority="P2")
# t2 会在 t1 完成后自动被调度器分配

# 单次 LLM
answer = await agent.llm("分析这段数据...")

# 多轮对话
await agent.llm("问题1", remember=True)
await agent.llm("追问", remember=True)
agent.clear_history()

# 流式输出
async for token in agent.llm_stream("生成报告..."):
    print(token, end="", flush=True)

# 消息
await agent.send_message(other_agent_id, "请协助处理任务")
msgs = await agent.get_inbox()
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama 地址 |
| `HERMES_MODEL` | `hermes3` | 默认 LLM 模型 |
| `HB_TIMEOUT` | `60` | 心跳超时（秒）|
| `ORCHESTRATOR_ENABLED` | `true` | 启用调度引擎 |
| `ORCHESTRATOR_INTERVAL` | `5` | 调度轮询间隔（秒）|
| `TASK_STALL_TIMEOUT` | `300` | 任务超时判定（秒）|
| `DEFAULT_MAX_RETRIES` | `3` | 默认最大重试次数 |
| `SGA_SEED_NODES` | `` | 种子节点列表（逗号分隔）|
| `SGA_PUBLIC_URL` | `` | 本节点公网 URL |
