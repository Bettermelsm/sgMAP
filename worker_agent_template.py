"""
worker_agent_template.py
========================
sgMAP Worker Agent 模板
每个计算节点复制此文件并按注释修改后运行。

部署位置：各 Worker 节点（SGlcl01 / SGlcl03 / SGcloud01 / LKcloud01）
运行方式：python3 worker_agent.py
"""

import asyncio
import logging
import os
from agent_sdk import AgentClient  # 根据实际 role 可改为 AnalystAgent / CoderAgent 等

# ═══════════════════════════════════════════════════════════════════════════
# ★ 按节点修改以下配置 ★
# ═══════════════════════════════════════════════════════════════════════════

# sgMAP Hub 连接（兼容 SGA_* 和 SGMAP_* 两种命名，SGA_* 优先）
HUB_URL   = os.getenv("SGA_HUB_URL") or os.getenv("SGMAP_HUB_URL", "http://192.168.100.209:9527")
API_KEY   = os.getenv("SGA_API_KEY") or os.getenv("SGMAP_API_KEY", "your-sgmap-api-key-here")

# 本节点身份（参考 skcl/AGENTS.md）
NODE_NAME    = "SGlcl01@sengene"      # ← 改为本节点名称
NODE_ROLE    = "analyzer"             # ← planner / coder / analyzer / retriever / evaluator
CAPABILITIES = [                       # ← 与 AGENTS.md 中完全一致
    "数据分析",
    "scRNA-seq",
    "WGCNA",
    "分子对接",
    "生信计算",
    "统计分析",
]

HEARTBEAT_INTERVAL = 30   # 秒
POLL_INTERVAL      = 10   # 秒，收件箱轮询间隔

# ═══════════════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(NODE_NAME)


# ═══════════════════════════════════════════════════════════════════════════
# ★ 任务执行逻辑（按节点实际能力实现）★
# ═══════════════════════════════════════════════════════════════════════════

async def execute_task(agent: AgentClient, task: dict) -> tuple[str, dict]:
    """
    执行具体任务，返回 (result_text, metadata)。

    task 字段说明：
      task["task_id"]    — 任务 ID
      task["title"]      — 任务标题
      task["description"]— 任务详细描述（含执行步骤）
      task["priority"]   — P0/P1/P2/P3
      task["context"]    — 附加上下文（可选）

    ★ 在此处添加本节点的实际执行逻辑，例如：
      - 调用 R/Python 生信分析脚本
      - 执行 AutoDock 分子对接
      - 运行 DeepLabCut 行为学分析
      - 调用 Hermes 生成代码
      - 执行 Windows 专用工具（SGlcl03）
    """
    title = task.get("title", "未命名任务")
    desc  = task.get("description", "")
    log.info(f"开始执行：{title}")

    # ── 示例：根据任务描述关键词路由到不同执行函数 ──────────────────────
    if "scRNA-seq" in desc or "单细胞" in desc:
        result = await _run_scrna(task)
    elif "WGCNA" in desc:
        result = await _run_wgcna(task)
    elif "分子对接" in desc or "AutoDock" in desc:
        result = await _run_docking(task)
    else:
        # 通用处理：记录任务并返回（后续可扩展）
        result = f"任务已接收：{title}\n描述：{desc[:200]}"
        log.info(f"通用处理完成：{title}")

    return result, {"node": NODE_NAME, "role": NODE_ROLE}


# ── 具体执行函数（按需实现）─────────────────────────────────────────────

async def _run_scrna(task: dict) -> str:
    """scRNA-seq 分析 pipeline（示例，需替换为实际调用）"""
    # 示例：调用外部 Python 脚本
    import subprocess
    desc = task.get("description", "")

    # 从描述中提取参数（实际使用时可用 LLM 解析）
    # proc = await asyncio.create_subprocess_exec(
    #     "python3", "scripts/run_seurat.py",
    #     "--input", geo_id,
    #     "--output", output_dir,
    #     stdout=asyncio.subprocess.PIPE,
    #     stderr=asyncio.subprocess.PIPE,
    # )
    # stdout, stderr = await proc.communicate()

    log.info("scRNA-seq 分析（占位实现，请替换为实际脚本调用）")
    return "scRNA-seq 分析完成（示例输出，请替换为实际结果）"


async def _run_wgcna(task: dict) -> str:
    """WGCNA 分析（示例）"""
    log.info("WGCNA 分析（占位实现）")
    return "WGCNA 分析完成（示例输出）"


async def _run_docking(task: dict) -> str:
    """分子对接（示例）"""
    log.info("分子对接（占位实现）")
    return "分子对接完成（示例输出）"


# ═══════════════════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    log.info(f"Worker Agent 启动：{NODE_NAME}（{NODE_ROLE}）")
    log.info(f"Hub：{HUB_URL}")
    log.info(f"能力：{CAPABILITIES}")

    agent = AgentClient(
        name=NODE_NAME,
        role=NODE_ROLE,
        capabilities=CAPABILITIES,
        platform_url=HUB_URL,
        api_key=API_KEY,
        heartbeat_interval=HEARTBEAT_INTERVAL,
    )

    await agent.register()
    log.info("已注册到 Hub，开始监听任务...")

    try:
        while True:
            try:
                inbox = await agent.get_inbox()
                for task in inbox:
                    task_id = task.get("task_id") or task.get("id")
                    title   = task.get("title", "")
                    log.info(f"收到任务 [{task_id}]：{title}")

                    try:
                        result, metadata = await execute_task(agent, task)
                        await agent.complete_task(task_id, result, metadata)
                        log.info(f"任务完成 [{task_id}]")
                    except Exception as e:
                        log.error(f"任务失败 [{task_id}]：{e}", exc_info=True)
                        await agent.complete_task(
                            task_id,
                            f"执行失败：{str(e)}",
                            {"error": True, "node": NODE_NAME},
                        )
            except Exception as e:
                log.error(f"主循环错误：{e}", exc_info=True)

            await asyncio.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log.info("收到中断信号，正在退出...")
    finally:
        await agent.stop()
        log.info(f"{NODE_NAME} 已停止")


if __name__ == "__main__":
    asyncio.run(main())
