"""
hermes_orchestrator.py
======================
sgMAP Hermes Orchestrator Agent
运行在 SGlcl02（Agent 侧），注册到 sgMAP Hub 后：
  1. 监听 Hub 下发的任务（包含来自通讯工具的自然语言消息）
  2. 读取 skcl/AGENTS.md 了解集群能力分布
  3. 调用本地 Ollama（Qwen 35B）解析意图 → 生成结构化任务 → 路由给对应 Worker
  4. 汇总结果后回复原始消息来源
  5. 本地 Ollama 失败时自动 fallback 到 MiniMax API

部署位置：SGlcl02
运行方式：python3 hermes_orchestrator.py
"""

import asyncio
import httpx
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 从同目录引入 sgMAP Agent SDK ──────────────────────────────────────────
from agent_sdk import AgentClient

# ═══════════════════════════════════════════════════════════════════════════
# 配置区（所有可调参数集中在此处）
# ═══════════════════════════════════════════════════════════════════════════

# sgMAP Hub 连接配置（兼容 SGA_* 和 SGMAP_* 两种命名，SGA_* 优先）
HUB_URL: str = os.getenv("SGA_HUB_URL") or os.getenv("SGMAP_HUB_URL", "http://192.168.100.209:9527")
HUB_API_KEY: str = os.getenv("SGA_API_KEY") or os.getenv("SGMAP_API_KEY", "your-sgmap-api-key-here")

# 本地 Ollama 配置（运行 35B 模型的机器）
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://192.168.100.209:11434")
OLLAMA_MODEL: str = os.getenv(
    "OLLAMA_MODEL",
    "Jarcgon/Qwen3.6-35B-A3B-Claude-4.7-Opus-abliterated-uncenfull"
)
OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))  # 秒，35B 模型推理较慢

# MiniMax Fallback 配置
MINIMAX_API_KEY: str = os.getenv("MINIMAX_API_KEY", "YOUR_MINIMAX_API_KEY_HERE")
MINIMAX_GROUP_ID: str = os.getenv("MINIMAX_GROUP_ID", "YOUR_MINIMAX_GROUP_ID_HERE")
MINIMAX_MODEL: str = os.getenv("MINIMAX_MODEL", "abab6.5s-chat")
MINIMAX_BASE_URL: str = "https://api.minimax.chat/v1/text/chatcompletion_pro"

# Orchestrator 自身配置
ORCHESTRATOR_NAME: str = "Hermes-Orchestrator@SGlcl02"
ORCHESTRATOR_ROLE: str = "coder"  # Orchestrator 以 coder role 注册到 Hub
HEARTBEAT_INTERVAL: int = 15       # 秒
AGENTS_MD_PATH: Path = Path(__file__).parent / "skcl" / "AGENTS.md"
AGENTS_MD_REFRESH_INTERVAL: int = 1800  # 秒，每 30 分钟重新读取 AGENTS.md

# 任务轮询间隔
INBOX_POLL_INTERVAL: int = 5  # 秒

# ═══════════════════════════════════════════════════════════════════════════
# 日志配置
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("HermesOrchestrator")


# ═══════════════════════════════════════════════════════════════════════════
# LLM 客户端：本地 Ollama + MiniMax Fallback
# ═══════════════════════════════════════════════════════════════════════════

