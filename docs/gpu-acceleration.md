# Intel iGPU 硬件加速探索日志

> 记录容器内 Intel 核显 (iGPU) 加速的完整探索过程，包括成功和失败的实验。

## 目标

在 Docker 容器内实现：
1. **VAAPI H.264 硬件编码** — 降低 CPU 占用，提升流媒体推送性能
2. **WebGL 硬件渲染** — 页面 GPU 渲染加速（CSS 3D、Canvas 等）

## 结论

| 功能 | 状态 | 方案 |
|---|---|---|
| VAAPI H.264 编码 | ✅ 已实现 | `intel-media-va-driver-non-free` + `renderD128` |
| WebGL 渲染 | ⚠️ 软件渲染 | SwiftShader (Chrome 自带) |
| GPU 3D 硬件渲染 | ❌ 不可行 | 需要 VT/seat，Docker 容器不支持 |

**核心发现**：VAAPI 视频编码和 WebGL 3D 渲染走的是**完全不同的路径**：

| | VAAPI 编码 | WebGL/3D 渲染 |
|---|---|---|
| 设备 | `/dev/dri/renderD128` | `/dev/dri/card1` |
| 需要 DRM master | ❌ | ✅ |
| 需要 VT/seat | ❌ | ✅ |
| Docker 容器可用 | ✅ | ❌ |

## 分支历史

```
main
 └─ feat/h264-hwaccel      ← VAAPI 编码 ✅ (可合入)
     └─ feat/xvfb-gpu      ← Xvfb 实验 ❌
         └─ feat/wayland-gpu ← Weston/Wayland 实验 ❌ → SwiftShader 最终方案
```

---

## 实验 1: VAAPI H.264 硬件编码 ✅

**分支**: `feat/h264-hwaccel`

### 变更

- **Dockerfile.intel**: 安装 `intel-media-va-driver-non-free`, `libigdgmm12`, `vainfo`
- **docker-compose.gpu.yml**: 挂载 `/dev/dri`, 添加 `group_add: ["992"]` (render 组)
- **browser_instance.py**: GPU 检测 + Chrome VAAPI flags
- **tab_publisher.py**: `frameRate: 30`, `contentHint: 'detail'`, `maxBitrate: 4Mbps`
- **sfu/main.go**: PLI 按需触发 + 10s 兜底, RTCP 转发

### Chrome GPU 参数

```python
GPU_ACCEL_ARGS = [
    "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder,VaapiVideoEncodeLinuxGL,UseOzonePlatform",
    "--ozone-platform=headless",
    "--disable-features=UseChromeOSDirectVideoDecoder",
    "--enable-accelerated-video-decode",
    "--use-gl=egl",
    "--enable-gpu-rasterization",
    "--disable-gpu-sandbox",
]
```

### 验证结果

```bash
# GPU 进程加载了 iHD 驱动
$ cat /proc/<gpu_pid>/maps | grep iHD
7f...  /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so

# GPU 进程打开了 renderD128
$ ls -la /proc/<gpu_pid>/fd | grep dri
lrwx... -> /dev/dri/renderD128

# VAAPI 编码支持
$ vainfo 2>&1 | grep H.264
VAProfileH264Main  : VAEntrypointEncSlice
```

### 遗留问题

- WebGL 不可用：`--headless=new` + `--ozone-platform=headless` 模式下 EGL 初始化失败
- Chrome GPU 进程显示 `--use-gl=disabled`

---

## 实验 2: Xvfb 虚拟显示 ❌

**分支**: `feat/xvfb-gpu`

### 思路

用 Xvfb 提供 X11 虚拟显示，让 Chrome 完整初始化 EGL/GPU。

### 尝试

1. `--use-gl=egl` + Xvfb → EGL 初始化失败
2. `--use-gl=desktop` (GLX) + Xvfb → GLX 也失败

### 失败原因

**Xvfb 是纯软件虚拟显示**，不连接物理 GPU。无论 EGL 还是 GLX，底层都没有硬件 GL 实现。

### 额外发现

