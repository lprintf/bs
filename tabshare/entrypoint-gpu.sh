#!/bin/bash
# GPU 模式启动脚本
# Weston headless + GL renderer → 提供 Wayland socket → Chrome 硬件 EGL
set -e

# ===== 1. 设备权限 =====
if [ -d /dev/dri ]; then
    chmod 666 /dev/dri/* || true
    echo "[entrypoint] /dev/dri permissions set"
    ls -la /dev/dri/
fi

# ===== 2. 启动 DBus =====
mkdir -p /var/run/dbus /var/lib/dbus
dbus-uuidgen > /var/lib/dbus/machine-id 2>/dev/null || true
dbus-daemon --system --fork 2>/dev/null || true
echo "[entrypoint] dbus started"

# ===== 3. 准备 XDG_RUNTIME_DIR =====
export XDG_RUNTIME_DIR=/run/user/1000
mkdir -p "$XDG_RUNTIME_DIR"
chown ubuntu:ubuntu "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# 清理残留 socket
rm -f "$XDG_RUNTIME_DIR"/wayland-* 2>/dev/null || true

# ===== 4. 启动 Weston (headless + GL renderer) =====
echo "[entrypoint] Starting Weston (headless + GL renderer)..."

# 先尝试 GL renderer (GPU 加速)
su -s /bin/bash ubuntu -c "
    export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
    weston --config=/home/ubuntu/.config/weston.ini 2>/tmp/weston.log &
" &
sleep 3

# 检查 Wayland socket
SOCKET=$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -1)

if [ -n "$SOCKET" ]; then
    echo "[entrypoint] Weston GL renderer started successfully"
    # 检查是否真的用了 GL renderer
    if grep -q "GL renderer" /tmp/weston.log 2>/dev/null; then
        echo "[entrypoint] ✅ GL renderer confirmed (GPU accelerated)"
    elif grep -q "pixman" /tmp/weston.log 2>/dev/null; then
        echo "[entrypoint] ⚠️  Fell back to pixman (software), GL renderer failed"
    fi
    echo "[entrypoint] Weston log (first 10 lines):"
    head -10 /tmp/weston.log 2>/dev/null || true
else
    echo "[entrypoint] Weston GL failed, trying headless without GL..."
    cat /tmp/weston.log 2>/dev/null || true

    # 回退: headless + pixman (软件渲染)
    su -s /bin/bash ubuntu -c "
        export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
        weston --backend=headless --no-config --width=1920 --height=1080 2>/tmp/weston-fallback.log &
    " &
    sleep 2
    SOCKET=$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -1)

    if [ -n "$SOCKET" ]; then
        echo "[entrypoint] Weston headless (pixman) started as fallback"
    else
        echo "[entrypoint] WARNING: Weston failed entirely, running without Wayland"
        cat /tmp/weston-fallback.log 2>/dev/null || true
        exec su -s /bin/bash ubuntu -c "python -u -m server.app"
    fi
fi

WAYLAND_DISPLAY=$(basename "$SOCKET")
echo "[entrypoint] Weston ready: WAYLAND_DISPLAY=$WAYLAND_DISPLAY"

# ===== 5. 启动应用 =====
exec su -s /bin/bash ubuntu -c "
    export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
    export WAYLAND_DISPLAY=$WAYLAND_DISPLAY
    python -u -m server.app
"
