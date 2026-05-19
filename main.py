"""
Multi-Agent 智能体协同平台 — 后端服务 v2
新增: SQLite 持久化 / Knowledge & Alert API / 智能体消息路由 / 流式 LLM / 真实系统指标

依赖: pip install fastapi uvicorn httpx python-multipart psutil aiofiles
启动: uvicorn main:app --reload --port 8000
"""

import asyncio
import hashlib
import json
import logging
import os
import socket
import sqlite3
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ─── Logging ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("platform")

# ─── Config ──────────────────────────────────────────────
OLLAMA_BASE       = os.getenv("OLLAMA_BASE",   "http://localhost:11434")
HERMES_MODEL      = os.getenv("HERMES_MODEL",  "hermes3")
HEARTBEAT_TIMEOUT = int(os.getenv("HB_TIMEOUT", "60"))
DB_PATH           = Path(os.getenv("DB_PATH", "./platform.db"))
BROADCAST_INTERVAL = 3   # seconds
OLLAMA_AUTO_REGISTER = os.getenv("OLLAMA_AUTO_REGISTER", "true").lower() == "true"
OLLAMA_MODEL_PATTERN = os.getenv("OLLAMA_MODEL_PATTERN", "")
OLLAMA_SYNC_INTERVAL = int(os.getenv("OLLAMA_SYNC_INTERVAL", "120"))
SGA_SEED_NODES = [s.strip() for s in os.getenv("SGA_SEED_NODES", "").split(",") if s.strip()]
SGA_PUBLIC_URL = os.getenv("SGA_PUBLIC_URL", "")
ORCHESTRATOR_ENABLED = os.getenv("ORCHESTRATOR_ENABLED", "true").lower() == "true"
ORCHESTRATOR_INTERVAL = int(os.getenv("ORCHESTRATOR_INTERVAL", "5"))
TASK_STALL_TIMEOUT = int(os.getenv("TASK_STALL_TIMEOUT", "300"))
DEFAULT_MAX_RETRIES = int(os.getenv("DEFAULT_MAX_RETRIES", "3"))
THIS_NODE_ID = hashlib.md5((socket.gethostname() + str(uuid.getnode())).encode()).hexdigest()[:4]

