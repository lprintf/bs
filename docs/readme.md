# TabShare 文档

基于 Playwright + WebRTC 的容器化浏览器标签页实时共享系统。

## 架构概览

```mermaid
graph LR
    subgraph Docker
        PW[Playwright] --> Chrome[Headless Chromium]
        Chrome -->|getDisplayMedia| Stream[视频流]
        Stream -->|WebRTC P2P| Viewer
        FastAPI -->|信令转发| Chrome
        FastAPI -->|CDP Input| Chrome
    end
    Viewer[用户浏览器] <-->|WebSocket 信令| FastAPI
    Viewer <-.->|WebRTC RTP| Chrome
```

## 文档索引

### 模块说明

| 文档 | 说明 |
|------|------|
| [项目根 README](../README.md) | 子模块职责、构建方式和启动入口 |
| [SFU](../sfu/README.md) | Go SFU、容器内编译和录制目录 |
| [TabShare Service](../tabshare/README.md) | FastAPI/Playwright 服务、容器运行方式 |
| [Admin UI](../tabshare/admin-ui/README.md) | React/Vite 管理后台构建和产物位置 |
| [Scripts](../scripts/README.md) | 运维脚本和诊断脚本 |

### 技术方案

| 文档 | 说明 |
|------|------|
| [v0 — CDP 图片传输](./design/v0-图片传输标签页共享.md) | 初版方案：CDP screencast + base64 图片推送 |
| [v1 — WebRTC 原生共享](./design/v1-webrtc标签页共享.md) | 当前方案：Chrome 原生 WebRTC + CDP 输入 |

### 版本记录

| 文档 | 说明 |
|------|------|
| [changelog](./changelog/) | 各版本变更日志 |
