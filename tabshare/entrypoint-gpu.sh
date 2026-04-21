#!/bin/bash
# GPU 模式启动脚本：先启动 Xvfb 虚拟显示，再启动应用
set -e

# 清理上次残留的锁文件
rm -f /tmp/.X99-lock /tmp/.X99-unix/X99 2>/dev/null || true

# 启动 Xvfb 虚拟显示
Xvfb :99 -screen 0 1280x720x24 +extension GLX -nolisten tcp &
XVFB_PID=$!
sleep 0.5

# 检查 Xvfb 是否启动成功
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "[entrypoint] ERROR: Xvfb failed to start"
    exit 1
fi

export DISPLAY=:99
echo "[entrypoint] Xvfb started on DISPLAY=$DISPLAY (PID=$XVFB_PID)"

# exec 替换当前进程，确保信号传递和 Docker logs 正常
exec python -u -m server.app
