# TabShare Service

`tabshare/` 是主应用服务。它用 FastAPI 提供管理 API、viewer 页面和 WebSocket 输入通道，用 Playwright 管理容器内 Chrome，并把标签页媒体发布到 Go SFU。

## 职责

| 能力 | 说明 |
| --- | --- |
| 管理 API | 浏览器、标签页、工作台、录制和编码配置 |
| Viewer 页面 | `/viewer/{browser_id}/{tab_id}` 和 `/workbench/{workbench_id}` |
| 输入通道 | WebSocket 接收鼠标、键盘、滚动、导航、文件粘贴 |
| Browser runtime | Playwright 启动 Chrome，维护持久浏览器和标签页 |
| Publisher | 在页面中创建 WebRTC publisher，并把 SDP offer 转给 SFU |
| 数据持久化 | SQLAlchemy/asyncpg 连接 PostgreSQL |

## 架构

```text
FastAPI
  ├─ management API ── PostgreSQL
  ├─ WebSocket input ── Playwright/Chrome
  ├─ static viewer/admin
  └─ SFU proxy ── http://127.0.0.1:8088

Chrome tab
  └─ getDisplayMedia({ preferCurrentTab: true })
      └─ WebRTC publisher ── Go SFU ── Viewer
```

## 容器构建方式

`tabshare/Dockerfile` 使用 Playwright Python 基础镜像，并在 image build 阶段完成：

| 步骤 | 说明 |
| --- | --- |
| 安装 `uv` | 从 `ghcr.io/astral-sh/uv` 复制二进制 |
| 安装 Chrome Stable | 优先使用 `data/opt/deb/google-chrome-stable_current_amd64.deb` |
| 安装 Python 依赖 | 通过 `uv pip install --system ...` 安装运行依赖 |
| 创建数据目录 | `/data/browsers`、`/data/recordings`、`/data/downloads`、`/data/uploads` |

该镜像不复制完整源码。根 `docker-compose.yml` 会把宿主机 `./tabshare` 挂载到容器 `/app`：

```yaml
volumes:
  - ./tabshare:/app
  - ./tabshare/data:/data
```

因此 Python 代码不是容器启动时编译出来的，而是运行时从挂载目录解释执行。

## 运行

推荐从仓库根目录启动：

```sh
./start.sh cpu
```

也可以直接使用 compose：

```sh
docker compose up -d --build tabshare
```

访问入口：

| 路由 | 说明 |
| --- | --- |
| `/admin` | 管理后台，优先加载 `server/static/admin-spa` |
| `/viewer/{browser_id}/{tab_id}` | 单标签页 viewer |
| `/workbench/{workbench_id}` | 工作台 viewer |

## 硬件加速镜像

| Dockerfile | 用途 |
| --- | --- |
| `Dockerfile` | CPU/通用基础镜像 |
| `Dockerfile.intel` | 基于 `bs-tabshare` 安装 Intel VAAPI/Vulkan 依赖 |
| `Dockerfile.amd` | 基于 `bs-tabshare` 安装 AMD Mesa VAAPI 依赖 |

根目录 `start.sh` 负责组合构建：

```sh
./start.sh intel
./start.sh amd
```

GPU 模式会叠加 `docker-compose.gpu.yml`，挂载 `/dev/dri` 并设置 `USE_HARDWARE_GPU=true`。

## 管理后台静态产物

`tabshare/admin-ui/` 不由 `tabshare/Dockerfile` 构建。改动管理后台后必须执行：

```sh
cd admin-ui
npm ci
npm run build
```

产物会写入：

```text
server/static/admin-spa/
```

## 关键环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SFU_INTERNAL_URL` | `http://127.0.0.1:8088` | FastAPI 访问 SFU 的内部地址 |
| `DATABASE_URL` | compose 中配置 | PostgreSQL 连接串 |
| `VIDEO_CODEC` | `h264` | Publisher SDP 编码偏好：`h264`、`vp8`、`vp9`、`av1` |
| `USE_HARDWARE_GPU` | 空 | GPU 模式开关 |
| `CHROME_PATH` | `/opt/google/chrome/google-chrome` | Chrome 可执行文件 |

## 目录

```text
server/
├── app.py                 # FastAPI 入口、静态文件、SFU proxy
├── config.py              # 环境变量配置
├── api/
│   ├── management.py      # 管理 API
│   └── ws.py              # viewer/workbench WebSocket
├── services/
│   ├── browser_instance.py
│   ├── browser_manager.py
│   ├── database.py
│   ├── tab_publisher.py
│   └── workbench_runtime.py
└── static/
    ├── admin-spa/         # React 构建产物
    ├── js/viewer.js
    ├── viewer.html
    └── workbench.html
```