# ─── App ─────────────────────────────────────────────────
app = FastAPI(title="Multi-Agent Platform", version="2.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Serve frontend ──────────────────────────────────────
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", "."))


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ══════════════════════════════════════════════════════════
#  SQLite persistence layer
# ══════════════════════════════════════════════════════════
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id     TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            role         TEXT NOT NULL,
            capabilities TEXT DEFAULT '[]',
            description  TEXT DEFAULT '',
            status       TEXT DEFAULT 'idle',
            tasks_completed INTEGER DEFAULT 0,
            last_heartbeat  REAL,
            metrics      TEXT DEFAULT '{}',
            registered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            task_id      TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            priority     TEXT DEFAULT 'P2',
            status       TEXT DEFAULT 'pending',
            assigned_to  TEXT,
            result_summary TEXT DEFAULT '',
            result_data  TEXT DEFAULT '{}',
            created_at   TEXT,
            completed_at TEXT,
            depends_on TEXT DEFAULT '[]',
            workflow_id TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            context_snapshot TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS events (
            id           TEXT PRIMARY KEY,
            timestamp    TEXT,
            from_agent   TEXT,
            from_name    TEXT,
            to_agent     TEXT,
            to_name      TEXT,
            action       TEXT,
            level        TEXT DEFAULT 'info',
            task_id      TEXT,
            result_summary TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            kb_id        TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            type         TEXT DEFAULT 'docs',
            description  TEXT DEFAULT '',
            doc_count    INTEGER DEFAULT 0,
            size_bytes   INTEGER DEFAULT 0,
            vector_pct   INTEGER DEFAULT 0,
            status       TEXT DEFAULT 'synced',
            created_at   TEXT,
            updated_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_rules (
            rule_id      TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            condition    TEXT NOT NULL,
            threshold    REAL DEFAULT 0,
            level        TEXT DEFAULT 'warning',
            enabled      INTEGER DEFAULT 1,
            created_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id     TEXT PRIMARY KEY,
            rule_id      TEXT,
            level        TEXT NOT NULL,
            title        TEXT NOT NULL,
            detail       TEXT DEFAULT '',
            acknowledged INTEGER DEFAULT 0,
            created_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            msg_id       TEXT PRIMARY KEY,
            from_agent   TEXT NOT NULL,
            to_agent     TEXT NOT NULL,
            content      TEXT NOT NULL,
            msg_type     TEXT DEFAULT 'text',
            read         INTEGER DEFAULT 0,
            created_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_logs (
            log_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            level TEXT DEFAULT 'info',
            message TEXT,
            context TEXT DEFAULT '{}',
            ts REAL
        );
        CREATE TABLE IF NOT EXISTS peer_nodes (
            node_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            alias TEXT DEFAULT '',
            status TEXT DEFAULT 'online',
            last_seen REAL,
            registered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            dag_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'pending',
            created_by TEXT DEFAULT 'user',
            created_at TEXT,
            completed_at TEXT
        );
        """)
        db.commit()
        # Seed default KB and alert rules if empty
        if not db.execute("SELECT 1 FROM knowledge_bases LIMIT 1").fetchone():
            _seed_knowledge_bases(db)
        if not db.execute("SELECT 1 FROM alert_rules LIMIT 1").fetchone():
            _seed_alert_rules(db)


def _seed_knowledge_bases(db):
    now = datetime.now().isoformat()
    kbs = [
        ("kb1", "产品文档库", "docs",   "产品相关文档与规范",  1240, 2469606400, 87, "synced"),
        ("kb2", "研究论文库", "vector", "学术论文与研究报告",  580,  4404019200, 95, "synced"),
        ("kb3", "内部知识库", "docs",   "内部流程与操作手册",  3200, 1932735283, 61, "warning"),
        ("kb4", "网络爬取库", "vector", "互联网数据抓取",      18500,12884901888,78, "synced"),
        ("kb5", "数据分析库", "graph",  "数据集与分析报告",    420,  966367641,  100,"synced"),
    ]
    for kb in kbs:
        db.execute(
            "INSERT INTO knowledge_bases VALUES (?,?,?,?,?,?,?,?,?,?)",
            (*kb, now, now)
        )
    db.commit()


def _seed_alert_rules(db):
    now = datetime.now().isoformat()
    rules = [
        ("r1", "CPU 使用率过高",  "cpu_gt",        85, "warning", 1),
        ("r2", "内存使用率过高",  "mem_gt",        90, "danger",  1),
        ("r3", "智能体离线告警",  "agent_offline",  0, "danger",  1),
        ("r4", "任务失败告警",    "task_fail",      0, "warning", 0),
    ]
    for r in rules:
        db.execute("INSERT INTO alert_rules VALUES (?,?,?,?,?,?,?)", (*r, now))
    db.commit()


# ─── In-memory hot state (not persisted between restarts for speed) ───
class HotState:
    def __init__(self):
        self.agent_status:   dict[str, str]  = {}   # agent_id -> status
        self.agent_hb:       dict[str, float]= {}   # agent_id -> last heartbeat
        self.agent_metrics:  dict[str, dict] = {}
        self.agent_task:     dict[str, str]  = {}   # agent_id -> current task_id
        self.events:         deque           = deque(maxlen=300)
        self.interactions:   int             = 0
        self.data_mb:        float           = 0.0
        self.message_queues: dict[str, list] = {}   # agent_id -> pending messages


hot = HotState()
_db_conn: Optional[sqlite3.Connection] = None


def db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = get_db()
    return _db_conn


# ══════════════════════════════════════════════════════════
#  WebSocket manager
# ══════════════════════════════════════════════════════════
class WsManager:
    def __init__(self):
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)
        log.info(f"WS connected ({len(self.clients)} total)")

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.remove(ws)


ws_mgr = WsManager()


# ══════════════════════════════════════════════════════════
#  Helper utilities
# ══════════════════════════════════════════════════════════
def now_iso() -> str:
    return datetime.now().isoformat()


def now_ts() -> float:
    return time.time()


def agent_name(agent_id: str) -> str:
    if not agent_id:
        return "system"
    row = db().execute("SELECT name FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    return row["name"] if row else agent_id


def add_event(from_agent: str, to_agent: Optional[str], action: str,
              level: str = "info", task_id: str = None, result_summary: str = ""):
    eid = str(uuid.uuid4())[:8]
    ts  = datetime.now().strftime("%H:%M:%S")
    fn  = agent_name(from_agent)
    tn  = agent_name(to_agent) if to_agent else None
    evt = {
        "id": eid, "timestamp": ts,
        "from_agent": from_agent, "from_name": fn,
        "to_agent": to_agent,    "to_name": tn,
        "action": action, "level": level,
        "task_id": task_id or "", "result_summary": result_summary or "",
    }
    hot.events.append(evt)
    try:
        db().execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, ts, from_agent, fn, to_agent, tn, action, level,
             task_id or "", result_summary or "")
        )
        db().commit()
    except Exception as e:
        log.warning(f"Event persist error: {e}")

ROLE_CAPS = {
    "planner":   {"perception": 80, "reasoning": 90, "qa": 60,  "tools": 50,  "code": 30,  "data": 70},
    "coder":     {"perception": 50, "reasoning": 70, "qa": 60,  "tools": 85,  "code": 95,  "data": 50},
    "retriever": {"perception": 85, "reasoning": 60, "qa": 90,  "tools": 70,  "code": 30,  "data": 65},
    "analyzer":  {"perception": 70, "reasoning": 85, "qa": 70,  "tools": 60,  "code": 40,  "data": 95},
    "evaluator": {"perception": 75, "reasoning": 80, "qa": 75,  "tools": 55,  "code": 60,  "data": 70},
    "chat":      {"perception": 90, "reasoning": 65, "qa": 85,  "tools": 40,  "code": 25,  "data": 40},
    "custom":    {"perception": 60, "reasoning": 60, "qa": 60,  "tools": 60,  "code": 60,  "data": 60},
}


def _compute_capability_scores(agents: list) -> list:
    dims = ["perception", "reasoning", "qa", "tools", "code", "data"]
    if not agents:
        return [0] * 6
    totals = {d: 0 for d in dims}
    for a in agents:
        base = ROLE_CAPS.get(a["role"], ROLE_CAPS["custom"])
        for d in dims:
            totals[d] += base[d]
    return [round(totals[d] / len(agents)) for d in dims]


def _get_gpu_utilization() -> int:
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return util.gpu
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return -1


def get_real_resources() -> dict:
    """Return real system metrics via psutil."""
    try:
        cpu  = round(psutil.cpu_percent(interval=None), 1)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
        gpu  = _get_gpu_utilization()
        return {
            "cpu":          cpu,
            "memory":       round(mem.percent, 1),
            "memory_used":  round(mem.used / 1e9, 2),
            "memory_total": round(mem.total / 1e9, 2),
            "gpu":          gpu,
            "storage":      round(disk.percent, 1),
            "storage_used": round(disk.used / 1e9, 1),
            "storage_total":round(disk.total / 1e9, 1),
        }
    except Exception:
        return {"cpu": 42, "memory": 68, "gpu": -1, "storage": 51}


def _compute_token_stats(agents: list) -> dict:
    result = {}
    for a in agents:
        m = a.get("metrics", {})
        if isinstance(m, dict):
            result[a["agent_id"]] = {
                "name": a["name"],
                "tokens_in": m.get("tokens_in", 0),
                "tokens_out": m.get("tokens_out", 0),
                "llm_calls": m.get("llm_calls", 0),
            }
    return result

def _get_peer_list() -> list:
    try:
        rows = db().execute("SELECT * FROM peer_nodes ORDER BY registered_at").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

def build_stats() -> dict:
    # --- agents ---
    rows = db().execute("SELECT * FROM agents").fetchall()
    agents = []
    now = now_ts()
    statuses = {"running": 0, "idle": 0, "busy": 0, "offline": 0}
    for r in rows:
        aid = r["agent_id"]
        # apply hot state
        hb  = hot.agent_hb.get(aid, r["last_heartbeat"] or now)
        st  = hot.agent_status.get(aid, r["status"])
        if (now - hb) > HEARTBEAT_TIMEOUT and st != "offline":
            st = "offline"
            hot.agent_status[aid] = "offline"
        statuses[st] = statuses.get(st, 0) + 1
        agents.append({
            "agent_id":        aid,
            "name":            r["name"],
            "role":            r["role"],
            "capabilities":    json.loads(r["capabilities"] or "[]"),
            "description":     r["description"],
            "status":          st,
            "tasks_completed": r["tasks_completed"],
            "last_heartbeat":  hb,
            "metrics":         hot.agent_metrics.get(aid, {}),
            "current_task":    hot.agent_task.get(aid),
            "registered_at":   r["registered_at"],
        })

    # --- tasks ---
    task_rows = db().execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    tasks = [dict(t) for t in task_rows]
    tp  = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    ts2 = {"pending": 0, "running": 0, "completed": 0, "paused": 0, "failed": 0}
    for t in tasks:
        tp[t.get("priority", "P2")] = tp.get(t.get("priority", "P2"), 0) + 1
        ts2[t.get("status", "pending")] = ts2.get(t.get("status", "pending"), 0) + 1

    top5 = sorted(agents, key=lambda a: a["tasks_completed"], reverse=True)[:5]

    return {
        "agents":           agents,
        "agent_count":      len(agents),
        "agent_statuses":   statuses,
        "tasks":            tasks,
        "task_count":       len(tasks),
        "task_priorities":  tp,
        "task_statuses":    ts2,
        "top5_agents":      [{"name": a["name"], "agent_id": a["agent_id"], "tasks_completed": a["tasks_completed"]} for a in top5],
        "events":           list(hot.events)[-30:],
        "resources":        get_real_resources(),
        "interactions":     hot.interactions,
        "data_processed":   round(hot.data_mb / 1000, 2),
        "ws_clients":       len(ws_mgr.clients),
        "capability_scores": _compute_capability_scores(agents),
        "token_stats": _compute_token_stats(agents),
        "peers": _get_peer_list(),
    }


# ══════════════════════════════════════════════════════════
#  Pydantic models
# ══════════════════════════════════════════════════════════
class AgentRegisterReq(BaseModel):
    name:         str
    role:         str
    capabilities: list[str] = []
    agent_id:     Optional[str] = None
    description:  str = ""

class HeartbeatReq(BaseModel):
    status:  str  = "idle"
    metrics: dict = {}

class TaskCreateReq(BaseModel):
    title:       str
    description: str = ""
    priority:    str = "P2"
    assigned_to: Optional[str] = None
    depends_on: list[str] = []
    workflow_id: str = ""
    max_retries: int = 3
    context_snapshot: dict = {}

class TaskResultReq(BaseModel):
    summary: str
    data:    dict = {}

class ChatReq(BaseModel):
    prompt:   str
    agent_id: Optional[str] = None
    model:    Optional[str] = None
    system:   str  = "You are a helpful assistant."
    stream:   bool = False
    history:  list[dict] = []   # multi-turn: [{role,content}, ...]

class KbCreateReq(BaseModel):
    name:        str
    type:        str = "docs"
    description: str = ""

class KbUpdateReq(BaseModel):
    name:        Optional[str] = None
    description: Optional[str] = None
    status:      Optional[str] = None

class AlertRuleReq(BaseModel):
    name:      str
    condition: str
    threshold: float = 0
    level:     str   = "warning"
    enabled:   bool  = True

class MessageReq(BaseModel):
    to_agent: str
    content:  str
    msg_type: str = "text"

class AgentLogReq(BaseModel):
    level: str = "info"
    message: str
    context: dict = {}

class WorkflowCreateReq(BaseModel):
    name: str
    description: str = ""
    tasks: list[dict] = []
    dependencies: dict = {}

class TaskContextReq(BaseModel):
    context_snapshot: dict = {}

class TaskReassignReq(BaseModel):
    agent_id: str


# ══════════════════════════════════════════════════════════
#  Startup / background tasks
# ══════════════════════════════════════════════════════════
async def _sync_ollama_models():
    """Discover Ollama models and register/deregister them as platform agents."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            ollama_models = resp.json().get("models", [])
    except Exception as e:
        log.info(f"Ollama sync: cannot reach Ollama ({e})")
        return

    existing = db().execute(
        "SELECT agent_id, name FROM agents WHERE description LIKE '%[ollama-auto]%'"
    ).fetchall()
    existing_map = {r["name"]: r["agent_id"] for r in existing}

    model_names = []
    for m in ollama_models:
        name = m.get("name", "")
        if OLLAMA_MODEL_PATTERN and OLLAMA_MODEL_PATTERN not in name:
            continue
        model_names.append(name)

    for name in model_names:
        if name in existing_map:
            continue
        agent_id = f"ollama-{name.replace(':','_')}"[:16]
        caps = [f"model:{name}", "LLM", "对话"]
        description = f"Ollama model: {name} [ollama-auto]"
        try:
            db().execute(
                """INSERT INTO agents (agent_id,name,role,capabilities,description,status,
                   tasks_completed,last_heartbeat,metrics,registered_at)
                   VALUES (?,?,?,?,?,'idle',0,?,?,?)""",
                (agent_id, name, "chat", json.dumps(caps), description, now_ts(), "{}", now_iso())
            )
            db().commit()
            hot.agent_status[agent_id] = "idle"
            hot.agent_hb[agent_id] = now_ts()
            hot.interactions += 1
            add_event("system", agent_id, f"Ollama model auto-registered: {name}", "info")
            log.info(f"Auto-registered Ollama model: {name} as {agent_id}")
        except Exception as e:
            log.warning(f"Failed to auto-register model {name}: {e}")

    for name, aid in existing_map.items():
        if name not in model_names:
            db().execute("DELETE FROM agents WHERE agent_id=?", (aid,))
            db().commit()
            hot.agent_status.pop(aid, None)
            hot.agent_hb.pop(aid, None)
            add_event("system", aid, f"Ollama model removed: {name}", "warning")
            log.info(f"Auto-deregistered removed Ollama model: {name}")

    await ws_mgr.broadcast({"type": "ollama_sync", "data": build_stats()})


async def _ollama_sync_loop():
    """Periodically sync Ollama models to agent registry."""
    while True:
        await asyncio.sleep(OLLAMA_SYNC_INTERVAL)
        await _sync_ollama_models()


@app.on_event("startup")
async def startup():
    init_db()
    # restore hot state from DB
    for row in db().execute("SELECT agent_id, status, last_heartbeat FROM agents").fetchall():
        hot.agent_status[row["agent_id"]] = row["status"]
        hot.agent_hb[row["agent_id"]]     = row["last_heartbeat"] or now_ts()
    log.info(f"Platform v2 started. DB={DB_PATH}, {len(hot.agent_status)} agents restored.")
    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(_alert_check_loop())
    if OLLAMA_AUTO_REGISTER:
        await _sync_ollama_models()
        asyncio.create_task(_ollama_sync_loop())
    # Start Orchestrator
    if ORCHESTRATOR_ENABLED:
        from orchestrator import Orchestrator
        orch = Orchestrator()
        asyncio.create_task(orch.tick())
    # Start peer mesh
    if SGA_SEED_NODES:
        from peer_mesh import PeerMesh
        mesh = PeerMesh()
        asyncio.create_task(mesh.run(THIS_NODE_ID))


async def _broadcast_loop():
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL)
        try:
            stats = build_stats()
            stats["ts"] = now_ts()
            await ws_mgr.broadcast({"type": "stats_update", "data": stats})
        except Exception as e:
            log.warning(f"Broadcast error: {e}")


async def _alert_check_loop():
    """Check alert rules against real metrics every 15 s."""
    while True:
        await asyncio.sleep(15)
        try:
            resources = get_real_resources()
            rules = db().execute(
                "SELECT * FROM alert_rules WHERE enabled=1"
            ).fetchall()
            for rule in rules:
                cond  = rule["condition"]
                thresh = rule["threshold"]
                fired = False
                if cond == "cpu_gt"    and resources["cpu"]    > thresh: fired = True
                if cond == "mem_gt"    and resources["memory"] > thresh: fired = True
                if cond == "agent_offline":
                    offline = sum(1 for s in hot.agent_status.values() if s == "offline")
                    if offline > 0: fired = True
                if fired:
                    aid = str(uuid.uuid4())[:8]
                    db().execute(
                        "INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,?)",
                        (aid, rule["rule_id"], rule["level"],
                         f"[{rule['name']}] 规则触发",
                         f"条件: {cond} threshold={thresh}",
                         0, now_iso())
                    )
                    db().commit()
        except Exception as e:
            log.debug(f"Alert check error: {e}")


# ══════════════════════════════════════════════════════════
#  Routes: Agents
# ══════════════════════════════════════════════════════════
@app.post("/api/agents/register", summary="注册智能体")
async def register_agent(req: AgentRegisterReq):
    agent_id = req.agent_id or str(uuid.uuid4())[:8]
    n = now_iso()
    db().execute(
        """INSERT INTO agents (agent_id,name,role,capabilities,description,status,
           tasks_completed,last_heartbeat,metrics,registered_at)
           VALUES (?,?,?,?,?,'idle',0,?,?,?)
           ON CONFLICT(agent_id) DO UPDATE SET
             name=excluded.name, role=excluded.role,
             capabilities=excluded.capabilities, description=excluded.description,
             status='idle', last_heartbeat=excluded.last_heartbeat""",
        (agent_id, req.name, req.role, json.dumps(req.capabilities),
         req.description, now_ts(), "{}", n)
    )
    db().commit()
    hot.agent_status[agent_id] = "idle"
    hot.agent_hb[agent_id]     = now_ts()
    add_event("system", agent_id, f"智能体 {req.name} 注册上线", "info")
    hot.interactions += 1
    stats = build_stats()
    await ws_mgr.broadcast({"type": "agent_joined", "agent_id": agent_id, "data": stats})
    log.info(f"Agent registered: {req.name} ({agent_id})")
    return {"agent_id": agent_id, "status": "registered"}


@app.get("/api/agents", summary="列出所有智能体")
async def list_agents(role: str = None, status: str = None):
    sql = "SELECT * FROM agents"
    params = []
    filters = []
    if role:   filters.append("role=?");   params.append(role)
    if status: filters.append("status=?"); params.append(status)
    if filters: sql += " WHERE " + " AND ".join(filters)
    rows = db().execute(sql, params).fetchall()
    agents = []
    n = now_ts()
    for r in rows:
        aid = r["agent_id"]
        st  = hot.agent_status.get(aid, r["status"])
        hb  = hot.agent_hb.get(aid, r["last_heartbeat"] or n)
        if (n - hb) > HEARTBEAT_TIMEOUT and st != "offline":
            st = "offline"; hot.agent_status[aid] = "offline"
        agents.append({**dict(r),
                       "status":          st,
                       "last_heartbeat":  hb,
                       "capabilities":    json.loads(r["capabilities"] or "[]"),
                       "metrics":         hot.agent_metrics.get(aid, {}),
                       "current_task":    hot.agent_task.get(aid)})
    return {"agents": agents}


@app.get("/api/agents/{agent_id}", summary="获取单个智能体")
async def get_agent(agent_id: str):
    row = db().execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    st = hot.agent_status.get(agent_id, row["status"])
    return {**dict(row),
            "status":         st,
            "last_heartbeat": hot.agent_hb.get(agent_id, row["last_heartbeat"]),
            "capabilities":   json.loads(row["capabilities"] or "[]"),
            "metrics":        hot.agent_metrics.get(agent_id, {}),
            "current_task":   hot.agent_task.get(agent_id)}


@app.post("/api/agents/{agent_id}/heartbeat", summary="发送心跳")
async def heartbeat(agent_id: str, req: HeartbeatReq):
    if not db().execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone():
        raise HTTPException(404, "Agent not found")
    hot.agent_hb[agent_id]      = now_ts()
    hot.agent_status[agent_id]  = req.status
    hot.agent_metrics[agent_id] = req.metrics
    db().execute(
        "UPDATE agents SET last_heartbeat=?, status=? WHERE agent_id=?",
        (now_ts(), req.status, agent_id)
    )
    db().commit()
    return {"ok": True, "ts": now_ts()}


@app.get("/api/agents/{agent_id}/metrics", summary="获取智能体指标")
async def agent_metrics(agent_id: str):
    if not db().execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone():
        raise HTTPException(404, "Agent not found")
    tasks_done = db().execute(
        "SELECT COUNT(*) as c FROM tasks WHERE assigned_to=? AND status='completed'",
        (agent_id,)
    ).fetchone()["c"]
    tasks_pending = db().execute(
        "SELECT COUNT(*) as c FROM tasks WHERE assigned_to=? AND status='pending'",
        (agent_id,)
    ).fetchone()["c"]
    return {
        "agent_id":      agent_id,
        "tasks_done":    tasks_done,
        "tasks_pending": tasks_pending,
        "status":        hot.agent_status.get(agent_id, "unknown"),
        "last_hb_ago":   round(now_ts() - hot.agent_hb.get(agent_id, now_ts()), 1),
        "metrics":       hot.agent_metrics.get(agent_id, {}),
    }


@app.delete("/api/agents/{agent_id}", summary="注销智能体")
async def deregister_agent(agent_id: str):
    row = db().execute("SELECT name FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    hot.agent_status[agent_id] = "offline"
    db().execute("UPDATE agents SET status='offline' WHERE agent_id=?", (agent_id,))
    db().commit()
    add_event("system", agent_id, f"智能体 {row['name']} 下线", "info")
    hot.interactions += 1
    await ws_mgr.broadcast({"type": "agent_left", "data": build_stats()})
    return {"ok": True}


# ══════════════════════════════════════════════════════════
#  Routes: Tasks
# ══════════════════════════════════════════════════════════
@app.post("/api/tasks", summary="创建任务")
async def create_task(req: TaskCreateReq):
    task_id = "T-" + str(uuid.uuid4())[:6].upper()
    n = now_iso()
    db().execute(
        """INSERT INTO tasks (task_id,title,description,priority,status,
           assigned_to,result_summary,result_data,created_at,
           depends_on,workflow_id,max_retries,context_snapshot)
           VALUES (?,?,?,?,'pending',?,?,?,?,
                   ?,?,?,?)""",
        (task_id, req.title, req.description, req.priority,
         req.assigned_to, "", "{}", n,
         json.dumps(req.depends_on), req.workflow_id, req.max_retries,
         json.dumps(req.context_snapshot))
    )
    db().commit()
    add_event("system", req.assigned_to, f"创建任务: {req.title}", "info", task_id=task_id)
    hot.interactions += 1
    if req.assigned_to and hot.agent_status.get(req.assigned_to) not in (None, "offline"):
        hot.agent_status[req.assigned_to] = "running"
        hot.agent_task[req.assigned_to]   = task_id
        db().execute("UPDATE agents SET status='running' WHERE agent_id=?", (req.assigned_to,))
        db().commit()
    await ws_mgr.broadcast({"type": "task_created", "task_id": task_id, "data": build_stats()})
    log.info(f"Task created: {req.title} ({task_id})")
    return {"task_id": task_id}


@app.post("/api/tasks/{task_id}/complete", summary="完成任务")
async def complete_task(task_id: str, agent_id: str, req: TaskResultReq):
    row = db().execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    db().execute(
        "UPDATE tasks SET status='completed',result_summary=?,result_data=?,completed_at=? WHERE task_id=?",
        (req.summary, json.dumps(req.data), now_iso(), task_id)
    )
    db().execute(
        "UPDATE agents SET tasks_completed=tasks_completed+1, status='idle' WHERE agent_id=?",
        (agent_id,)
    )
    db().commit()
    hot.agent_status[agent_id] = "idle"
    hot.agent_task.pop(agent_id, None)
    add_event(agent_id, None, "完成任务", "success", task_id=task_id, result_summary=req.summary[:80])
    hot.interactions += 1
    hot.data_mb += len(json.dumps(req.data)) / 1e6
    await ws_mgr.broadcast({"type": "task_completed", "task_id": task_id, "data": build_stats()})
    return {"ok": True}


@app.get("/api/tasks", summary="列出任务")
async def list_tasks(
    status:   str = None,
    priority: str = None,
    agent_id: str = None,
    q:        str = None,
    limit:    int = Query(100, le=500),
    offset:   int = 0,
):
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if status:   sql += " AND status=?";      params.append(status)
    if priority: sql += " AND priority=?";    params.append(priority)
    if agent_id: sql += " AND assigned_to=?"; params.append(agent_id)
    if q:        sql += " AND title LIKE ?";  params.append(f"%{q}%")
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    tasks = [dict(t) for t in db().execute(sql, params).fetchall()]
    count_sql = "SELECT COUNT(*) as c FROM tasks"
    count_params = []
    if status:
        count_sql += " WHERE status=?"
        count_params.append(status)
    total = db().execute(count_sql, count_params).fetchone()["c"]
    return {"tasks": tasks, "total": total, "limit": limit, "offset": offset}


@app.get("/api/tasks/{task_id}", summary="获取单个任务")
async def get_task(task_id: str):
    row = db().execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Task not found")
    return dict(row)


@app.delete("/api/tasks/{task_id}", summary="删除任务")
async def delete_task(task_id: str):
    if not db().execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
        raise HTTPException(404, "Task not found")
    db().execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
    db().commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════
#  Routes: Knowledge bases
# ══════════════════════════════════════════════════════════
@app.get("/api/knowledge", summary="列出知识库")
async def list_kbs():
    rows = db().execute("SELECT * FROM knowledge_bases ORDER BY created_at").fetchall()
    kbs = []
    for r in rows:
        kbs.append({
            "kb_id":       r["kb_id"],
            "name":        r["name"],
            "type":        r["type"],
            "description": r["description"],
            "doc_count":   r["doc_count"],
            "size_gb":     round(r["size_bytes"] / 1e9, 2),
            "vector_pct":  r["vector_pct"],
            "status":      r["status"],
            "updated_at":  r["updated_at"],
        })
    return {"knowledge_bases": kbs}


@app.post("/api/knowledge", summary="添加知识库")
async def create_kb(req: KbCreateReq):
    kb_id = "kb" + str(uuid.uuid4())[:6]
    n = now_iso()
    db().execute(
        "INSERT INTO knowledge_bases VALUES (?,?,?,?,?,?,?,?,?,?)",
        (kb_id, req.name, req.type, req.description, 0, 0, 0, "synced", n, n)
    )
    db().commit()
    add_event("system", None, f"知识库 {req.name} 已创建", "info")
    return {"kb_id": kb_id}


@app.patch("/api/knowledge/{kb_id}", summary="更新知识库")
async def update_kb(kb_id: str, req: KbUpdateReq):
    row = db().execute("SELECT * FROM knowledge_bases WHERE kb_id=?", (kb_id,)).fetchone()
    if not row:
        raise HTTPException(404, "KB not found")
    updates = {k: v for k, v in req.dict().items() if v is not None}
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        db().execute(
            f"UPDATE knowledge_bases SET {set_clause}, updated_at=? WHERE kb_id=?",
            [*updates.values(), now_iso(), kb_id]
        )
        db().commit()
    return {"ok": True}


@app.post("/api/knowledge/{kb_id}/sync", summary="触发同步")
async def sync_kb(kb_id: str):
    row = db().execute("SELECT name FROM knowledge_bases WHERE kb_id=?", (kb_id,)).fetchone()
    if not row:
        raise HTTPException(404, "KB not found")
    # Simulate sync: update status, bump vector_pct
    db().execute(
        """UPDATE knowledge_bases
           SET status='synced',
               vector_pct=MIN(100, vector_pct+5),
               updated_at=?
           WHERE kb_id=?""",
        (now_iso(), kb_id)
    )
    db().commit()
    add_event("system", None, f"知识库 {row['name']} 同步完成", "success")
    await ws_mgr.broadcast({"type": "kb_synced", "kb_id": kb_id})
    return {"ok": True}


@app.delete("/api/knowledge/{kb_id}", summary="删除知识库")
async def delete_kb(kb_id: str):
    if not db().execute("SELECT 1 FROM knowledge_bases WHERE kb_id=?", (kb_id,)).fetchone():
        raise HTTPException(404, "KB not found")
    db().execute("DELETE FROM knowledge_bases WHERE kb_id=?", (kb_id,))
    db().commit()
    return {"ok": True}


@app.post("/api/knowledge/query", summary="知识库查询（转发到 Hermes）")
async def kb_query(kb_id: Optional[str] = None, q: str = ""):
    if not q:
        raise HTTPException(400, "Query string required")
    # Forward to Hermes as a RAG-style prompt
    kb_name = "全部知识库"
    if kb_id:
        row = db().execute("SELECT name FROM knowledge_bases WHERE kb_id=?", (kb_id,)).fetchone()
        if row:
            kb_name = row["name"]
    system = f"你是知识库 '{kb_name}' 的检索助手，根据用户问题给出精准答案。"
    prompt = f"请在知识库中检索并回答：{q}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json={
                "model": HERMES_MODEL,
                "messages": [{"role": "system", "content": system},
                              {"role": "user",   "content": prompt}],
                "stream": False,
            })
            resp.raise_for_status()
            answer = resp.json()["message"]["content"]
    except Exception:
        # Fallback when Ollama not available
        answer = f"[模拟结果] 在 '{kb_name}' 中找到 3 条相关内容：\n1. 相关度 94%\n2. 相关度 87%\n3. 相关度 79%"
    return {"answer": answer, "kb": kb_name, "query": q}


# ══════════════════════════════════════════════════════════
#  Routes: Alerts
# ══════════════════════════════════════════════════════════
@app.get("/api/alerts/rules", summary="列出告警规则")
async def list_rules():
    rows = db().execute("SELECT * FROM alert_rules ORDER BY created_at").fetchall()
    return {"rules": [dict(r) for r in rows]}


@app.post("/api/alerts/rules", summary="创建告警规则")
async def create_rule(req: AlertRuleReq):
    rid = "r" + str(uuid.uuid4())[:6]
    db().execute(
        "INSERT INTO alert_rules VALUES (?,?,?,?,?,?,?)",
        (rid, req.name, req.condition, req.threshold,
         req.level, int(req.enabled), now_iso())
    )
    db().commit()
    return {"rule_id": rid}


@app.patch("/api/alerts/rules/{rule_id}", summary="更新告警规则")
async def update_rule(rule_id: str, enabled: Optional[bool] = None, name: Optional[str] = None):
    if not db().execute("SELECT 1 FROM alert_rules WHERE rule_id=?", (rule_id,)).fetchone():
        raise HTTPException(404, "Rule not found")
    if enabled is not None:
        db().execute("UPDATE alert_rules SET enabled=? WHERE rule_id=?", (int(enabled), rule_id))
    if name:
        db().execute("UPDATE alert_rules SET name=? WHERE rule_id=?", (name, rule_id))
    db().commit()
    return {"ok": True}


@app.delete("/api/alerts/rules/{rule_id}", summary="删除告警规则")
async def delete_rule(rule_id: str):
    db().execute("DELETE FROM alert_rules WHERE rule_id=?", (rule_id,))
    db().commit()
    return {"ok": True}


@app.get("/api/alerts", summary="列出告警")
async def list_alerts(level: str = None, acknowledged: bool = None, limit: int = 50):
    sql = "SELECT * FROM alerts WHERE 1=1"
    params: list = []
    if level:
        sql += " AND level=?"; params.append(level)
    if acknowledged is not None:
        sql += " AND acknowledged=?"; params.append(int(acknowledged))
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db().execute(sql, params).fetchall()
    return {"alerts": [dict(r) for r in rows]}


@app.post("/api/alerts/{alert_id}/acknowledge", summary="确认告警")
async def ack_alert(alert_id: str):
    db().execute("UPDATE alerts SET acknowledged=1 WHERE alert_id=?", (alert_id,))
    db().commit()
    return {"ok": True}


@app.delete("/api/alerts", summary="清空告警")
async def clear_alerts():
    db().execute("DELETE FROM alerts")
    db().commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════
#  Routes: Agent-to-agent messaging
# ══════════════════════════════════════════════════════════
@app.post("/api/agents/{agent_id}/message", summary="发送消息给智能体")
async def send_message(agent_id: str, req: MessageReq):
    """Route a message from one agent to another. Target agent can poll /inbox."""
    if not db().execute("SELECT 1 FROM agents WHERE agent_id=?", (agent_id,)).fetchone():
        raise HTTPException(404, "Source agent not found")
    if not db().execute("SELECT 1 FROM agents WHERE agent_id=?", (req.to_agent,)).fetchone():
        raise HTTPException(404, "Target agent not found")
    msg_id = "M-" + str(uuid.uuid4())[:6].upper()
    db().execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
        (msg_id, agent_id, req.to_agent, req.content, req.msg_type, 0, now_iso())
    )
    db().commit()
    add_event(agent_id, req.to_agent, f"发送消息: {req.content[:40]}", "info")
    hot.interactions += 1
    # Push to target's in-memory queue for instant delivery
    hot.message_queues.setdefault(req.to_agent, []).append({
        "msg_id":     msg_id,
        "from_agent": agent_id,
        "from_name":  agent_name(agent_id),
        "content":    req.content,
        "msg_type":   req.msg_type,
        "created_at": now_iso(),
    })
    await ws_mgr.broadcast({
        "type":     "new_message",
        "to_agent": req.to_agent,
        "from":     agent_name(agent_id),
        "preview":  req.content[:60],
    })
    return {"msg_id": msg_id}


