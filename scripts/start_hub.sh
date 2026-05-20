#!/bin/bash
set -e

# ── 加载配置 ──
if [ -f "config/hub.env" ]; then
    export $(grep -v '^#' config/hub.env | grep -v '^$' | xargs)
fi

# ── 安装依赖 ──
pip install fastapi uvicorn httpx psutil aiofiles python-multipart -q

# ── 初始化目录 ──
mkdir -p shared/knowledge shared/skills shared/task_outputs
mkdir -p task_files logs

# ── 克隆或更新共享仓库 ──
if [ -n "$SGA_SHARED_REPO" ]; then
    if [ -d "shared/.git" ]; then
        echo "Updating shared repo..."
        git -C shared pull
    else
        echo "Cloning shared repo..."
        git clone "$SGA_SHARED_REPO" shared
    fi
fi

# ── 启动 ──
PORT=${SGA_PORT:-9527}
echo "SGA Hub starting on 0.0.0.0:$PORT"
uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --log-level info