- **空白页问题**: 非 headless 模式关闭所有页面 → Chrome 进程退出
- **修复**: 保留一个 `about:blank` 页面作为 keepalive

---

## 实验 3: Weston Wayland Compositor ❌

**分支**: `feat/wayland-gpu`

### 尝试 3a: Weston headless backend

```ini
[core]
backend=headless
```

**结果**: 失败。headless backend 也是纯软件渲染，和 Xvfb 同理。

### 尝试 3b: Weston DRM backend

```ini
[core]
backend=drm  # 自动检测
```

**结果**: 失败。错误日志：

```
[seatd/seat.c:222] Could open device: client is not active
[seatd/client.c:238] Could not open device: Operation not permitted
no drm device found
fatal: failed to create compositor backend
```

**失败原因**:

Weston DRM backend 需要：
1. **VT (虚拟终端)** — Docker 容器没有 `/dev/tty0`
2. **seat 管理** — 需要 seatd/logind, 容器内无法正常工作
3. **DRM master** — 需要通过 seat 系统获取，而非直接打开设备

### 尝试 3c: EGL_PLATFORM=surfaceless

设置环境变量 `EGL_PLATFORM=surfaceless`，尝试让 Chrome 直接用 render node 做 EGL。

**结果**: 失败。Chrome 不支持 surfaceless EGL 用于 WebGL。

### 尝试 3d: --ignore-gpu-blocklist

**结果**: 失败。问题不是 blocklist，而是 EGL 根本无法初始化。

### waypipe 参考

参考了 `../antigravity` 项目，它通过 waypipe 实现了 GPU 加速。分析后发现：

> **waypipe 的 GPU 渲染实际发生在客户端机器**，不是容器内。容器只发送 Wayland 协议，客户端的 compositor 做实际 GPU 渲染。

这意味着 waypipe 方案需要外部客户端，不适用于 headless 流媒体场景。

---

## 最终方案: SwiftShader + VAAPI ✅

### 架构

```
┌─────────────────────────────────────────────┐
│ Docker Container                             │
│                                              │
│  Chrome Browser                              │
│  ├── WebGL 渲染 → SwiftShader (CPU, 软件)    │
│  ├── 页面合成   → CPU                        │
│  └── H.264 编码 → VAAPI (GPU, renderD128) ✅ │
│                                              │
└─────────────────────────────────────────────┘
```

### 配置

**Dockerfile.intel**:
```dockerfile
FROM bs-tabshare
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    intel-media-va-driver-non-free libigdgmm12 vainfo \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
ENV LIBVA_DRIVER_NAME=iHD
USER ubuntu
CMD ["python", "-u", "-m", "server.app"]
```

**docker-compose.gpu.yml**:
```yaml
services:
  tabshare:
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "992"   # render group
    environment:
      - USE_HARDWARE_GPU=true
```

**Chrome 参数**:
```python
GPU_ACCEL_ARGS = [
    "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder,VaapiVideoEncodeLinuxGL,UseOzonePlatform",
    "--ozone-platform=headless",
    "--disable-features=UseChromeOSDirectVideoDecoder",
    "--enable-accelerated-video-decode",
    "--use-gl=angle",
    "--use-angle=swiftshader-webgl",
    "--enable-unsafe-swiftshader",
    "--enable-gpu-rasterization",
    "--enable-zero-copy",
    "--disable-gpu-sandbox",
    "--ignore-gpu-blocklist",
]
```

### 性能说明

- **H.264 编码**: GPU 硬件加速，大幅降低 CPU 占用
- **WebGL**: 软件渲染 (SwiftShader)，性能足够一般页面
- **合成/光栅化**: CPU，headless 模式下影响不大
- **最大收益**: 在于 VAAPI 编码，这是流媒体场景的瓶颈

---

## 环境信息

- **宿主机**: Ubuntu, kernel 7.0.0-14-generic
- **GPU**: Intel iGPU (iHD 驱动)
- **容器基础镜像**: Ubuntu 24.04
- **Chrome**: Playwright 管理的 Chromium
- **Weston**: 13.0.0
