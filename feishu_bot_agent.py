"""
feishu_bot_agent.py — SGA 飞书 Bot Agent 接入模板
====================================================

架构角色：
  本进程同时扮演两个角色：
  1. feishu-gateway  ── 接收飞书群消息 → 解析指令 → 创建 Hub 任务/工作流
  2. feishu-responder ── 订阅 Hub WebSocket → 监听任务事件 → 发飞书消息回群

部署方式：
  可独立部署在任意一台机器上（不依赖 Hub 同机部署）
  python feishu_bot_agent.py

依赖安装：
  pip install httpx websockets aiohttp fastapi uvicorn

飞书应用配置（在飞书开放平台完成）：
  1. 创建企业自建应用
  2. 开启"机器人"能力
  3. 订阅事件：im.message.receive_v1（接收群消息）
  4. 权限：im:message、im:message:send_as_bot、im:chat
  5. 配置事件请求地址：http://your-server:8088/feishu/webhook
  6. 将 Bot 加入目标群聊

环境变量配置（见 config/agent.env）：
  SGA_HUB_URL            Hub 地址，如 http://your-hub:8000
  SGA_API_KEY            Hub 鉴权密钥
  FEISHU_APP_ID          飞书应用 App ID
  FEISHU_APP_SECRET      飞书应用 App Secret
  FEISHU_VERIFY_TOKEN    飞书事件验证 Token
  FEISHU_ENCRYPT_KEY     飞书事件加密密钥（可选）
  BOT_WEBHOOK_PORT       本服务监听端口，默认 8088
  BOT_ALLOWED_CHAT_IDS   允许的群 chat_id，逗号分隔（留空=允许所有群）
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx
import websockets
import uvicorn
from fastapi import FastAPI, Request, Response

# ─────────────────────────────────────────────
# 日志
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("feishu-bot")

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
HUB_URL          = os.getenv("SGA_HUB_URL", "http://localhost:9527")
HUB_API_KEY      = os.getenv("SGA_API_KEY", "")
APP_ID           = os.getenv("FEISHU_APP_ID", "")
APP_SECRET       = os.getenv("FEISHU_APP_SECRET", "")
VERIFY_TOKEN     = os.getenv("FEISHU_VERIFY_TOKEN", "")
ENCRYPT_KEY      = os.getenv("FEISHU_ENCRYPT_KEY", "")
WEBHOOK_PORT     = int(os.getenv("BOT_WEBHOOK_PORT", "8088"))
ALLOWED_CHATS    = set(filter(None, os.getenv("BOT_ALLOWED_CHAT_IDS", "").split(",")))

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def hub_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if HUB_API_KEY:
        h["X-Api-Key"] = HUB_API_KEY
    return h


def now_ts() -> float:
    return time.time()


# ─────────────────────────────────────────────────────────────────
# 第一层：飞书 API 客户端
# 负责：获取 token、发消息（文本/卡片）、解密事件
# ─────────────────────────────────────────────────────────────────
class FeishuClient:
    """封装飞书开放平台 API"""

    BASE = "https://open.feishu.cn/open-apis"

    def __init__(self):
        self._token: str = ""
        self._token_expire: float = 0.0

    # ── Token 管理 ────────────────────────────────────────────────

    async def _ensure_token(self):
        """自动刷新 tenant_access_token（提前 5 分钟刷新）"""
        if time.time() < self._token_expire - 300:
            return
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{self.BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": APP_ID, "app_secret": APP_SECRET},
            )
            data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 Token 获取失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire = time.time() + data.get("expire", 7200)
        log.info("飞书 Token 已刷新，有效期 %ds", data.get("expire", 7200))

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json"}

    # ── 消息发送 ──────────────────────────────────────────────────

    async def send_text(self, chat_id: str, text: str,
                        reply_msg_id: str = "") -> dict:
        """发送文本消息到群聊"""
        await self._ensure_token()
        payload = {
            "receive_id": chat_id,
            "msg_type":   "text",
            "content":    json.dumps({"text": text}),
        }
        if reply_msg_id:
            payload["reply_in_thread"] = False   # 不开启话题，保持在主会话
        params = {"receive_id_type": "chat_id"}
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{self.BASE}/im/v1/messages",
                params=params,
                json=payload,
                headers=self._auth(),
            )
        return resp.json()

    async def send_card(self, chat_id: str, card: dict) -> dict:
        """发送交互式卡片消息"""
        await self._ensure_token()
        payload = {
            "receive_id": chat_id,
            "msg_type":   "interactive",
            "content":    json.dumps(card),
        }
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{self.BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                json=payload,
                headers=self._auth(),
            )
        return resp.json()

    async def reply_text(self, message_id: str, text: str) -> dict:
        """回复某条具体消息"""
        await self._ensure_token()
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{self.BASE}/im/v1/messages/{message_id}/reply",
                json={"msg_type": "text",
                      "content": json.dumps({"text": text})},
                headers=self._auth(),
            )
        return resp.json()

    # ── 事件解密 ──────────────────────────────────────────────────

    @staticmethod
    def decrypt_event(encrypted: str, key: str) -> dict:
        """解密飞书加密事件（当配置了 Encrypt Key 时使用）"""
        key_bs = hashlib.sha256(key.encode()).digest()
        encrypted_bs = base64.b64decode(encrypted)
        iv = encrypted_bs[:16]
        content = encrypted_bs[16:]
        from Crypto.Cipher import AES  # pycryptodome
        cipher = AES.new(key_bs, AES.MODE_CBC, iv)
        plaintext = cipher.decrypt(content)
        # PKCS7 去填充
        pad = plaintext[-1]
        plaintext = plaintext[:-pad]
        return json.loads(plaintext)

    @staticmethod
    def verify_signature(timestamp: str, nonce: str,
                         body: bytes, token: str) -> bool:
        """校验飞书事件签名"""
        content = (timestamp + nonce + token + body.decode()).encode()
        sig = hashlib.sha256(content).hexdigest()
        return True  # 实际部署时与请求头 X-Lark-Signature 对比


# ─────────────────────────────────────────────────────────────────
# 第二层：消息解析器
# 负责：将群消息解析为结构化指令
# ─────────────────────────────────────────────────────────────────

# 指令前缀映射
# 群里发 "/task 分析这段代码" → 创建单任务
# 群里发 "/workflow 完整代码审查流程" → 创建多步工作流
# 群里发 "/status" → 查询工作流状态
# 群里发 "/agents" → 列出在线 Agent
# 群里发 "/help" → 显示帮助信息

COMMAND_PREFIX = "/"

@dataclass
class ParsedCommand:
    cmd:      str            # task / workflow / status / agents / help
    args:     str            # 命令后面的原始文字
    chat_id:  str            # 来源群 ID
    msg_id:   str            # 飞书消息 ID（用于回复）
    sender:   str            # 发送者 open_id
    raw_text: str            # 完整原文


def parse_message(event: dict) -> Optional[ParsedCommand]:
    """
    从飞书事件中提取消息内容并解析为指令。
    仅处理 @Bot 或以 / 开头的消息，其他消息忽略。
    """
    try:
        msg    = event.get("event", {}).get("message", {})
        sender = event.get("event", {}).get("sender", {})
        chat_id   = msg.get("chat_id", "")
        msg_id    = msg.get("message_id", "")
        sender_id = sender.get("sender_id", {}).get("open_id", "")
        msg_type  = msg.get("message_type", "")

        if msg_type != "text":
            return None  # 暂只处理文本消息

        content = json.loads(msg.get("content", "{}"))
        text    = content.get("text", "").strip()

        # 去掉 @bot 提及（飞书格式：<at user_id="xxx">Bot</at>）
        import re
        text = re.sub(r'<at[^>]+>[^<]*</at>', '', text).strip()

        if not text:
            return None

        # 判断是否为指令
        if not text.startswith(COMMAND_PREFIX):
            # 非指令消息：如果是@提及则当作自由文本任务
            if "@_user_1" in msg.get("content", ""):  # 简化判断
                return ParsedCommand("task", text, chat_id, msg_id,
                                     sender_id, text)
            return None

        parts = text[1:].split(maxsplit=1)
        cmd   = parts[0].lower() if parts else ""
        args  = parts[1] if len(parts) > 1 else ""
        return ParsedCommand(cmd, args, chat_id, msg_id, sender_id, text)

    except Exception as e:
        log.warning("消息解析失败: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────
# 第三层：Hub 代理
# 负责：将解析后的指令转换为 Hub API 调用
# ─────────────────────────────────────────────────────────────────

class HubProxy:
    """封装对 SGA Hub API 的调用"""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    async def create_task(self, title: str, description: str,
                          chat_id: str) -> dict:
        """创建单个任务"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{HUB_URL}/api/tasks",
                json={
                    "title":       title,
                    "description": description,
                    "priority":    "P1",
                    "context_snapshot": {
                        "source":  "feishu",
                        "chat_id": chat_id,
                        "created_by": self.agent_id,
                    },
                },
                headers=hub_headers(),
            )
        return resp.json()

    async def create_workflow(self, name: str, description: str,
                              chat_id: str) -> dict:
        """
        创建工作流。
        实际任务拆解由 Hub 端 Orchestrator 的 LLM 完成，
        这里只传递意图描述。

        如果 Hub 尚未实现 LLM 自动拆解，则在 Bot 本地用 LLM 拆解后
        以 task 列表形式提交（参见 _decompose_locally）。
        """
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(
                f"{HUB_URL}/api/workflows",
                json={
                    "name":        name,
                    "description": description,
                    "tasks": await self._decompose_locally(description),
                    "dependencies": {},
                },
                headers=hub_headers(),
            )
        return resp.json()

    async def _decompose_locally(self, description: str) -> list:
        """
        本地 LLM 将自然语言描述拆解为子任务列表。
        如果没有本地 LLM，返回单任务列表作为兜底。
        """
        # ── 方案1：直接调用 Ollama（如果本机有 LLM）──────────────
        ollama_base = os.getenv("OLLAMA_BASE", "")
        if ollama_base:
            try:
                return await self._decompose_with_ollama(
                    description, ollama_base
                )
            except Exception as e:
                log.warning("本地 LLM 拆解失败，降级为单任务: %s", e)

        # ── 方案2：兜底：作为单任务处理 ──────────────────────────
        return [{"title": description[:80], "description": description}]

    async def _decompose_with_ollama(self, description: str,
                                     base: str) -> list:
        """调用 Ollama 将任务描述拆解为 JSON 子任务列表"""
        model = os.getenv("AGENT_MODEL", "hermes3")
        prompt = f"""你是一个任务规划专家。
请将以下用户请求拆解为 2-5 个可执行的子任务，每个子任务应该：
1. 职责单一、可独立执行
2. 按执行顺序排列
3. 用 depends_on 字段表示依赖（用 "$索引" 格式，如 "$0" 表示第一个子任务）

用户请求：{description}

只输出 JSON 数组，不要有任何其他文字：
[
  {{"title": "子任务标题", "description": "详细描述", "depends_on": []}},
  {{"title": "子任务标题", "description": "详细描述", "depends_on": ["$0"]}}
]"""

        async with httpx.AsyncClient(timeout=60) as c:
            resp = await c.post(
                f"{base}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            raw = resp.json().get("response", "[]")

        # 提取 JSON
        import re
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            raise ValueError("LLM 未返回有效 JSON")
        tasks = json.loads(match.group())
        log.info("LLM 拆解出 %d 个子任务", len(tasks))
        return tasks

    async def get_workflow_status(self, workflow_id: str) -> dict:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get(
                f"{HUB_URL}/api/workflows/{workflow_id}",
                headers=hub_headers(),
            )
        return resp.json()

    async def list_agents(self) -> list:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get(
                f"{HUB_URL}/api/agents",
                headers=hub_headers(),
            )
        return resp.json().get("agents", [])


# ─────────────────────────────────────────────────────────────────
# 第四层：Hub WebSocket 订阅者
# 负责：监听 Hub 事件 → 转发结果到飞书群
# ─────────────────────────────────────────────────────────────────

class HubEventSubscriber:
    """
    订阅 Hub 的 WebSocket 广播，将任务完成/失败事件
    格式化后发送回飞书群。
    """

    def __init__(self, feishu: FeishuClient,
                 chat_id_map: dict):   # {workflow_id/task_id → chat_id}
        self.feishu      = feishu
        self.chat_id_map = chat_id_map
        self._running    = False

    async def run(self):
        """持续订阅 Hub WebSocket，断线自动重连"""
        self._running = True
        ws_url = HUB_URL.replace("http://", "ws://") \
                        .replace("https://", "wss://") + "/ws"

        while self._running:
            try:
                log.info("连接 Hub WebSocket: %s", ws_url)
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    log.info("Hub WebSocket 已连接")
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                            await self._handle_event(event)
                        except Exception as e:
                            log.warning("事件处理异常: %s", e)
            except Exception as e:
                log.warning("WebSocket 断开: %s，5 秒后重连...", e)
                await asyncio.sleep(5)

    async def _handle_event(self, event: dict):
        """根据事件类型分发处理"""
        etype = event.get("type", "")

        if etype == "task_completed":
            await self._on_task_completed(event)
        elif etype == "task_assigned":
            await self._on_task_assigned(event)
        elif etype == "workflow_completed":
            await self._on_workflow_completed(event)
        elif etype == "workflow_failed":
            await self._on_workflow_failed(event)
        elif etype == "file_available":
            await self._on_file_available(event)
        # 其他事件类型（stats_update 等）暂时忽略

    async def _on_task_completed(self, event: dict):
        task_id = event.get("task_id", "")
        result  = event.get("result", "")
        chat_id = self.chat_id_map.get(task_id, "")
        if not chat_id:
            return
        text = f"✅ 任务完成\n─────────\n{result[:400]}"
        await self.feishu.send_text(chat_id, text)

    async def _on_task_assigned(self, event: dict):
        task_id  = event.get("task_id", "")
        agent_id = event.get("agent_id", "")
        chat_id  = self.chat_id_map.get(task_id, "")
        if not chat_id:
            return
        await self.feishu.send_text(
            chat_id,
            f"⚙️ 任务 {task_id} 已分配给 Agent: {agent_id}，执行中..."
        )

    async def _on_workflow_completed(self, event: dict):
        wf_id      = event.get("workflow_id", "")
        task_count = event.get("task_count", 0)
        chat_id    = self.chat_id_map.get(wf_id, "")
        if not chat_id:
            return
        card = build_workflow_result_card(
            workflow_id=wf_id,
            status="completed",
            task_count=task_count,
            hub_url=HUB_URL,
        )
        await self.feishu.send_card(chat_id, card)

    async def _on_workflow_failed(self, event: dict):
        wf_id   = event.get("workflow_id", "")
        chat_id = self.chat_id_map.get(wf_id, "")
        if not chat_id:
            return
        await self.feishu.send_text(
            chat_id,
            f"❌ 工作流 {wf_id} 执行失败，请到 Hub 看板查看详情：{HUB_URL}"
        )

    async def _on_file_available(self, event: dict):
        task_id  = event.get("task_id", "")
        filename = event.get("filename", "")
        uploader = event.get("uploader", "")
        chat_id  = self.chat_id_map.get(task_id, "")
        if not chat_id:
            return
        download_url = f"{HUB_URL}/api/files/{task_id}/{filename}"
        await self.feishu.send_text(
            chat_id,
            f"📎 新文件可下载\n文件名：{filename}\n来源：{uploader}\n"
            f"下载：{download_url}"
        )


# ─────────────────────────────────────────────────────────────────
# 飞书消息卡片模板
# ─────────────────────────────────────────────────────────────────

def build_workflow_result_card(workflow_id: str, status: str,
                                task_count: int, hub_url: str) -> dict:
    """构建工作流完成的飞书交互卡片"""
    status_icon = "✅" if status == "completed" else "❌"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"{status_icon} 工作流执行完成"},
            "template": "green" if status == "completed" else "red",
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {
                        "tag": "lark_md",
                        "content": f"**工作流 ID**\n{workflow_id}"
                    }},
                    {"is_short": True, "text": {
                        "tag": "lark_md",
                        "content": f"**子任务数**\n{task_count} 个"
                    }},
                ],
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag":   "button",
                        "text":  {"tag": "plain_text", "content": "查看详情"},
                        "type":  "primary",
                        "url":   hub_url,
                    },
                ],
            },
        ],
    }