@app.get("/api/agents/{agent_id}/inbox", summary="获取智能体收件箱")
async def get_inbox(agent_id: str, unread_only: bool = True):
    """Drain the in-memory queue (instant) + DB history."""
    pending = hot.message_queues.pop(agent_id, [])
    if pending:
        db().executemany(
            "UPDATE messages SET read=1 WHERE msg_id=?",
            [(m["msg_id"],) for m in pending]
        )
        db().commit()
    # Also fetch from DB for history
    sql = "SELECT * FROM messages WHERE to_agent=? ORDER BY created_at DESC LIMIT 50"
    history = [dict(r) for r in db().execute(sql, (agent_id,)).fetchall()]
    return {"messages": pending + history, "pending_count": len(pending)}


# ══════════════════════════════════════════════════════════
#  Routes: LLM proxy (with streaming + multi-turn)
# ══════════════════════════════════════════════════════════
@app.post("/api/chat", summary="调用 Hermes LLM")
async def chat(req: ChatReq):
    messages = [{"role": "system", "content": req.system}]
    messages.extend(req.history)
    messages.append({"role": "user", "content": req.prompt})

    target_model = req.model or HERMES_MODEL
    if req.agent_id and not req.model:
        row = db().execute("SELECT capabilities FROM agents WHERE agent_id=?", (req.agent_id,)).fetchone()
        if row:
            caps = json.loads(row["capabilities"] or "[]")
            for cap in caps:
                if cap.startswith("model:"):
                    target_model = cap[6:]
                    break

    if req.stream:
        return StreamingResponse(
            _stream_chat(messages, req.agent_id, target_model),
            media_type="text/event-stream"
        )

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(f"{OLLAMA_BASE}/api/chat", json={
                "model": target_model, "messages": messages, "stream": False,
            })
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
    except httpx.ConnectError:
        raise HTTPException(503, "Ollama 服务未启动，请先运行 `ollama serve`")
    except Exception as e:
        raise HTTPException(500, str(e))

    if req.agent_id:
        add_event(req.agent_id, None, f"LLM 推理完成 ({len(content)} chars)", "success")
        hot.interactions += 1
        hot.data_mb += len(content) / 1e6
        await ws_mgr.broadcast({"type": "llm_response", "data": build_stats()})
    return {"response": content, "model": target_model, "turns": len(req.history) + 1}


