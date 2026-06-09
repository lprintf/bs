# BS / TabShare

TabShare 是一个容器化浏览器标签页实时共享系统。主链路由 FastAPI 管理 Playwright/Chrome，Go SFU 转发 WebRTC RTP，PostgreSQL 保存浏览器、标签页和工作台状态。

## 子模块

| 路径 | 职责 | 构建方式 | 运行入口 |
| --- | --- | --- | --- |
| `docker-compose.yml` | 编排 `postgres`、`sfu`、`tabshare` | Compose 触发各镜像构建 | `docker compose up -d` |
| `docker-compose.gpu.yml` | GPU 设备挂载和 VAAPI 环境覆盖 | 与主 compose 叠加 | `./start.sh intel` 或 `./start.sh amd` |
| `sfu/` | Go WebRTC SFU、RTP 转发、WebM 录制 | 在 `sfu/Dockerfile` 的 `golang:1.24-alpine` build stage 内编译 | 容器内 `/usr/local/bin/sfu` |
| `tabshare/` | FastAPI 服务、Playwright 浏览器管理、静态页面服务 | `tabshare/Dockerfile` 安装 Python 依赖；源码由 compose 挂载到 `/app` | `python -u -m server.app` |
| `tabshare/admin-ui/` | React/Vite 管理后台源码 | 本地 Node 构建，产物写入 `tabshare/server/static/admin-spa` | FastAPI `/admin` 静态托管 |
| `scripts/` | 诊断和依赖下载脚本 | 不构建 | 手动执行 |
| `docs/` | 架构、变更、开发规范 | 不构建 | Markdown 文档 |

本文中的“容器内编译”指 Docker image build 阶段发生在容器里的编译，不是容器启动后动态编译。当前只有 `sfu/` 的 Go 二进制是这样生成的；`tabshare/` 是容器内安装依赖后解释执行 Python；`tabshare/admin-ui/` 需要在 Node 环境中提前构建静态产物。

## 标准启动

```sh
./start.sh cpu
```

等价于构建并启动主 compose：

```sh
docker compose up -d --build
```

GPU 模式：

```sh
./start.sh intel
./start.sh amd
```

`start.sh` 会先构建通用 `bs-tabshare` 镜像，再按硬件类型构建 `bs-tabshare-intel` 或 `bs-tabshare-amd`，并叠加 `docker-compose.gpu.yml` 挂载 `/dev/dri`。

## 前端构建

管理后台改动后需要手动构建：

```sh
cd tabshare/admin-ui
npm ci
npm run build
```

构建产物输出到：

```text
tabshare/server/static/admin-spa/
```

FastAPI 会优先从该目录托管 `/admin`。

## 模块文档

| 文档 | 说明 |
| --- | --- |
| [sfu/README.md](./sfu/README.md) | SFU 和录制器说明 |
| [tabshare/README.md](./tabshare/README.md) | FastAPI/Playwright 服务说明 |
| [tabshare/admin-ui/README.md](./tabshare/admin-ui/README.md) | 管理后台构建说明 |
| [scripts/README.md](./scripts/README.md) | 脚本说明 |
| [docs/readme.md](./docs/readme.md) | 设计文档索引 |