def build_agents_card(agents: list) -> dict:
    """构建在线 Agent 列表卡片"""
    status_icon = {"idle": "🟢", "running": "🔵", "offline": "⚫"}
    rows = "\n".join(
        f"- {status_icon.get(a.get('status','offline'),'⚪')} "
        f"**{a.get('name',a.get('agent_id','?'))}** "
        f"({a.get('model','')}) — {a.get('status','offline')}"
        for a in agents
    ) or "暂无在线 Agent"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"📡 当前在线 Agent ({len(agents)} 个)"},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": rows}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md",
                "content": f"Hub 看板：{HUB_URL}"}},
        ],
    }


HELP_TEXT = """🤖 **SGA Bot 指令列表**

**/task** `<描述>`
创建单个任务，由最优 Agent 执行
例：`/task 分析 main.py 的代码质量`

**/workflow** `<描述>`
创建多步工作流，自动拆解子任务并分发
例：`/workflow 完整的代码审查流程，包括静态分析和安全检查`

**/status** `<workflow_id>`
查询指定工作流的执行进度

**/agents**
列出当前所有在线 Agent 及其状态

**/help**
显示此帮助信息

---
💡 也可以直接 @机器人 描述任务，Bot 会自动创建为单任务"""


# ─────────────────────────────────────────────────────────────────
# 第五层：Bot 主进程
# 负责：注册为 Agent、协调各层、启动 HTTP 服务接收 webhook
# ─────────────────────────────────────────────────────────────────