async def _stream_chat(messages: list, agent_id: Optional[str], model: Optional[str] = None) -> AsyncIterator[str]:
    target_model = model or HERMES_MODEL
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json={
                "model": target_model, "messages": messages, "stream": True,
            }) as resp:
                full = ""
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        full += token
                        yield f"data: {json.dumps({'token': token})}\n\n"
                        if chunk.get("done"):
                            yield f"data: {json.dumps({'done': True, 'total': len(full)})}\n\n"
                            break
                    except Exception:
                        continue
        if agent_id:
            add_event(agent_id, None, f"流式推理完成 ({len(full)} chars)", "success")
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


@app.get("/api/models", summary="列出 Ollama 模型")
async def list_models():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            return resp.json()
    except Exception as e:
        return {"models": [], "error": str(e)}


# ══════════════════════════════════════════════════════════
#  Routes: Stats, Events, Health
# ══════════════════════════════════════════════════════════
@app.get("/api/stats", summary="全局统计快照")
async def get_stats():
    return build_stats()


@app.get("/api/events", summary="协同事件流")
async def get_events(limit: int = Query(30, le=200)):
    # Merge in-memory recent + DB history
    mem_events = list(hot.events)[-limit:]
    return {"events": mem_events}


@app.get("/api/resources", summary="实时系统资源")
async def get_resources():
    return get_real_resources()


