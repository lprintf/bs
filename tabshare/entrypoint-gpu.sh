#!/bin/bash
# GPU 模式启动脚本 (参考 antigravity 项目)
# 1. 启动 dbus  2. 设备权限  3. Weston  4. 应用 (ubuntu 用户)
set -e

# ===== 1. 设备权限 =====
if [ -d /dev/dri ]; then
    chmod 666 /dev/dri/* || true
    echo "[entrypoint] /dev/dri permissions set"
fi

# ===== 2. 启动 DBus (Chrome GPU 初始化需要) =====
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

# ===== 4. 启动 Weston (以 ubuntu) =====
# 先试 DRM backend (直接 GPU)，失败回退 headless
echo "[entrypoint] Starting Weston..."
su -s /bin/bash ubuntu -c "
    export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
    weston --config=/home/ubuntu/.config/weston.ini 2>/tmp/weston.log &
" &
sleep 2

# 检查 Wayland socket
SOCKET=$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -1)

if [ -z "$SOCKET" ]; then
    echo "[entrypoint] Weston auto-detect failed, trying headless backend..."
    cat /tmp/weston.log 2>/dev/null || true

    su -s /bin/bash ubuntu -c "
        export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
        weston --backend=headless --no-config --width=1280 --height=720 2>/tmp/weston-headless.log &
    " &
    sleep 2
    SOCKET=$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -1)
fi

if [ -z "$SOCKET" ]; then
    echo "[entrypoint] WARNING: Weston failed entirely, running without Wayland"
    cat /tmp/weston-headless.log 2>/dev/null || true
    exec su -s /bin/bash ubuntu -c "python -u -m server.app"
fi

WAYLAND_DISPLAY=$(basename "$SOCKET")
echo "[entrypoint] Weston ready: WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
echo "[entrypoint] Weston log:"
cat /tmp/weston.log 2>/dev/null | head -5 || true

# ===== 5. 启动应用 (ubuntu 用户，继承环境) =====
exec su -s /bin/bash ubuntu -c "
    export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR
    export WAYLAND_DISPLAY=$WAYLAND_DISPLAY
    python -u -m server.app
"
