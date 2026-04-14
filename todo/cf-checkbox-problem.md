# Cloudflare Turnstile 绕过 — 需求与实验总结

## 核心需求

在容器化 Playwright 环境（TabShare）中，当 Cloudflare 退化到 **Turnstile checkbox** 时，点击后能通过验证。

> [!IMPORTANT]
> **不是**"新 profile 能不能秒过"——这从来不是问题。新 profile + 干净 IP 一直都能绕过全页 challenge。
> 真正的问题是：**checkbox 弹出后点击验证失败**，以及**长期使用的 profile 被逐渐标记为 bot(不重要, 只要能过checkbox,这个问题就不会存在)**。

### 场景分解

| 场景                                           | 当前状态   | 目标                             |
| ---------------------------------------------- | ---------- | -------------------------------- |
| 新 profile + 干净 IP → 自动通过（无 checkbox） | ✅ 已可用   | 保持                             |
| CF 退化到 checkbox → 点击后通过                | ❌ 失败     | **必须解决**                     |
| 长期 profile 不被 `cf_chl_rc_ni` 递增标记      | ❌ 会被标记 | 解决cf checkbox前直接删除profile |
| 用户手动通过 CF 验证（通过 viewer 直接操作）   | ✅ 已可用   | 保持但非依赖                     |

---

## 已确认的检测向量

| # | 向量 | 状态 | 说明 |
|---|---|---|---|
| 1 | **`Runtime.enable` CDP 命令** | ❌ 未解决 | 主要向量。Playwright 自动调用，Turnstile 检测 `Runtime.consoleAPICalled` 副作用 |
| 2 | `navigator.webdriver` | ✅ 已解决 | stealth.js 重定义为 `false`（非 `undefined`） |
| 3 | `userAgentData.brands` | ✅ 已解决 | stealth.js 注入 `"Google Chrome"` 品牌 |
| 4 | 屏幕分辨率不一致 | ✅ 已解决 | fingerprint.py 锁定 1920x1080，与 Xvfb 一致 |
| 5 | Playwright 启动参数 | ✅ 已解决 | `ignore_default_args` 清除高风险参数 |
| 6 | Profile 污染 `cf_chl_rc_ni` | ⚠️ 临时方案 | challenge 失败时删除 profile 重建 |

### Runtime.enable 详情

手动 patch Playwright 1.58.0 driver 的实验结论：
- ✅ runtimeEnableLeak 检测可以通过
- ❌ 引入了 `Page.delegate._sessions` 兼容性问题（1.58.0 用 `delegate` 而非 `_delegate`）
- ❌ patch 脆弱，修一个 bug 引入新 bug，不可维护

可行方案：rebrowser-playwright 或 Patchright（见下方对比）

---

## 检测测试站点

| 站点                          | 用途                                             | URL                                            |
| ----------------------------- | ------------------------------------------------ | ---------------------------------------------- |
| **rebrowser-bot-detector**    | 检测 Runtime.enable leak、webdriver、viewport 等 | https://bot-detector.rebrowser.net/            |
| **SannySoft**                 | 经典 bot 检测（WebDriver、Chrome、plugins）      | https://bot.sannysoft.com/                     |
| **Cloudflare Dashboard**      | 真实 Turnstile 验证（登录页）                    | https://dash.cloudflare.com/login              |
| **Cloudflare Turnstile Demo** | 官方 Turnstile 测试页                            | https://www.cloudflare.com/products/turnstile/ |
| **CreepJS**                   | 深度指纹检测                                     | https://abrahamjuliot.github.io/creepjs/       |
| **BrowserLeaks**              | WebGL、Canvas、Audio 指纹                        | https://browserleaks.com/                      |

---

## 方案对比

### A. rebrowser-patches（手动 patch Playwright driver）

```
适用：Playwright Node.js / Python driver
原理：修改 crPage.js、crConnection.js、frames.js 等源码
优点：不换库，侵入最小
缺点：
  - 与 Playwright 版本强耦合，升级就坏
  - 需要精确匹配属性名（如 delegate vs _delegate）
  - 我们实测 patch 质量差，调了半天还是有 bug
```

### B. rebrowser-playwright（drop-in 替换）

```
适用：Node.js 项目
原理：预 patch 好的 Playwright 包，npm 直接装
优点：最省事，社区维护
缺点：
  - Python 版 PyPI 包未知稳定性
  - 版本可能滞后
```

**PyPI**: https://pypi.org/project/rebrowser-playwright/  
**GitHub**: https://github.com/rebrowser/rebrowser-playwright-python

### C. Patchright

```
适用：Python / Node.js
原理：fork Playwright，禁用 Runtime.enable + Console.enable
优点：API 兼容 Playwright，pip install patchright
缺点：
  - 用户反馈有坑（作者是 Kaliiiiiiiiii-Vinyzu）
  - 不支持所有 Playwright 功能（如 console.log 断了）
  - 内置 Chromium 可能不如 Chrome for Testing
```

### D. 不 Patch，接受 checkbox + 人工兜底

```
原理：不改 driver，遇到 checkbox 提示用户通过 viewer 手动点击
优点：零维护，零 bug
缺点：依赖人工，自动化程度低
```

---

## 关键文件

| 文件 | 职责 |
|---|---|
| `server/services/stealth.js` | JS 指纹伪装（webdriver、brands、screen） |
| `server/services/fingerprint.py` | 指纹生成与管理 |
| `server/services/browser_instance.py` | 浏览器启动参数管理 |
| `server/api/ws.py` | WebSocket 鼠标/键盘输入 |

---

## 下一步

1. **替换 Playwright 为 rebrowser-playwright** — `pip install rebrowser-playwright`，API 兼容，drop-in 替换
2. **备选：Patchright** — `pip install patchright`，如 rebrowser 不行再试
3. **验证标准**：在始终弹 Turnstile checkbox 的站点，点击后能通过验证