# ── Agent Logs ────────────────────────────────────────────
@app.post("/api/agents/{agent_id}/log", summary="写入 Agent 日志")
async def write_agent_log(agent_id: str, req: AgentLogReq):
    log_id = str(uuid.uuid4())[:8]
    ts = time.time()
    db().execute("INSERT INTO agent_logs VALUES (?,?,?,?,?,?)",
                 (log_id, agent_id, req.level, req.message, json.dumps(req.context), ts))
    db().commit()
    await ws_mgr.broadcast({"type": "agent_log", "agent_id": agent_id, "level": req.level, "message": req.message})
    return {"ok": True, "log_id": log_id}

@app.get("/api/agents/{agent_id}/logs", summary="查询 Agent 日志")
async def get_agent_logs(agent_id: str, level: str = "", limit: int = 50, offset: int = 0):
    sql = "SELECT * FROM agent_logs WHERE agent_id=?"
    params = [agent_id]
    if level:
        sql += " AND level=?"
        params.append(level)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = db().execute(sql, params).fetchall()
    return {"logs": [dict(r) for r in rows]}

@app.get("/api/stats/tokens", summary="Token 消耗统计")
async def get_token_stats():
    agents = db().execute("SELECT agent_id, name, metrics FROM agents").fetchall()
    return _compute_token_stats([dict(a) for a in agents])

