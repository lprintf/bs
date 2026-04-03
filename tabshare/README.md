# TabShare - WebRTC Tab Sharing

基于 WebRTC 的浏览器标签页实时共享系统。

## 架构

```
Headless Chrome (Playwright)
    ├── Tab 1 → getDisplayMedia({preferCurrentTab}) → WebRTC → Viewer A
    ├── Tab 2 → getDisplayMedia({preferCurrentTab}) → WebRTC → Viewer B
    └── Tab N → ...
```

- **捕获**: 每个 tab 调用 `getDisplayMedia({preferCurrentTab: true})` 捕获自身内容
- **传输**: WebRTC P2P 视频流，Chrome 内部 VP8/H264 编码（零 Python 开销）
- **信令**: WebSocket 中继 offer/answer/ICE（Python 仅转发 JSON）

## 运行

```bash
docker build -t tabshare:v1 .
docker run -v $PWD:/app --network=host --init --rm --ipc=host tabshare:v1
```

访问 http://localhost:8085/viewer/tab_1

## 关键 Chrome 启动参数

- `--headless=new` — 新版 headless，支持 getDisplayMedia
- `--use-fake-ui-for-media-stream` — 自动跳过媒体权限弹窗
- `--disable-features=WebRtcHideLocalIpsWithMdns` — ICE candidate 使用真实 IP（而非不可解析的 .local mDNS 地址）

## 文件结构

```
server/
├── app.py              # FastAPI 入口 + 生命周期管理
├── config.py           # 环境变量配置
├── api/
│   └── ws.py           # WebSocket 端点
├── services/
│   ├── tab_manager.py  # Playwright 浏览器/标签管理
│   └── webrtc_bridge.py # WebRTC 信令桥接
└── static/
    └── viewer.html     # 前端 viewer
```
