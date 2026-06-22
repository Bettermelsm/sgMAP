# sgMAP 业务代码部署说明

## 文件清单

```
sgmap_code/
├── hermes_orchestrator.py   # Hermes Orchestrator（部署到 SGlcl02）
├── worker_agent_template.py # Worker Agent 模板（各计算节点按需修改）
├── .env.template            # 环境变量模板（复制为 .env 后填写）
├── skcl/                    # 复制到 sgMAP 主仓库根目录
│   ├── AGENTS.md            # 节点能力注册表（★ 最重要的配置文件）
│   ├── skills/
│   │   └── skill_template.md
│   └── wiki/
│       ├── index.md
│       └── log.md
└── README_deploy.md         # 本文件
```

---

## 部署步骤

### Step 1：将文件放入 sgMAP 仓库

```bash
cd /path/to/sgMAP

# 复制 SKCL 目录结构
cp -r /path/to/sgmap_code/skcl ./

# 复制 Orchestrator 和 Worker 模板
cp /path/to/sgmap_code/hermes_orchestrator.py ./
cp /path/to/sgmap_code/worker_agent_template.py ./

# 复制环境变量模板
cp /path/to/sgmap_code/.env.template ./.env
```

### Step 2：配置环境变量（SGlcl02）

```bash
# 编辑 .env，填入实际值
vim .env

# 加载环境变量
export $(cat .env | grep -v '^#' | xargs)
```

### Step 3：启动 Hermes Orchestrator（SGlcl02）

```bash
cd /path/to/sgMAP
python3 hermes_orchestrator.py
```

生产模式（systemd）：

```bash
sudo tee /etc/systemd/system/sgmap-orchestrator.service << EOF
[Unit]
Description=sgMAP Hermes Orchestrator
After=network.target

[Service]
User=$USER
WorkingDirectory=/path/to/sgMAP
EnvironmentFile=/path/to/sgMAP/.env
ExecStart=$(which python3) hermes_orchestrator.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable sgmap-orchestrator
sudo systemctl start sgmap-orchestrator
journalctl -u sgmap-orchestrator -f
```

### Step 4：各计算节点部署 Worker Agent

```bash
# 以 SGlcl01 为例
cd /path/to/sgMAP
cp worker_agent_template.py worker_agent_SGlcl01.py

# 编辑修改以下字段：
# NODE_NAME    = "SGlcl01@sengene"
# NODE_ROLE    = "analyzer"
# CAPABILITIES = ["数据分析", "scRNA-seq", "WGCNA", "分子对接", "生信计算", "统计分析"]
# 并在 execute_task() 中实现实际分析脚本的调用

python3 worker_agent_SGlcl01.py
```

各节点的 `NODE_NAME`、`NODE_ROLE`、`CAPABILITIES` 必须与 `skcl/AGENTS.md` 中完全一致。

### Step 5：配置 SKCL GitHub 同步（可选）

```bash
# 通知 Hub 同步 SKCL（sgMAP 主仓库已包含 skcl/）
curl -X POST http://<Hub_IP>:9527/api/shared/sync \
  -H "X-API-Key: $SGA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"github_repo": "https://github.com/Bettermelsm/SGA_Multi-Agent", "branch": "master"}'
```

---

## 关键配置文件：skcl/AGENTS.md

这是整个系统最重要的配置文件。Hermes Orchestrator 读取它来了解：
- 每个节点叫什么名字
- 每个节点有哪些能力（capabilities）
- 哪些任务不能路由到哪些节点

**修改后无需重启 Orchestrator**，它每 30 分钟自动刷新。

---

## 验证部署

```bash
# 1. 确认 Hub 已运行
curl http://<Hub_IP>:9527/health

# 2. 查看已注册 Agent
curl -H "X-API-Key: $SGA_API_KEY" http://<Hub_IP>:9527/api/agents
# 应该能看到：Hermes-Orchestrator@SGlcl02 和各 Worker 节点

# 3. 测试发送任务（模拟通讯工具消息）
curl -X POST http://<Hub_IP>:9527/api/tasks \
  -H "X-API-Key: $SGA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "测试任务",
    "description": "请分析 GEO 数据集 GSE123456，执行 WGCNA 分析",
    "priority": "P2"
  }'

# 4. 观察 Orchestrator 日志，应该看到 LLM 规划并分发任务
journalctl -u sgmap-orchestrator -f
```

---

## 常见问题

**Q: Ollama 连接失败（192.168.100.209:11434）**
A: 确认 Ollama 已在目标机器启动，且 `OLLAMA_HOST=0.0.0.0` 已设置（默认只监听 localhost）：
```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

**Q: MiniMax API 返回鉴权错误**
A: 检查 `.env` 中的 `MINIMAX_API_KEY` 和 `MINIMAX_GROUP_ID` 是否正确。
Group ID 在 MiniMax 控制台 → 账户信息中查看。

**Q: Orchestrator 注册失败（Hub 连不上）**
A: 确认 Hub 已启动，且 `SGA_HUB_URL` 指向的地址从 SGlcl02 可访问：
```bash
curl http://192.168.100.209:9527/health
```