# ── Peer Nodes ────────────────────────────────────────────
@app.post("/api/peers/join", summary="节点加入")
async def peer_join(req: dict):
    node_id = req.get("node_id", "")
    url = req.get("url", "")
    if not node_id or not url:
        raise HTTPException(400, "node_id and url required")
    now = now_ts()
    try:
        db().execute("INSERT INTO peer_nodes VALUES (?,?,?,?,?,?)",
                     (node_id, url, req.get("alias", ""), "online", now, now_iso()))
    except Exception:
        db().execute("UPDATE peer_nodes SET url=?, last_seen=?, status='online' WHERE node_id=?",
                     (url, now, node_id))
    db().commit()
    add_event("system", None, f"节点 {node_id} 加入", "info")
    return {"ok": True}

@app.get("/api/peers", summary="列出对等节点")
async def list_peers():
    return {"peers": _get_peer_list()}

@app.delete("/api/peers/{node_id}", summary="移除节点")
async def remove_peer(node_id: str):
    db().execute("DELETE FROM peer_nodes WHERE node_id=?", (node_id,))
    db().commit()
    return {"ok": True}

@app.post("/api/peers/{node_id}/sync", summary="同步对端 Agent")
async def sync_peer(node_id: str):
    row = db().execute("SELECT * FROM peer_nodes WHERE node_id=?", (node_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Node not found")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{row['url']}/api/agents")
            agents = resp.json().get("agents", [])
        for a in agents:
            remote_id = f"remote:{node_id}:{a['agent_id']}"
            caps = a.get("capabilities", []) + ["remote"]
            try:
                db().execute("INSERT INTO agents (agent_id,name,role,capabilities,description,status,tasks_completed,last_heartbeat,metrics,registered_at) VALUES (?,?,?,?,?,'idle',0,?,?,?)",
                             (remote_id, f"[{node_id}] {a['name']}", a.get("role","remote"), json.dumps(caps), "Remote agent", now_ts(), "{}", now_iso()))
            except Exception:
                pass
        db().commit()
        return {"synced": len(agents)}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/knowledge/global", summary="跨节点知识库汇总")
async def global_knowledge():
    local = db().execute("SELECT * FROM knowledge_bases").fetchall()
    result = {"local": [dict(r) for r in local], "remote": []}
    peers = db().execute("SELECT * FROM peer_nodes WHERE status='online'").fetchall()
    for p in peers:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                resp = await c.get(f"{p['url']}/api/knowledge")
                result["remote"].append({"node_id": p["node_id"], "kbs": resp.json().get("knowledge_bases", [])})
        except Exception:
            pass
    return result

@app.post("/api/knowledge/{kb_id}/broadcast", summary="广播知识库到对等节点")
async def broadcast_kb(kb_id: str):
    kb = db().execute("SELECT * FROM knowledge_bases WHERE kb_id=?", (kb_id,)).fetchone()
    if not kb:
        raise HTTPException(404, "KB not found")
    peers = db().execute("SELECT * FROM peer_nodes WHERE status='online'").fetchall()
    sent = 0
    for p in peers:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"{p['url']}/api/knowledge", json={"name": kb["name"], "type": kb["type"], "description": kb["description"]})
                sent += 1
        except Exception:
            pass
    return {"sent_to": sent}

