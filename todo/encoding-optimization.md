# 视频编码优化 TODO

## 当前状态

| 项目 | 状态 |
|---|---|
| 编码器 | H.264 High Profile, VAAPI 硬件编码 (`iHD_drv_video.so`) |
| SDP Munging | `forceH264High()` — 只保留 H.264, High Profile (64xxxx) 优先 |
| SFU Track | `MimeTypeH264`, keyframe 检测已适配 H.264 NAL |
| Chrome Flags | `AcceleratedVideoEncoder`, `VaapiVideoDecoder`, `Vulkan` 等 |
| maxBitrate | 60 Mbps (LAN) |
| maxFramerate | 60 fps |
| contentHint | `detail` |
| 编码器诊断 | `[encoder-stats]` 定时日志已加入，待采集 |

## 已知问题

### 🔴 P0 — 静态帧质量震荡
- **现象**: 页面静止时，左上角一个文字变化会导致整个页面其他不变区域瞬间变模糊，随即恢复
- **VP9 和 H.264 High Profile 都有此问题**
- **RDP (AMD 5900HX) 无此问题** — RDP 用 dirty-rectangle，只传变化像素
- **根因假设**:
  1. WebRTC BWE 将实际编码码率限制在远低于 60Mbps → 编码器预算不足 → 变化区域抢其他区域的比特
  2. WebRTC 全帧编码架构 — 每帧都重新编码整个 1920×1080，不像 RDP 只发 dirty rect

### 🟡 P1 — 静态页面码率偏高
- 上次 VP9 测试时静态页面 ~4.6 Mbps（理想应 < 500 kbps）
- H.264 High Profile 未确认
- 与 P0 可能同源：编码器每帧重编整图

## 待诊断

- [ ] **采集 `[encoder-stats]` 日志** — 重启 tabshare 后读取 Chrome console
  ```bash
  # 方法1: CDP WebSocket
  docker exec -u ubuntu $(docker ps -q -f name=tabshare) bash -c \
    'curl -s http://localhost:9222/json | python3 -c "import json,sys; tabs=json.load(sys.stdin); print(tabs[0][\"webSocketDebuggerUrl\"])"'
  
  # 方法2: 直接 evaluate
  docker exec -u ubuntu $(docker ps -q -f name=tabshare) bash -c \
    'curl -s http://localhost:9222/json | python3 -m json.tool | grep title'
  ```
- [ ] **确认关键指标**:
  | 字段 | 含义 | 正常 | 异常 |
  |---|---|---|---|
  | `qlr` | 质量受限原因 | `none` | `bandwidth` = BWE 瓶颈 |
  | `targetBitrate` | BWE 分配码率 | ~60000 kbps | < 5000 kbps = 受限 |
  | `encoderImpl` | 编码器实现 | `ExternalEncoder` | `OpenH264` = 软件回退 |
  | `qpSum` | 累计 QP | 平稳增长 | 跳变 = 质量震荡 |

## 修复方案（按优先级）

### 方案 A — 解除 BWE 限制（如诊断确认 qlr=bandwidth）
- 在 SDP 中设置 `b=AS:60000` 强制带宽上限
- 或设置 `encodings[0].priority = 'high'`
- 或在 SFU 侧发送 REMB 告知高带宽

### 方案 B — CQP 模式（恒定 QP）
- Chrome flag: `--force-fieldtrials=WebRTC-Video-QualityScaling/Disabled/`
- 让编码器使用固定 QP 而非 CBR/VBR，质量恒定但码率可变
- 适合 LAN 场景

### 方案 C — 应用层 Dirty Rectangle（长期方案）
- 在浏览器端用 `OffscreenCanvas` 对比前后帧差异
- 只编码/传输变化区域
- 类似 RDP 的方式，但实现复杂

### 方案 D — 硬件升级
- **Intel Arc A580** (¥1200) — VP9/AV1/HEVC 全功能编码器
- **Intel Meteor Lake / Lunar Lake** — iGPU 全功能编码器
- **NVIDIA RTX 4060** — NVENC AV1，需额外配 Chrome

## 实验记录

| 日期 | 配置 | 结果 |
|---|---|---|
| 04-24 | VP9 + 60Mbps | 画面清晰但码率高，静态帧震荡 |
| 04-24 | VP9 + 10Mbps + degradation | 画面模糊 |
| 04-24 | VP9 + 8Mbps + maintain-resolution | 已清晰区域变模糊 |
| 04-24 | VP9 + 20Mbps | 质量震荡 (EncSliceLP 限制) |
| 04-24 | H.264 High + 60Mbps | ✅ 效果好，但仍有轻微震荡 |
| 04-25 | 用户确认 H.264 High 也有震荡，与 VP9 同类问题 |

## 关键文件

- [tab_publisher.py](file:///aiwsp/bs/tabshare/server/services/tab_publisher.py) — SDP munging + 编码参数
- [browser_instance.py](file:///aiwsp/bs/tabshare/server/services/browser_instance.py) — Chrome GPU flags
- [main.go](file:///aiwsp/bs/sfu/main.go) — SFU track + keyframe 检测
- [viewer.html](file:///aiwsp/bs/tabshare/server/static/viewer.html) — 前端 stats 面板
