# Scripts

`scripts/` 放诊断和依赖下载脚本，不参与镜像构建。

## 脚本

| 文件 | 职责 | 运行位置 |
| --- | --- | --- |
| `download_sdk.sh` | 下载 LiveKit JS SDK 到 `tabshare/server/static/vendor` | 本机 |
| `check_encoder.py` | 通过 CDP/Playwright 查看 WebRTC encoder stats 和 GPU 引擎信息 | 已运行 TabShare 的环境 |

## 相关根脚本

| 文件 | 职责 |
| --- | --- |
| `start.sh` | 按 `cpu`、`intel`、`amd` 模式构建并启动 compose |
| `sync.sh` | 通过 `rsync`/`sshpass` 同步仓库到远端机器 |
| `webgl_test.sh` | 通过后端 debug API 检查目标 tab 的 WebGL 渲染器 |

`sync.sh` 和 `webgl_test.sh` 是运维辅助脚本，不应作为应用启动路径依赖。
