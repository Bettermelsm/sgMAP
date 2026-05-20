#!/bin/bash
# Multi-Agent Platform v2 — 一键启动脚本 (macOS / Linux)
set -e

PLATFORM_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=${PORT:-9527}

echo "╔══════════════════════════════════════════════╗"
echo "║  Multi-Agent 智能体协同平台 v3  启动脚本      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── 1. Check Ollama ──────────────────────────────────────
echo "▸ 检查 Ollama..."
if ! command -v ollama &>/dev/null; then
  echo "  ✗ 未找到 ollama，请先安装: https://ollama.com"
  echo "    macOS: brew install ollama"
  exit 1
fi
if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
  echo "  → 启动 Ollama 后台服务..."
  ollama serve &>/tmp/ollama.log &
  sleep 3
fi
echo "  ✓ Ollama 就绪"

# ── 2. Check Hermes model ────────────────────────────────
HERMES_MODEL=${HERMES_MODEL:-hermes3}
echo "▸ 检查模型 $HERMES_MODEL..."
if ! ollama list 2>/dev/null | grep -q "$HERMES_MODEL"; then
  echo "  → 拉取 $HERMES_MODEL (首次可能需要几分钟)..."
  ollama pull "$HERMES_MODEL"
fi
echo "  ✓ 模型就绪"

# ── 3. Python venv ───────────────────────────────────────
echo "▸ 配置 Python 环境..."
if [ ! -d "$PLATFORM_DIR/.venv" ]; then
  python3 -m venv "$PLATFORM_DIR/.venv"
fi
source "$PLATFORM_DIR/.venv/bin/activate"
pip install -q fastapi uvicorn httpx python-multipart psutil aiofiles
echo "  ✓ Python 依赖就绪"

# ── 4. Start backend ─────────────────────────────────────
echo "▸ 启动 FastAPI 后端 (port $PORT)..."
cd "$PLATFORM_DIR"
HERMES_MODEL="$HERMES_MODEL" uvicorn main:app \
  --host 0.0.0.0 --port "$PORT" \
  --reload --log-level info &
BACKEND_PID=$!
sleep 2

if curl -s "http://localhost:$PORT/health" &>/dev/null; then
  echo "  ✓ 后端就绪 → http://localhost:$PORT"
  echo "  ✓ API 文档 → http://localhost:$PORT/docs"
else
  echo "  ✗ 后端启动失败，查看日志"
  exit 1
fi

# ── 5. Open dashboard ────────────────────────────────────
echo "▸ 打开看板..."
DASHBOARD="http://localhost:$PORT"
if command -v open &>/dev/null; then
  open "$DASHBOARD"
elif command -v xdg-open &>/dev/null; then
  xdg-open "$DASHBOARD"
fi

# ── Summary ──────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  🚀 平台启动成功！                             ║"
echo "║                                              ║"
echo "║  看板:    http://localhost:$PORT              ║"
echo "║  API:     http://localhost:$PORT/docs          ║"
echo "║  WS:      ws://localhost:$PORT/ws              ║"
echo "║  DB:      $PLATFORM_DIR/platform.db           ║"
echo "║                                              ║"
echo "║  运行演示: python demo_agents.py              ║"
echo "║  按 Ctrl+C 停止                               ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

cleanup() {
  echo ""
  echo "▸ 停止服务..."
  kill "$BACKEND_PID" 2>/dev/null || true
  echo "✓ 已停止"
  exit 0
}
trap cleanup INT TERM
wait "$BACKEND_PID"