class LLMClient:
    """
    统一 LLM 调用接口。
    优先使用本地 Ollama（Qwen 35B），失败时自动切换 MiniMax。
    """

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=OLLAMA_TIMEOUT)

    async def chat(self, system: str, user: str) -> str:
        """发送对话请求，返回模型回复文本。"""
        try:
            reply = await self._ollama_chat(system, user)
            log.info("LLM: 使用本地 Ollama 完成推理")
            return reply
        except Exception as e:
            log.warning(f"LLM: Ollama 调用失败（{e}），切换 MiniMax fallback")
            return await self._minimax_chat(system, user)

    async def _ollama_chat(self, system: str, user: str) -> str:
        """调用本地 Ollama API（OpenAI 兼容格式）。"""
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": 0.3,   # 调度任务要求稳定输出，低温度
                "num_predict": 2048,
            },
        }
        resp = await self._http.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()

    async def _minimax_chat(self, system: str, user: str) -> str:
        """调用 MiniMax API（Pro 格式）。"""
        payload = {
            "model": MINIMAX_MODEL,
            "messages": [
                {"sender_type": "USER", "sender_name": "sgMAP", "text": user},
            ],
            "bot_setting": [
                {
                    "bot_name": "Hermes",
                    "content": system,
                }
            ],
            "reply_constraints": {"sender_type": "BOT", "sender_name": "Hermes"},
            "temperature": 0.3,
            "tokens_to_generate": 2048,
        }
        headers = {
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        }
        url = f"{MINIMAX_BASE_URL}?GroupId={MINIMAX_GROUP_ID}"
        resp = await self._http.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["reply"].strip()

    async def close(self):
        await self._http.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# AGENTS.md 读取与缓存
# ═══════════════════════════════════════════════════════════════════════════

class AgentsRegistry:
    """
    读取并缓存 skcl/AGENTS.md，定期自动刷新。
    为 LLM 提供结构化的集群能力上下文。
    """

    def __init__(self, path: Path, refresh_interval: int = AGENTS_MD_REFRESH_INTERVAL):
        self._path = path
        self._refresh_interval = refresh_interval
        self._content: str = ""
        self._last_loaded: float = 0.0

    def get(self) -> str:
        """获取 AGENTS.md 内容，必要时自动刷新。"""
        now = time.monotonic()
        if now - self._last_loaded > self._refresh_interval or not self._content:
            self._load()
        return self._content

    def _load(self):
        if not self._path.exists():
            log.warning(f"AGENTS.md 未找到：{self._path}，使用空上下文")
            self._content = "（暂无节点能力注册表，请创建 skcl/AGENTS.md）"
        else:
            self._content = self._path.read_text(encoding="utf-8")
            log.info(f"已刷新 AGENTS.md（{len(self._content)} 字符）")
        self._last_loaded = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# 任务规划：LLM 解析自然语言 → 结构化任务列表
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT_TEMPLATE = """你是 sgMAP 平台的 Hermes Orchestrator，负责将用户的自然语言请求转化为结构化任务，并分配给最合适的 Agent 节点执行。

## 当前集群节点能力

{agents_md}

## 任务分配规则

1. 实时任务（BCI、ROS2 控制）：必须分配给本地 GPU 节点，禁止路由到云端
2. 内存密集型任务（scRNA-seq、WGCNA）：分配给内存 ≥ 64GB 的节点
3. 轻量定时任务：优先分配给云端节点，节省本地算力
4. 大文件传输（>500MB）：通过 Syncthing 同步目录，在任务 description 中注明

## 输出格式要求

你必须只输出一个合法的 JSON 数组，不要有任何额外文字或 Markdown 代码块。
格式如下：

[
  {{
    "title": "简短任务标题（≤30字）",
    "description": "详细任务描述，包含具体执行步骤和预期输出",
    "priority": "P0|P1|P2|P3",
    "required_capabilities": ["能力1", "能力2"],
    "assigned_node": "节点名称（与 AGENTS.md 中一致）",
    "estimated_minutes": 预估执行分钟数
  }}
]

P0=紧急，P1=高，P2=普通，P3=低。
如果一个请求需要拆分为多个子任务，输出多个对象。
如果请求不需要计算任务（如闲聊），输出空数组 []。
"""