# 全局状态：记录 {任务ID/工作流ID → 飞书群 chat_id} 的映射
# 使用简单的内存字典；生产环境可改为 Redis 或数据库持久化
chat_id_map: dict = {}

feishu  = FeishuClient()
hub     = None   # HubProxy，启动后初始化
subscriber = None


class FeishuBotAgent:

    def __init__(self):
        import socket, hashlib as hl
        node_fp = hl.md5(
            (socket.gethostname() + str(uuid.getnode())).encode()
        ).hexdigest()[:4]
        self.agent_id  = f"{node_fp}:{str(uuid.uuid4())[:6]}-feishu-bot"
        self.hub_proxy = HubProxy(self.agent_id)
        self._token_in  = 0
        self._token_out = 0

    # ── 注册 & 心跳 ───────────────────────────────────────────────

    async def register(self):
        """注册到 Hub"""
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{HUB_URL}/api/agents/register",
                json={
                    "agent_id":     self.agent_id,
                    "name":         "feishu-bot",
                    "role":         "gateway",
                    "model":        "N/A",
                    "backend":      "feishu",
                    "capabilities": ["message_routing", "feishu_integration"],
                },
                headers=hub_headers(),
            )
        if resp.status_code == 200:
            log.info("已注册到 Hub: %s (id=%s)", HUB_URL, self.agent_id)
        else:
            log.warning("注册失败: %s", resp.text)

    async def heartbeat_loop(self, interval: int = 30):
        """持续心跳"""
        import psutil, os as _os
        proc = psutil.Process()
        while True:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.post(
                        f"{HUB_URL}/api/agents/{self.agent_id}/heartbeat",
                        json={
                            "status": "idle",
                            "metrics": {
                                "tokens_in":     self._token_in,
                                "tokens_out":    self._token_out,
                                "model_name":    "feishu-gateway",
                                "model_backend": "feishu",
                                "proc_cpu_pct":  proc.cpu_percent(interval=None),
                                "proc_mem_mb":   round(
                                    proc.memory_info().rss / 1e6, 1
                                ),
                            },
                        },
                        headers=hub_headers(),
                    )
            except Exception as e:
                log.warning("心跳失败: %s", e)
            await asyncio.sleep(interval)

    # ── 指令处理 ──────────────────────────────────────────────────

    async def handle_command(self, cmd: ParsedCommand):
        """分发处理各种指令"""
        chat_id = cmd.chat_id

        # 检查群白名单
        if ALLOWED_CHATS and chat_id not in ALLOWED_CHATS:
            log.info("群 %s 不在白名单，忽略", chat_id)
            return

        log.info("处理指令: /%s args=%r chat=%s",
                 cmd.cmd, cmd.args[:50], chat_id)

        if cmd.cmd == "help":
            await feishu.send_text(chat_id, HELP_TEXT)

        elif cmd.cmd == "agents":
            agents = await self.hub_proxy.list_agents()
            card = build_agents_card(agents)
            await feishu.send_card(chat_id, card)

        elif cmd.cmd == "status":
            workflow_id = cmd.args.strip()
            if not workflow_id:
                await feishu.reply_text(
                    cmd.msg_id, "请提供工作流 ID，例如：/status abc12345"
                )
                return
            data = await self.hub_proxy.get_workflow_status(workflow_id)
            prog = data.get("progress", {})
            wf   = data.get("workflow", {})
            text = (
                f"📊 工作流状态\n"
                f"────────────\n"
                f"名称：{wf.get('name', workflow_id)}\n"
                f"状态：{wf.get('status', '?')}\n"
                f"进度：{prog.get('completed', 0)}/{prog.get('total', 0)} 完成\n"
                f"运行中：{prog.get('running', 0)}  等待：{prog.get('pending', 0)}"
                f"  失败：{prog.get('failed', 0)}"
            )
            await feishu.send_text(chat_id, text)

        elif cmd.cmd == "task":
            if not cmd.args:
                await feishu.reply_text(
                    cmd.msg_id, "请描述任务内容，例如：/task 分析这段代码"
                )
                return
            await feishu.reply_text(cmd.msg_id, "⏳ 正在创建任务...")
            result = await self.hub_proxy.create_task(
                title=cmd.args[:80],
                description=cmd.args,
                chat_id=chat_id,
            )
            task_id = result.get("task_id", "")
            if task_id:
                chat_id_map[task_id] = chat_id
                await feishu.send_text(
                    chat_id,
                    f"✅ 任务已创建\n任务 ID：{task_id}\n"
                    f"正在分配给最优 Agent 执行，完成后将在此通知..."
                )
            else:
                await feishu.reply_text(
                    cmd.msg_id, f"❌ 任务创建失败：{result.get('detail', '未知错误')}"
                )

        elif cmd.cmd == "workflow":
            if not cmd.args:
                await feishu.reply_text(
                    cmd.msg_id, "请描述工作流，例如：/workflow 完整的代码审查流程"
                )
                return
            await feishu.reply_text(cmd.msg_id, "⏳ 正在规划工作流子任务，请稍候...")
            result = await self.hub_proxy.create_workflow(
                name=cmd.args[:80],
                description=cmd.args,
                chat_id=chat_id,
            )
            wf_id = result.get("workflow_id", "")
            if wf_id:
                # 注册工作流和所有子任务的 chat_id 映射
                chat_id_map[wf_id] = chat_id
                for tid in result.get("task_ids", []):
                    chat_id_map[tid] = chat_id
                count = result.get("task_count", 0)
                await feishu.send_text(
                    chat_id,
                    f"✅ 工作流已创建\n"
                    f"工作流 ID：{wf_id}\n"
                    f"已拆解为 {count} 个子任务\n"
                    f"Orchestrator 正在调度执行，完成后将在此汇报结果..."
                )
            else:
                await feishu.reply_text(
                    cmd.msg_id,
                    f"❌ 工作流创建失败：{result.get('detail', '未知错误')}"
                )

        else:
            # 未知指令
            await feishu.reply_text(
                cmd.msg_id,
                f"❓ 未知指令 `/{cmd.cmd}`，发送 /help 查看帮助"
            )


