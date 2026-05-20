# SGA Cloud Hub — 5 分钟快速上手

## 1. 启动 Hub

```bash
pip install fastapi uvicorn httpx psutil aiofiles python-multipart
uvicorn main:app --port 9527
# 浏览器打开 http://localhost:9527
```

## 2. 启动 Agent

```bash
# 另一个终端
python demo_agents.py
```

## 3. 公网部署（可选）

```bash
# 设置 API Key
export SGA_API_KEY=your-secret-key

# 启动
uvicorn main:app --host 0.0.0.0 --port 9527

# 推荐用 Nginx 反向代理 + HTTPS
```

## 4. 多机 Agent 接入

在远程机器上:
```bash
export SGA_HUB_URL=https://your-hub-address
export SGA_API_KEY=your-secret-key
python demo_agents.py https://your-hub-address
```

## 5. GitHub 知识库同步（可选）

```bash
export SGA_SHARED_REPO=https://github.com/your-org/SGA_Shared.git
# 重启 Hub，或在看板「指挥台」页面点击"同步 GitHub"
```

## 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SGA_API_KEY` | 空（无鉴权） | API 鉴权密钥 |
| `SGA_SHARED_REPO` | 空 | GitHub 共享仓库 URL |
| `ORCHESTRATOR_ENABLED` | true | 启用 DAG 调度 |
| `TASK_STALL_TIMEOUT` | 300 | 任务超时（秒） |
