#!/bin/bash
# 清理残留锁文件
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# 启动 Xvfb 虚拟显示器
Xvfb ${DISPLAY:-:99} -screen 0 1920x1080x24 -ac -nolisten tcp &
XVFB_PID=$!
sleep 1

# 检查 Xvfb 是否启动成功
if kill -0 $XVFB_PID 2>/dev/null; then
    echo "[Xvfb] Started on ${DISPLAY:-:99} (pid=$XVFB_PID)"
else
    echo "[Xvfb] Failed to start, retrying..." >&2
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
    Xvfb ${DISPLAY:-:99} -screen 0 1920x1080x24 -ac -nolisten tcp &
    XVFB_PID=$!
    sleep 1
    if kill -0 $XVFB_PID 2>/dev/null; then
        echo "[Xvfb] Started on retry (pid=$XVFB_PID)"
    else
        echo "[Xvfb] Failed to start!" >&2
        exit 1
    fi
fi

# 启动 Python 应用
exec python -u -m server.app