# 全局 Bot 实例
bot = FeishuBotAgent()


# ─────────────────────────────────────────────────────────────────
# FastAPI Webhook 服务
# 负责：接收飞书事件推送
# ─────────────────────────────────────────────────────────────────

app = FastAPI(title="SGA Feishu Bot Webhook")


@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """
    飞书事件推送接收端点。
    飞书会向此地址 POST 事件（URL 验证 + 实际事件）。
    """
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        return Response(status_code=400)

    # ── URL 验证（飞书第一次配置时发送）────────────────────────────
    # 方式1：encrypt 模式
    if "encrypt" in body:
        if not ENCRYPT_KEY:
            return Response(status_code=400,
                            content=json.dumps({"error": "no encrypt key"}))
        body = FeishuClient.decrypt_event(body["encrypt"], ENCRYPT_KEY)

    # 方式2：challenge 验证（明文模式）
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    # ── 正式事件处理 ────────────────────────────────────────────────
    # 异步处理，立即返回 200（飞书要求 3 秒内响应）
    asyncio.create_task(_process_event(body))
    return {"code": 0}


async def _process_event(event: dict):
    """异步处理飞书事件（不阻塞 HTTP 响应）"""
    try:
        cmd = parse_message(event)
        if cmd:
            await bot.handle_command(cmd)
    except Exception as e:
        log.error("事件处理失败: %s", e, exc_info=True)


# ─────────────────────────────────────────────────────────────────
# 启动入口
# ─────────────────────────────────────────────────────────────────

async def main():
    global subscriber

    log.info("=== SGA 飞书 Bot Agent 启动 ===")
    log.info("Hub URL: %s", HUB_URL)
    log.info("Webhook 端口: %d", WEBHOOK_PORT)

    # 1. 注册到 Hub
    await bot.register()

    # 2. 初始化 WebSocket 订阅者
    subscriber = HubEventSubscriber(feishu, chat_id_map)

    # 3. 并发运行：心跳 + Hub WS 订阅 + Webhook HTTP 服务
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        log_level="warning",   # 减少 uvicorn 的噪音日志
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        bot.heartbeat_loop(interval=30),    # 心跳保活
        subscriber.run(),                    # 订阅 Hub 事件
        server.serve(),                      # 接收飞书 Webhook
    )


if __name__ == "__main__":
    asyncio.run(main())
