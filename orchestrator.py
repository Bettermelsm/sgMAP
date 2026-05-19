"""
orchestrator.py — SGA 调度中枢核心逻辑
基于 DAG 的任务调度器，自动分配任务给空闲智能体
"""
import asyncio
import json
import time
import logging

log = logging.getLogger("orchestrator")


class Orchestrator:
    """
    调度策略：
      1. 找出所有 depends_on 已满足（前置任务完成）且状态为 pending 的任务
      2. 按 priority 排序（P0 > P1 > P2 > P3）
      3. 基于 Agent capabilities 匹配最优执行者
      4. 分发任务并监控执行状态
    """

    async def tick(self):
        """调度主循环"""
        # Lazy import to avoid circular dependency
        from main import db, hot, add_event, ORCHESTRATOR_INTERVAL
        while True:
            await asyncio.sleep(ORCHESTRATOR_INTERVAL)
            try:
                await self._schedule_ready_tasks()
                await self._check_stalled_tasks()
            except Exception as e:
                log.warning(f"Orchestrator tick error: {e}")

    async def _schedule_ready_tasks(self):
        from main import db, hot
        ready = self._get_ready_tasks()
        for task in ready:
            agent = self._match_agent(task)
            if agent:
                await self._assign_task(task["task_id"], agent["agent_id"])

    def _get_ready_tasks(self) -> list:
        from main import db
        tasks = db().execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY priority"
        ).fetchall()
        ready = []
        for t in tasks:
            deps = json.loads(t["depends_on"] or "[]")
            if not deps:
                ready.append(dict(t))
                continue
            placeholders = ",".join("?" * len(deps))
            completed = db().execute(
                f"SELECT COUNT(*) as c FROM tasks WHERE task_id IN ({placeholders}) AND status='completed'",
                deps
            ).fetchone()["c"]
            if completed == len(deps):
                ready.append(dict(t))
        return ready

    def _match_agent(self, task: dict) -> dict | None:
        from main import db, hot
        idle_agents = [
            aid for aid, status in hot.agent_status.items()
            if status == "idle"
        ]
        if not idle_agents:
            return None
        # Simple strategy: match by role in capabilities
        task_role = task.get("description", "").lower()
        for aid in idle_agents:
            row = db().execute("SELECT * FROM agents WHERE agent_id=?", (aid,)).fetchone()
            if row:
                caps = json.loads(row["capabilities"] or "[]")
                caps_str = " ".join(caps).lower()
                role = row["role"].lower()
                if role in task_role or any(c in task_role for c in caps):
                    return dict(row)
        # Fallback: first idle agent
        aid = idle_agents[0]
        row = db().execute("SELECT * FROM agents WHERE agent_id=?", (aid,)).fetchone()
        return dict(row) if row else None

    async def _assign_task(self, task_id: str, agent_id: str):
        from main import db, hot, add_event, ws_mgr, build_stats
        db().execute("UPDATE tasks SET assigned_to=?, status='running' WHERE task_id=?",
                     (agent_id, task_id))
        db().commit()
        hot.agent_status[agent_id] = "running"
        hot.agent_task[agent_id] = task_id
        add_event("orchestrator", agent_id, f"自动分配任务 {task_id}", "info", task_id=task_id)
        try:
            await ws_mgr.broadcast({"type": "task_assigned", "task_id": task_id, "agent_id": agent_id, "data": build_stats()})
        except Exception:
            pass

    async def _check_stalled_tasks(self):
        from main import db, hot, add_event, TASK_STALL_TIMEOUT, DEFAULT_MAX_RETRIES
        now = time.time()
        running = db().execute("SELECT * FROM tasks WHERE status='running'").fetchall()
        for task in running:
            agent_id = task["assigned_to"]
            if not agent_id:
                continue
            last_hb = hot.agent_hb.get(agent_id, 0)
            if now - last_hb > TASK_STALL_TIMEOUT:
                retry_count = task["retry_count"] or 0
                max_retries = task["max_retries"] or DEFAULT_MAX_RETRIES
                if retry_count < max_retries:
                    db().execute(
                        "UPDATE tasks SET status='pending', retry_count=retry_count+1 WHERE task_id=?",
                        (task["task_id"],))
                    db().commit()
                    add_event("orchestrator", None, f"任务 {task['task_id']} 超时，重试 ({retry_count+1}/{max_retries})", "warning", task_id=task["task_id"])
                else:
                    db().execute(
                        "UPDATE tasks SET status='failed' WHERE task_id=?",
                        (task["task_id"],))
                    db().commit()
                    add_event("orchestrator", None, f"任务 {task['task_id']} 失败：超过最大重试次数", "error", task_id=task["task_id"])
