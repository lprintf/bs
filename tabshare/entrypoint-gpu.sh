#!/bin/bash
# GPU 模式启动脚本
# headless Chrome + ANGLE Vulkan → 直接通过 render node 用 GPU
# VAAPI H.264 硬件编码 + WebGL 2.0 硬件渲染
set -e

# ===== 1. 设备权限 =====
if [ -d /dev/dri ]; then
    chmod 666 /dev/dri/* || true
    echo "[entrypoint] /dev/dri permissions set"
    ls -la /dev/dri/
fi

# ===== 2. 启动 DBus (Chrome 需要) =====
mkdir -p /var/run/dbus /var/lib/dbus
dbus-uuidgen > /var/lib/dbus/machine-id 2>/dev/null || true
dbus-daemon --system --fork 2>/dev/null || true
echo "[entrypoint] dbus started"

# ===== 3. 恢复 Chrome ANGLE EGL (如果被之前的版本替换过) =====
CHROME_DIR="/opt/google/chrome"
if [ -f "$CHROME_DIR/libEGL.so.angle.bak" ]; then
    echo "[entrypoint] Restoring Chrome ANGLE EGL..."
    mv -f "$CHROME_DIR/libEGL.so.angle.bak" "$CHROME_DIR/libEGL.so"
    mv -f "$CHROME_DIR/libGLESv2.so.angle.bak" "$CHROME_DIR/libGLESv2.so"
fi

# ===== 4. 启动应用 =====
echo "[entrypoint] Starting app (headless + ANGLE Vulkan + VAAPI)"
exec su -s /bin/bash ubuntu -c "python -u -m server.app"
