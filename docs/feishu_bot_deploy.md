# 飞书 Bot Agent 部署指南

## 快速开始（5 步完成）

### Step 1：创建飞书应用

1. 打开[飞书开放平台](https://open.feishu.cn/app)，点击"创建企业自建应用"
2. 进入应用后，左侧导航 **添加应用能力 → 机器人**，开启机器人能力
3. 进入 **权限管理**，开启以下权限（生产版本需审核，开发阶段直接用测试企业）：
   - `im:message`（接收消息）
   - `im:message:send_as_bot`（发送消息）
   - `im:chat`（获取群信息）
4. 进入 **凭证与基础信息**，记录 `App ID` 和 `App Secret`
5. 进入 **事件与回调 → 事件配置**，记录 `Verification Token`（后面要填入环境变量）

---

### Step 2：配置环境变量

复制以下内容到 `config/agent.env`，填入真实值：

```bash
# ── SGA Hub 连接 ─────────────────────────────────────────────────
SGA_HUB_URL=http://your-hub-ip:9527
SGA_API_KEY=your-hub-api-key

# ── 飞书应用凭证 ──────────────────────────────────────────────────
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFY_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_ENCRYPT_KEY=                          # 可选，启用加密时填写

# ── Bot 服务配置 ──────────────────────────────────────────────────
BOT_WEBHOOK_PORT=8088
BOT_ALLOWED_CHAT_IDS=                        # 留空=所有群可用；多个群ID用逗号分隔

# ── 本地 LLM（用于自动拆解工作流子任务，可选）────────────────────
OLLAMA_BASE=http://localhost:11434
AGENT_MODEL=hermes3
```

---

### Step 3：安装依赖并启动

```bash
pip install httpx websockets fastapi uvicorn psutil

# 加载配置
export $(grep -v '^#' config/agent.env | xargs)

# 启动 Bot
python feishu_bot_agent.py
```

Bot 启动后会输出：
```
=== SGA 飞书 Bot Agent 启动 ===
Hub URL: http://your-hub:9527
Webhook 端口: 8088
已注册到 Hub: http://your-hub:9527 (id=xxxx:xxxxxx-feishu-bot)
Hub WebSocket 已连接
```

---

### Step 4：配置飞书事件接收地址

回到飞书开放平台 **事件与回调 → 事件配置**：

1. **请求地址**填入：`http://your-server-ip:8088/feishu/webhook`
   - 如果 Bot 在内网，需要先做内网穿透（ngrok / frp / Cloudflare Tunnel）
   - 飞书要求地址必须公网可访问

2. 点击"验证"，飞书会发送 challenge 请求，Bot 自动响应，验证通过后保存

3. 添加订阅事件：**消息与群组 → 接收消息 → `im.message.receive_v1`**

---

### Step 5：将 Bot 加入群聊

在飞书群里点右上角 **设置 → 成员 → 添加机器人**，搜索你的应用名称添加即可。

---

## 使用方法

Bot 加入群后，发送以下指令：

| 指令 | 说明 | 示例 |
|------|------|------|
| `/help` | 显示帮助 | `/help` |
| `/agents` | 查看在线 Agent | `/agents` |
| `/task <描述>` | 创建单个任务 | `/task 分析 main.py 的代码质量` |
| `/workflow <描述>` | 创建多步工作流 | `/workflow 完整的代码审查，包括静态分析和安全检查` |
| `/status <ID>` | 查询工作流进度 | `/status abc12345` |
| @机器人 + 任意文字 | 创建单任务（@方式） | `@Bot 帮我写一个排序算法` |

---

## 消息流转图

```
用户在飞书群发送指令
        │
        ▼
飞书服务器 POST → http://your-server:8088/feishu/webhook
        │
        ▼ parse_message()
识别指令类型（task / workflow / status / agents）
        │
        ├─ /task ──────→ POST /api/tasks         (Hub)
        │                       │
        ├─ /workflow ──→ LLM拆解子任务            (本地 Ollama，可选)
        │                       │
        │               POST /api/workflows       (Hub)
        │                       │
        └─ /status ────→ GET /api/workflows/{id}  (Hub)
                                │
Hub Orchestrator 调度各 Agent 执行
                                │
Hub WebSocket 广播 task_completed / workflow_completed
                                │
                        ▼ HubEventSubscriber
                飞书 Bot 发送结果卡片消息到群聊
```

---

## 内网穿透（本地部署时使用）

如果 Bot 部署在内网，飞书无法直接回调，需要做穿透：

**方案1：ngrok（临时开发用）**
```bash
ngrok http 8088
# 输出：https://xxxx.ngrok.io → 填入飞书事件请求地址
```

**方案2：frp（稳定生产用）**
```ini
# frpc.ini
[common]
server_addr = your-cloud-server-ip
server_port = 7000

[feishu-bot]
type = http
local_port = 8088
custom_domains = bot.your-domain.com
```

**方案3：Cloudflare Tunnel（免费稳定）**
```bash
cloudflared tunnel --url http://localhost:8088
```

---

## 生产环境建议

1. **进程守护**：用 `supervisor` 或 `systemd` 保持 Bot 进程存活
   ```ini
   # /etc/supervisor/conf.d/feishu-bot.conf
   [program:feishu-bot]
   command=python /path/to/feishu_bot_agent.py
   directory=/path/to/SGA_Multi-Agent
   environment=SGA_HUB_URL="http://hub:8000",SGA_API_KEY="xxx",FEISHU_APP_ID="xxx",...
   autostart=true
   autorestart=true
   stdout_logfile=/var/log/feishu-bot.log
   ```

2. **chat_id 持久化**：默认使用内存字典，重启后丢失任务→群的映射关系。
   生产环境在 `main()` 中将 `chat_id_map` 替换为 Redis 或 SQLite：
   ```python
   # 简单 SQLite 持久化示例
   import sqlite3
   class PersistentChatIdMap:
       def __init__(self, db_path="bot_state.db"):
           self.conn = sqlite3.connect(db_path, check_same_thread=False)
           self.conn.execute("CREATE TABLE IF NOT EXISTS mappings (id TEXT PRIMARY KEY, chat_id TEXT)")
       def __setitem__(self, k, v):
           self.conn.execute("INSERT OR REPLACE INTO mappings VALUES (?,?)", (k,v))
           self.conn.commit()
       def __getitem__(self, k):
           row = self.conn.execute("SELECT chat_id FROM mappings WHERE id=?", (k,)).fetchone()
           return row[0] if row else ""
       def get(self, k, default=""):
           return self[k] or default
   ```

3. **多群支持**：`BOT_ALLOWED_CHAT_IDS` 填入所有允许使用的群 ID，
   从飞书群设置页面的 URL 中获取（`chat_id=oc_xxxxxx`）

4. **加密事件**：生产环境建议在飞书后台开启事件加密，
   安装 `pycryptodome` 后 `FEISHU_ENCRYPT_KEY` 填入密钥即可自动解密

---

## 常见问题

**Q：Bot 收不到消息**
- 检查飞书后台事件请求地址是否验证通过（绿色对勾）
- 确认 `im.message.receive_v1` 事件已订阅
- 确认 Bot 已加入目标群聊
- 查看 Bot 日志确认 webhook 是否收到请求

**Q：任务创建成功但没有 Agent 执行**
- 发送 `/agents` 确认是否有在线 Agent
- 检查 Agent 的 `capabilities` 是否与任务的 `required_capabilities` 匹配
- 查看 Hub 看板的 Orchestrator 日志

**Q：工作流完成后没有收到飞书通知**
- 检查 Hub WebSocket 连接是否正常（Bot 日志中应有"Hub WebSocket 已连接"）
- 确认 `chat_id_map` 中存有对应的映射（重启 Bot 会丢失，需持久化）

**Q：飞书事件解密失败**
- 确认 `FEISHU_ENCRYPT_KEY` 与飞书后台配置一致
- 安装 `pycryptodome`：`pip install pycryptodome`