async def plan_tasks(
    llm: LLMClient,
    registry: AgentsRegistry,
    user_message: str,
    source: str = "unknown",
) -> list[dict]:
    """
    调用 LLM 将自然语言消息解析为结构化任务列表。
    返回任务列表，解析失败时返回空列表。
    """
    system = SYSTEM_PROMPT_TEMPLATE.format(agents_md=registry.get())
    user = f"[来源: {source}]\n{user_message}"

    log.info(f"规划任务中，消息来源={source}，长度={len(user_message)}")
    raw = await llm.chat(system, user)
    log.debug(f"LLM 原始输出:\n{raw}")

    # 容错解析：LLM 可能在 JSON 外包裹文字
    tasks = _parse_json_tasks(raw)
    log.info(f"规划完成，共 {len(tasks)} 个任务")
    return tasks


def _parse_json_tasks(raw: str) -> list[dict]:
    """从 LLM 输出中鲁棒地提取 JSON 数组。"""
    # 尝试直接解析
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取第一个 [...] 块
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    log.warning("无法解析 LLM 输出为 JSON 任务列表，返回空列表")
    log.debug(f"原始输出：{raw[:500]}")
    return []


# ═══════════════════════════════════════════════════════════════════════════
# 结果汇总：等待子任务完成并汇总回复
# ═══════════════════════════════════════════════════════════════════════════