# ── Task Intervention ─────────────────────────────────────
@app.post("/api/tasks/{task_id}/pause", summary="暂停任务")
async def pause_task(task_id: str):
    db().execute("UPDATE tasks SET status='paused' WHERE task_id=?", (task_id,))
    db().commit()
    add_event("orchestrator", None, f"任务 {task_id} 已暂停", "warning", task_id=task_id)
    await ws_mgr.broadcast({"type": "task_paused", "task_id": task_id, "data": build_stats()})
    return {"ok": True}

@app.post("/api/tasks/{task_id}/resume", summary="恢复任务")
async def resume_task(task_id: str):
    db().execute("UPDATE tasks SET status='pending' WHERE task_id=?", (task_id,))
    db().commit()
    add_event("orchestrator", None, f"任务 {task_id} 已恢复", "info", task_id=task_id)
    await ws_mgr.broadcast({"type": "task_resumed", "task_id": task_id, "data": build_stats()})
    return {"ok": True}

@app.post("/api/tasks/{task_id}/retry", summary="重试任务")
async def retry_task(task_id: str):
    db().execute("UPDATE tasks SET status='pending', retry_count=retry_count+1 WHERE task_id=?", (task_id,))
    db().commit()
    add_event("orchestrator", None, f"任务 {task_id} 重试", "warning", task_id=task_id)
    await ws_mgr.broadcast({"type": "task_retry", "task_id": task_id, "data": build_stats()})
    return {"ok": True}

