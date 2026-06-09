# SFU

`sfu/` 是 TabShare 的 Go WebRTC SFU。它接收浏览器标签页发布的 RTP，向 viewer 转发媒体流，并负责按房间录制 WebM 文件。

## 职责

| 能力 | 说明 |
| --- | --- |
| 发布端接入 | 接收 Chrome publisher 的 SDP offer 和 RTP |
| viewer 接入 | 为外部 viewer 创建订阅端 PeerConnection |
| 快速切换 | 支持 viewer 在不同 room 间切换 RTP 来源 |
| 录制 | 使用 VP8 sample 写入 WebM，文件保存到 `RECORDING_DIR` |
| 编码协商 | 注册 H.264、VP8、VP9、AV1；实际编码由 publisher SDP 控制 |

## 容器内编译

Go 二进制在 Docker image build 阶段编译：

```dockerfile
FROM golang:1.24-alpine AS builder
RUN go mod tidy && CGO_ENABLED=0 go build -o /sfu .
```

运行镜像只包含 Alpine 和 `/usr/local/bin/sfu`，不会在容器启动时再次编译。

## 构建

通过根 compose 构建：

```sh
docker compose build sfu
```

单独构建：

```sh
docker build -t bs-sfu ./sfu
```

本机 Go 环境可用时也可以本地检查：

```sh
go test ./...
go build -o /tmp/sfu .
```

## 运行配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `NAT_IP` | 空 | 对外声明的 ICE IP，compose 中使用 `${HOST_IP}` |
| `UDP_PORT` | `59055` | WebRTC UDP 端口 |
| `HTTP_PORT` | `8088` | SFU HTTP API 端口 |
| `RECORDING_DIR` | `./recordings` | WebM 录制输出目录 |

Compose 暴露的端口：

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| `59055` | UDP/TCP | ICE/RTP |
| `8085` | TCP | 因 `tabshare` 与 `sfu` 共享 network namespace，对外统一暴露 FastAPI |

`sfu` 自身监听 `HTTP_PORT=8088`，由 `tabshare` 通过 `SFU_INTERNAL_URL=http://127.0.0.1:8088` 访问。

## 录制文件

根 compose 将录制目录挂载到：

```text
./tabshare/data/recordings:/data/recordings
```

SFU 容器内使用：

```text
RECORDING_DIR=/data/recordings
```