async def wait_for_tasks(
    agent: AgentClient,
    task_ids: list[str],
    timeout: int = 600,
) -> dict[str, dict]:
    """
    等待所有子任务完成（或超时）。
    返回 {task_id: task_info} 字典。
    """
    results = {}
    deadline = asyncio.get_event_loop().time() + timeout
    pending = set(task_ids)

    while pending and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(10)
        for tid in list(pending):
            try:
                async with httpx.AsyncClient() as http:
                    resp = await http.get(
                        f"{HUB_URL}/api/tasks/{tid}",
                        headers={"X-API-Key": HUB_API_KEY},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        task = resp.json()
                        status = task.get("status", "")
                        if status in ("completed", "failed", "cancelled"):
                            results[tid] = task
                            pending.discard(tid)
                            log.info(f"任务 {tid} 完成，状态={status}")
            except Exception as e:
                log.warning(f"查询任务 {tid} 状态失败: {e}")

    # 超时的任务标记为 timeout
    for tid in pending:
        results[tid] = {"task_id": tid, "status": "timeout", "result": "执行超时"}
        log.warning(f"任务 {tid} 执行超时")

    return results


def _summarize_results(
    tasks_plan: list[dict],
    results: dict[str, dict],
) -> str:
    """将多个子任务结果汇总为自然语言回复。"""
    if not tasks_plan:
        return "收到，这条消息不需要执行任务。"

    lines = [f"已完成 {len(tasks_plan)} 个任务：\n"]
    for i, task in enumerate(tasks_plan, 1):
        tid = task.get("_task_id", "unknown")
        result_info = results.get(tid, {})
        status = result_info.get("status", "unknown")
        result_text = result_info.get("result", "无结果")
        node = task.get("assigned_node", "未知节点")

        status_emoji = {"completed": "✅", "failed": "❌", "timeout": "⏰"}.get(status, "❓")
        lines.append(f"{i}. {status_emoji} **{task['title']}**（{node}）")
        if status == "completed":
            lines.append(f"   结果：{str(result_text)[:200]}")
        elif status == "failed":
            lines.append(f"   失败原因：{str(result_text)[:200]}")
        elif status == "timeout":
            lines.append(f"   任务已提交但超时未返回，请稍后查看 Hub 看板")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 核心处理循环
# ═══════════════════════════════════════════════════════════════════════════

async def process_inbox_item(
    agent: AgentClient,
    llm: LLMClient,
    registry: AgentsRegistry,
    item: dict,
):
    """处理收件箱中的一条消息或任务。"""
    # 兼容两种来源：通讯工具消息 / Hub 直接下发的任务
    content = (
        item.get("content")
        or item.get("description")
        or item.get("title")
        or ""
    )
    source = item.get("source", item.get("sender_name", "hub"))
    item_id = item.get("id") or item.get("task_id") or "unknown"

    if not content.strip():
        log.debug(f"忽略空内容消息 {item_id}")
        return

    log.info(f"处理消息 [{item_id}] 来源={source}: {content[:80]}...")

    # Step 1: LLM 规划任务
    tasks_plan = await plan_tasks(llm, registry, content, source)

    if not tasks_plan:
        log.info(f"消息 [{item_id}] 无需执行任务（闲聊或无效请求）")
        # 仍然通过 LLM 生成一个友好回复
        reply = await llm.chat(
            "你是 sgMAP 平台助手，用简洁友好的语言回复用户。",
            content,
        )
        await _send_reply(agent, item, reply)
        return

    # Step 2: 通过 Hub API 创建子任务
    submitted_ids = []
    for task in tasks_plan:
        try:
            task_id = await agent.create_task(
                title=task["title"],
                description=task["description"],
                priority=task.get("priority", "P2"),
                required_capabilities=task.get("required_capabilities", []),
                assigned_to=task.get("assigned_node"),
            )
            task["_task_id"] = task_id
            submitted_ids.append(task_id)
            log.info(
                f"已提交任务 [{task_id}] → {task.get('assigned_node', '自动分配')}: "
                f"{task['title']}"
            )
        except Exception as e:
            log.error(f"提交任务失败 [{task['title']}]: {e}")
            task["_task_id"] = None

    # Step 3: 等待结果（超时 10 分钟）
    results = await wait_for_tasks(agent, [tid for tid in submitted_ids if tid], timeout=600)

    # Step 4: 汇总并回复
    summary = _summarize_results(tasks_plan, results)
    await _send_reply(agent, item, summary)


async def _send_reply(agent: AgentClient, original_item: dict, reply: str):
    """将回复发送回原始消息来源。"""
    sender_id = original_item.get("sender_id") or original_item.get("from_agent_id")
    item_id = original_item.get("id") or original_item.get("task_id", "")

    if sender_id:
        try:
            await agent.send_message(sender_id, reply)
            log.info(f"已回复给 {sender_id}")
        except Exception as e:
            log.warning(f"发送回复失败: {e}")

    # 如果是任务形式，标记完成
    if item_id and original_item.get("task_id"):
        try:
            await agent.complete_task(item_id, reply, {"orchestrated_at": datetime.now().isoformat()})
        except Exception as e:
            log.debug(f"标记任务完成失败（可能已完成）: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    log.info("=" * 60)
    log.info(f"sgMAP Hermes Orchestrator 启动")
    log.info(f"Hub:     {HUB_URL}")
    log.info(f"Ollama:  {OLLAMA_BASE_URL}")
    log.info(f"Model:   {OLLAMA_MODEL}")
    log.info(f"AGENTS:  {AGENTS_MD_PATH}")
    log.info("=" * 60)

    # 初始化组件
    llm = LLMClient()
    registry = AgentsRegistry(AGENTS_MD_PATH)

    # 预加载 AGENTS.md
    registry.get()

    # 注册到 Hub
    agent = AgentClient(
        name=ORCHESTRATOR_NAME,
        role=ORCHESTRATOR_ROLE,
        capabilities=["任务规划", "自然语言理解", "集群调度", "LLM推理", "工作流编排"],
        platform_url=HUB_URL,
        api_key=HUB_API_KEY,
        heartbeat_interval=HEARTBEAT_INTERVAL,
    )

    await agent.register()
    log.info(f"已注册到 Hub，开始监听任务...")

    # 主循环
    try:
        while True:
            try:
                inbox = await agent.get_inbox()
                if inbox:
                    log.info(f"收件箱：{len(inbox)} 条待处理")
                    for item in inbox:
                        # 并发处理，但限制同时处理数量避免 LLM 过载
                        await process_inbox_item(agent, llm, registry, item)
            except Exception as e:
                log.error(f"主循环错误: {e}", exc_info=True)

            await asyncio.sleep(INBOX_POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("收到中断信号，正在退出...")
    finally:
        await agent.stop()
        await llm.close()
        log.info("Hermes Orchestrator 已停止")


if __name__ == "__main__":
    asyncio.run(main())
