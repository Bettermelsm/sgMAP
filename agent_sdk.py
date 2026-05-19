"""
Multi-Agent Platform SDK  v2
新增: 断线重连 / 流式 LLM / 多轮对话 / 工具调用 / 消息收件箱 / 任务监听装饰器

快速开始
--------
    from agent_sdk import AgentClient

    agent = AgentClient(name="我的智能体", role="analyzer")

    @agent.on_task
    async def handle(task):
        result = await agent.llm(task["description"])
        return {"summary": result[:100]}

    asyncio.run(agent.run())
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Callable, Optional

import hashlib
import socket
import os

import httpx

log = logging.getLogger("agent_sdk")


def _node_fingerprint() -> str:
    """基于机器标识生成4位前缀，跨机器唯一"""
    raw = socket.gethostname() + str(uuid.getnode())
    return hashlib.md5(raw.encode()).hexdigest()[:4]

def _make_agent_id() -> str:
    fp = _node_fingerprint()
    short = str(uuid.uuid4())[:6]
    return f"{fp}:{short}"


class AgentMetrics:
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    avg_latency_ms: float = 0
    tasks_running: int = 0
    tasks_queued: int = 0
    errors_count: int = 0
    proc_cpu_pct: float = 0
    proc_mem_mb: float = 0
    model_name: str = ""
    model_backend: str = ""

    def to_dict(self) -> dict:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "llm_calls": self.llm_calls,
            "avg_latency_ms": self.avg_latency_ms,
            "tasks_running": self.tasks_running,
            "tasks_queued": self.tasks_queued,
            "errors_count": self.errors_count,
            "proc_cpu_pct": self.proc_cpu_pct,
            "proc_mem_mb": self.proc_mem_mb,
            "model_name": self.model_name,
            "model_backend": self.model_backend,
        }


class AgentClient:
    """
    Platform SDK client.

    Parameters
    ----------
    name : str
        Display name shown on the dashboard.
    role : str
        One of: planner / coder / retriever / analyzer / chat / evaluator / custom
    capabilities : list[str]
        Tags shown in the UI (e.g. ["data analysis", "SQL"]).
    platform_url : str
        Base URL of the FastAPI backend.
    heartbeat_interval : int
        Seconds between heartbeat POSTs.
    reconnect_delay : int
        Seconds to wait before retrying a failed registration.
    max_reconnect : int
        Max reconnect attempts (0 = unlimited).
    """

    def __init__(
        self,
        name: str,
        role: str,
        capabilities: list[str] | None = None,
        platform_url: str = "http://localhost:9527",
        description: str = "",
        heartbeat_interval: int = 15,
        reconnect_delay: int = 5,
        max_reconnect: int = 0,
    ):
        self.name               = name
        self.role               = role
        self.capabilities       = capabilities or []
        self.platform_url       = platform_url.rstrip("/")
        self.description        = description
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delay    = reconnect_delay
        self.max_reconnect      = max_reconnect

        self.agent_id:          Optional[str]      = None
        self._status:           str                = "idle"
        self._running:          bool               = False
        self._task_handler:     Optional[Callable] = None
        self._tool_registry:    dict[str, Callable]= {}
        self._conversation:     list[dict]         = []   # multi-turn history
        self._reconnect_count:  int                = 0
        self._http:             Optional[httpx.AsyncClient] = None

        self._metrics = AgentMetrics()
        self._token_counter = {"input": 0, "output": 0}

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def register(self) -> str:
        """Register with the platform, return agent_id."""
        async with self._client() as c:
            resp = await c.post(
                f"{self.platform_url}/api/agents/register",
                json={
                    "name":         self.name,
                    "role":         self.role,
                    "capabilities": self.capabilities,
                    "description":  self.description,
                    "agent_id":     _make_agent_id(),
                },
                timeout=10,
            )
            resp.raise_for_status()
            self.agent_id = resp.json()["agent_id"]
            log.info(f"[{self.name}] registered as {self.agent_id}")
            self._reconnect_count = 0
            return self.agent_id

    async def run(self, poll_inbox: bool = True):
        """
        Register, then loop: heartbeat + optional inbox polling.
        Handles network errors with automatic reconnect.
        """
        while True:
            try:
                await self.register()
                self._running = True
                tasks = [asyncio.create_task(self._heartbeat_loop())]
                if poll_inbox:
                    tasks.append(asyncio.create_task(self._inbox_loop()))
                await asyncio.gather(*tasks)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                self._reconnect_count += 1
                if self.max_reconnect and self._reconnect_count > self.max_reconnect:
                    log.error(f"[{self.name}] max reconnects reached, giving up")
                    raise
                log.warning(
                    f"[{self.name}] connection error ({e}), "
                    f"retrying in {self.reconnect_delay}s "
                    f"(attempt {self._reconnect_count})"
                )
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self):
        """Deregister and stop all loops."""
        self._running = False
        if self.agent_id:
            try:
                async with self._client() as c:
                    await c.delete(
                        f"{self.platform_url}/api/agents/{self.agent_id}",
                        timeout=5,
                    )
            except Exception:
                pass
        log.info(f"[{self.name}] stopped")

    # ── Decorators ─────────────────────────────────────────────────────

    def on_task(self, fn: Callable):
        """
        Decorator: register an async task handler.

            @agent.on_task
            async def handle(task: dict) -> dict:
                return {"summary": "done"}
        """
        self._task_handler = fn
        return fn

    def tool(self, name: str | None = None, description: str = ""):
        """
        Decorator: register a callable as an LLM tool.

            @agent.tool(name="search_web")
            async def search(query: str) -> str:
                ...
        """
        def decorator(fn: Callable):
            tool_name = name or fn.__name__
            self._tool_registry[tool_name] = {
                "fn":          fn,
                "description": description or fn.__doc__ or "",
                "name":        tool_name,
            }
            return fn
        return decorator

    # ── Task management ────────────────────────────────────────────────

    async def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "P2",
        assigned_to: str | None = None,
    ) -> str:
        """Submit a task to the platform; returns task_id."""
        async with self._client() as c:
            resp = await c.post(
                f"{self.platform_url}/api/tasks",
                json={
                    "title":       title,
                    "description": description,
                    "priority":    priority,
                    "assigned_to": assigned_to or self.agent_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            task_id = resp.json()["task_id"]
            self._status = "running"
            log.info(f"[{self.name}] created task {task_id}")
            return task_id

    async def complete_task(
        self, task_id: str, summary: str, data: dict | None = None
    ):
        """Mark task as complete and report results."""
        async with self._client() as c:
            await c.post(
                f"{self.platform_url}/api/tasks/{task_id}/complete"
                f"?agent_id={self.agent_id}",
                json={"summary": summary, "data": data or {}},
                timeout=10,
            )
        self._status = "idle"
        log.info(f"[{self.name}] completed task {task_id}")

    async def run_task(self, title: str, description: str = "", priority: str = "P2") -> dict:
        """
        High-level helper: create task → call handler → complete task.
        Requires @on_task to be registered.
        """
        if not self._task_handler:
            raise RuntimeError("No task handler registered. Use @agent.on_task")
        task_id = await self.create_task(title, description, priority)
        task = {"task_id": task_id, "title": title, "description": description}
        try:
            result = await self._task_handler(task)
            result = result or {}
            await self.complete_task(task_id, result.get("summary", title), result)
            return result
        except Exception as e:
            await self.complete_task(task_id, f"Error: {e}", {"error": str(e)})
            raise

    # ── LLM access ─────────────────────────────────────────────────────

    async def llm(
        self,
        prompt: str,
        system: str | None = None,
        remember: bool = False,
    ) -> str:
        """
        Single-turn LLM call via the platform proxy.

        Parameters
        ----------
        prompt : str
            User message.
        system : str, optional
            Override the system prompt.
        remember : bool
            If True, append this exchange to the multi-turn history.
        """
        sys_prompt = system or f"You are {self.name}, a {self.role} agent."
        async with self._client() as c:
            resp = await c.post(
                f"{self.platform_url}/api/chat",
                json={
                    "prompt":   prompt,
                    "agent_id": self.agent_id,
                    "system":   sys_prompt,
                    "history":  self._conversation.copy() if remember else [],
                    "stream":   False,
                },
                timeout=90,
            )
            resp.raise_for_status()
            answer = resp.json()["response"]
        # Track token usage (estimate from response length)
        self._metrics.llm_calls += 1
        self._metrics.tokens_in += len(prompt.split())
        self._metrics.tokens_out += len(answer.split())
        if remember:
            self._conversation.append({"role": "user",      "content": prompt})
            self._conversation.append({"role": "assistant", "content": answer})
        return answer

    async def llm_stream(
        self,
        prompt: str,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Streaming LLM call. Yields tokens as they arrive.

        Usage::

            async for token in agent.llm_stream("Tell me about Hermes"):
                print(token, end="", flush=True)
        """
        sys_prompt = system or f"You are {self.name}, a {self.role} agent."
        async with self._client(timeout=120) as c:
            async with c.stream(
                "POST",
                f"{self.platform_url}/api/chat",
                json={
                    "prompt":   prompt,
                    "agent_id": self.agent_id,
                    "system":   sys_prompt,
                    "history":  [],
                    "stream":   True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    if "token" in payload:
                        yield payload["token"]
                    if payload.get("done"):
                        break

    def clear_history(self):
        """Clear the multi-turn conversation history."""
        self._conversation.clear()

    # ── Tool-augmented LLM ─────────────────────────────────────────────

    async def llm_with_tools(self, prompt: str) -> str:
        """
        Call the LLM with all registered tools. Automatically executes
        tool calls and feeds results back into the conversation.

        Requires tools registered via @agent.tool().
        """
        if not self._tool_registry:
            return await self.llm(prompt)

        # Build tool descriptions for the system prompt
        tool_docs = "\n".join(
            f"- {name}: {info['description']}"
            for name, info in self._tool_registry.items()
        )
        system = (
            f"You are {self.name}, a {self.role} agent.\n\n"
            f"Available tools (call with JSON: {{\"tool\": \"name\", \"args\": {{...}}}}): \n{tool_docs}\n\n"
            "If you need to use a tool, respond ONLY with the JSON call. "
            "Otherwise answer directly."
        )
        response = await self.llm(prompt, system=system)

        # Try to parse a tool call from the response
        try:
            call = json.loads(response.strip())
            if "tool" in call and call["tool"] in self._tool_registry:
                tool_name = call["tool"]
                args      = call.get("args", {})
                fn        = self._tool_registry[tool_name]["fn"]
                log.info(f"[{self.name}] executing tool {tool_name}({args})")
                tool_result = await fn(**args) if asyncio.iscoroutinefunction(fn) else fn(**args)
                # Feed result back
                followup = await self.llm(
                    f"Tool '{tool_name}' returned: {tool_result}\nNow answer the original question: {prompt}",
                    system=system,
                )
                return followup
        except (json.JSONDecodeError, KeyError):
            pass  # Not a tool call — return raw response

        return response

    # ── Messaging ──────────────────────────────────────────────────────

    async def send_message(
        self, to_agent: str, content: str, msg_type: str = "text"
    ) -> str:
        """Send a message to another agent; returns msg_id."""
        async with self._client() as c:
            resp = await c.post(
                f"{self.platform_url}/api/agents/{self.agent_id}/message",
                json={"to_agent": to_agent, "content": content, "msg_type": msg_type},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()["msg_id"]

    async def get_inbox(self) -> list[dict]:
        """Return and drain the inbox (unread messages)."""
        async with self._client() as c:
            resp = await c.get(
                f"{self.platform_url}/api/agents/{self.agent_id}/inbox",
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("messages", [])

    # ── Status helpers ─────────────────────────────────────────────────

    async def set_status(self, status: str):
        """Immediately update agent status on the platform."""
        self._status = status
        if self.agent_id:
            async with self._client() as c:
                await c.post(
                    f"{self.platform_url}/api/agents/{self.agent_id}/heartbeat",
                    json={"status": status, "metrics": {}},
                    timeout=5,
                )

    async def get_metrics(self) -> dict:
        """Fetch this agent's metrics from the platform."""
        async with self._client() as c:
            resp = await c.get(
                f"{self.platform_url}/api/agents/{self.agent_id}/metrics",
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    # ── Internal loops ─────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        while self._running:
            try:
                import psutil as _psutil
                proc = _psutil.Process(os.getpid())
                self._metrics.proc_cpu_pct = proc.cpu_percent(interval=0.1)
                self._metrics.proc_mem_mb = proc.memory_info().rss / 1e6
            except Exception:
                pass
            try:
                async with self._client() as c:
                    await c.post(
                        f"{self.platform_url}/api/agents/{self.agent_id}/heartbeat",
                        json={"status": self._status, "metrics": self._metrics.to_dict()},
                        timeout=5,
                    )
            except Exception as e:
                log.warning(f"[{self.name}] heartbeat failed: {e}")
            await asyncio.sleep(self.heartbeat_interval)

    async def _inbox_loop(self):
        """Poll inbox every 5 s; dispatch messages to task handler if registered."""
        while self._running:
            try:
                msgs = await self.get_inbox()
                for msg in msgs:
                    if self._task_handler and msg.get("msg_type") == "task":
                        asyncio.create_task(
                            self._task_handler({"description": msg["content"],
                                                "title":       msg.get("content", "")[:40],
                                                "from":        msg.get("from_name")})
                        )
            except Exception:
                pass
            await asyncio.sleep(5)

    # ── HTTP client factory ────────────────────────────────────────────

    def _client(self, timeout: int = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout)


# ══════════════════════════════════════════════════════════
#  Example agents
# ══════════════════════════════════════════════════════════

class PlannerAgent(AgentClient):
    """Breaks a high-level goal into actionable steps using Hermes."""

    def __init__(self, platform_url: str = "http://localhost:9527"):
        super().__init__(
            name="规划智能体",
            role="planner",
            capabilities=["任务规划", "流程分解", "目标拆解"],
            platform_url=platform_url,
            description="将复杂目标拆解为可执行步骤",
        )

    async def plan(self, goal: str) -> list[str]:
        await self.set_status("running")
        task_id = await self.create_task(f"规划: {goal[:40]}", goal, "P1")
        response = await self.llm(
            f"请将以下目标分解为 3-5 个具体可执行步骤（每步一行）:\n{goal}",
            system="你是专业的任务规划师，给出清晰、可操作的步骤。",
        )
        steps = [s.strip() for s in response.strip().split("\n") if s.strip()]
        await self.complete_task(task_id, f"规划完成，共 {len(steps)} 步", {"steps": steps})
        return steps


class CoderAgent(AgentClient):
    """Generates and reviews code."""

    def __init__(self, platform_url: str = "http://localhost:9527"):
        super().__init__(
            name="代码智能体",
            role="coder",
            capabilities=["代码生成", "代码审查", "Debug", "重构"],
            platform_url=platform_url,
            description="负责代码生成与质量保障",
        )
        self._setup_tools()

    def _setup_tools(self):
        @self.tool(name="run_python", description="Execute a Python snippet and return stdout")
        async def run_python(code: str) -> str:
            try:
                import subprocess, sys
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True, text=True, timeout=10
                )
                return result.stdout or result.stderr or "(no output)"
            except Exception as e:
                return f"Error: {e}"

    async def generate(self, spec: str, language: str = "Python") -> str:
        task_id = await self.create_task(f"代码生成: {spec[:40]}", spec, "P2")
        code = await self.llm(
            f"请用 {language} 实现以下功能，只返回代码，不要解释:\n{spec}",
            system=f"你是一位专业的 {language} 开发工程师。",
        )
        await self.complete_task(task_id, f"生成 {len(code)} 字符代码", {"code": code, "language": language})
        return code

    async def review(self, code: str) -> str:
        return await self.llm(
            f"请审查以下代码，指出问题并给出改进建议:\n```\n{code}\n```",
            system="你是代码审查专家，关注正确性、性能和可维护性。",
        )


class AnalystAgent(AgentClient):
    """Data analysis and insight generation."""

    def __init__(self, platform_url: str = "http://localhost:9527"):
        super().__init__(
            name="分析智能体",
            role="analyzer",
            capabilities=["数据分析", "趋势预测", "报告生成", "可视化建议"],
            platform_url=platform_url,
            description="负责数据分析与洞察提取",
        )

    async def analyze(self, data_description: str) -> dict:
        task_id = await self.create_task(
            f"分析: {data_description[:40]}", data_description, "P1"
        )
        # Multi-turn: first get key metrics, then interpretation
        metrics_raw = await self.llm(
            f"请从以下数据描述中提取 3 个关键指标（JSON 格式）:\n{data_description}",
            remember=True,
        )
        interpretation = await self.llm(
            "现在请对这些指标给出业务洞察和建议",
            remember=True,
        )
        self.clear_history()
        result = {"metrics_raw": metrics_raw, "interpretation": interpretation}
        await self.complete_task(task_id, interpretation[:100], result)
        return result

    async def stream_report(self, topic: str):
        """Stream a full analysis report token by token."""
        task_id = await self.create_task(f"报告: {topic[:40]}", "", "P2")
        full = ""
        print(f"\n[{self.name}] 生成报告: {topic}\n" + "─"*40)
        async for token in self.llm_stream(
            f"请生成一份关于 '{topic}' 的详细分析报告（500字以上）"
        ):
            print(token, end="", flush=True)
            full += token
        print("\n" + "─"*40)
        await self.complete_task(task_id, full[:100], {"report": full})
        return full


# ══════════════════════════════════════════════════════════
#  CLI entry point
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    AGENTS = {
        "planner":  PlannerAgent,
        "coder":    CoderAgent,
        "analyst":  AnalystAgent,
        "generic":  AgentClient,
    }

    role = sys.argv[1] if len(sys.argv) > 1 else "generic"
    url  = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:9527"

    cls = AGENTS.get(role, AgentClient)
    if cls is AgentClient:
        agent = AgentClient(name=f"Generic-{role}", role=role, platform_url=url)
    else:
        agent = cls(platform_url=url)

    print(f"Starting {agent.name} → {url}")
    asyncio.run(agent.run())
