#!/bin/bash
set -e

# ── 加载配置 ──
if [ -f "config/agent.env" ]; then
    export $(grep -v '^#' config/agent.env | grep -v '^$' | xargs)
fi

if [ -z "$SGA_HUB_URL" ]; then
    echo "Please set SGA_HUB_URL in config/agent.env"
    exit 1
fi

echo "Connecting to Hub: $SGA_HUB_URL"
python3 scripts/demo_agents.py "$SGA_HUB_URL"