@app.patch("/api/tasks/{task_id}/context", summary="编辑任务上下文")
async def update_task_context(task_id: str, req: TaskContextReq):
    db().execute("UPDATE tasks SET context_snapshot=?, status='pending' WHERE task_id=?",
                 (json.dumps(req.context_snapshot), task_id))
    db().commit()
    await ws_mgr.broadcast({"type": "task_context_updated", "task_id": task_id, "data": build_stats()})
    return {"ok": True}

@app.post("/api/tasks/{task_id}/reassign", summary="重新分配任务")
async def reassign_task(task_id: str, req: TaskReassignReq):
    db().execute("UPDATE tasks SET assigned_to=?, status='pending' WHERE task_id=?",
                 (req.agent_id, task_id))
    db().commit()
    add_event("orchestrator", req.agent_id, f"任务 {task_id} 重新分配", "info", task_id=task_id)
    await ws_mgr.broadcast({"type": "task_reassigned", "task_id": task_id, "data": build_stats()})
    return {"ok": True}

# ── Workflows ─────────────────────────────────────────────
@app.post("/api/workflows", summary="创建工作流")
async def create_workflow(req: WorkflowCreateReq):
    wf_id = f"wf-{str(uuid.uuid4())[:6]}"
    task_ids = []
    for t in req.tasks:
        tid = f"T-{str(uuid.uuid4())[:6]}"
        deps = req.dependencies.get(t.get("title", ""), [])
        db().execute(
            "INSERT INTO tasks (task_id,title,description,priority,assigned_to,depends_on,workflow_id,max_retries,context_snapshot,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, t.get("title",""), t.get("description",""), t.get("priority","P2"), t.get("assigned_to"),
             json.dumps(deps), wf_id, t.get("max_retries", DEFAULT_MAX_RETRIES),
             json.dumps(t.get("context_snapshot",{})), "pending", now_iso()))
        task_ids.append(tid)
    dag = {t: req.dependencies.get(t, []) for t in [t_.get("title","") for t_ in req.tasks]}
    db().execute("INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?)",
                 (wf_id, req.name, req.description, json.dumps(dag), "pending", "user", now_iso(), ""))
    db().commit()
    add_event("system", None, f"工作流 {req.name} 创建 ({len(task_ids)} 个任务)", "info")
    await ws_mgr.broadcast({"type": "workflow_created", "workflow_id": wf_id, "data": build_stats()})
    return {"workflow_id": wf_id, "task_ids": task_ids}

@app.get("/api/workflows/{wf_id}", summary="查看工作流")
async def get_workflow(wf_id: str):
    wf = db().execute("SELECT * FROM workflows WHERE workflow_id=?", (wf_id,)).fetchone()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    tasks = db().execute("SELECT * FROM tasks WHERE workflow_id=?", (wf_id,)).fetchall()
    return {"workflow": dict(wf), "tasks": [dict(t) for t in tasks]}

@app.post("/api/workflows/{wf_id}/pause", summary="暂停工作流")
async def pause_workflow(wf_id: str):
    db().execute("UPDATE workflows SET status='paused' WHERE workflow_id=?", (wf_id,))
    db().execute("UPDATE tasks SET status='paused' WHERE workflow_id=? AND status='pending'", (wf_id,))
    db().commit()
    await ws_mgr.broadcast({"type": "workflow_paused", "data": build_stats()})
    return {"ok": True}

@app.post("/api/workflows/{wf_id}/resume", summary="恢复工作流")
async def resume_workflow(wf_id: str):
    db().execute("UPDATE workflows SET status='running' WHERE workflow_id=?", (wf_id,))
    db().execute("UPDATE tasks SET status='pending' WHERE workflow_id=? AND status='paused'", (wf_id,))
    db().commit()
    await ws_mgr.broadcast({"type": "workflow_resumed", "data": build_stats()})
    return {"ok": True}


@app.get("/health", summary="健康检查")
async def health():
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        pass
    return {
        "status":        "ok",
        "version":       "2.0.0",
        "agents":        len(hot.agent_status),
        "tasks":         db().execute("SELECT COUNT(*) as c FROM tasks").fetchone()["c"],
        "ws_clients":    len(ws_mgr.clients),
        "ollama":        "ok" if ollama_ok else "unreachable",
        "db":            str(DB_PATH),
        "uptime_agents": sum(1 for s in hot.agent_status.values() if s != "offline"),
    }


# ══════════════════════════════════════════════════════════
#  WebSocket
# ══════════════════════════════════════════════════════════
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_mgr.connect(ws)
    await ws.send_json({"type": "connected", "data": build_stats()})
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=30)
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": now_ts()})
                elif msg.get("type") == "subscribe":
                    await ws.send_json({"type": "subscribed", "channels": msg.get("channels", [])})
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_mgr.disconnect(ws)
        log.info(f"WS disconnected ({len(ws_mgr.clients)} remain)")
