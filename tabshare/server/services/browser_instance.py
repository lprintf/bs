"""
BrowserInstance — 管理单个 Playwright persistent context（独立指纹/代理/cookie）
每个 BrowserInstance 下可有多个 Tab（Page + TabPublisher）

使用 Google Chrome + 指纹注入实现反检测。
"""
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from rebrowser_playwright.async_api import async_playwright, BrowserContext, Page
from server.config import CHROME_PATH, BROWSER_DATA_DIR
from server.services.fingerprint import load_or_create_fingerprint, save_fingerprint, Fingerprint
from server.services.tab_publisher import TabPublisher

# 反检测启动参数
BASE_ARGS = [
    "--headless=new",
    "--use-gl=angle",
    "--use-angle=swiftshader-webgl",  # 软件渲染 WebGL，无需 GPU
    "--disable-blink-features=AutomationControlled",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--use-fake-ui-for-media-stream",
    "--auto-select-desktop-capture-source=Entire screen",
    "--unsafely-treat-insecure-origin-as-secure=http://127.0.0.1:8088",
]

_SUPPRESSED_LOGS = ["Failed to execute 'postMessage' on 'DOMWindow'"]

DEFAULT_VIEWPORT = {"width": 1280, "height": 720}

DATA_DIR = Path("/data")
DOWNLOADS_DIR = DATA_DIR / "downloads"
UPLOADS_DIR = DATA_DIR / "uploads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# stealth.js 模板路径
_STEALTH_JS_PATH = Path(__file__).parent / "stealth.js"


class TabInfo:
    """Holds state for a single tab within a browser."""
    def __init__(self, tab_id: str, browser_id: str, page: Page, publisher: TabPublisher):
        self.tab_id = tab_id
        self.browser_id = browser_id
        self.page = page
        self.publisher = publisher
        self.viewers: list = []  # WebSocket connections
        self.downloads: list[dict] = []  # [{filename, path, size, timestamp}]
        self.pending_filechooser = None  # Playwright FileChooser object
        self.last_input_ws = None  # 最后发送输入的 viewer ws

    def to_dict(self) -> dict:
        try:
            url = self.page.url if not self.page.is_closed() else None
        except Exception:
            url = None

        vp = None
        try:
            vp = self.page.viewport_size
        except Exception:
            pass

        return {
            "tab_id": self.tab_id,
            "browser_id": self.browser_id,
            "url": url,
            "viewport": vp or DEFAULT_VIEWPORT,
            "viewer_count": len(self.viewers),
            "status": "closed" if (self.page.is_closed() if self.page else True) else "active",
        }


class BrowserInstance:
    """Manages one isolated Playwright browser context with anti-detect fingerprinting."""

    def __init__(
        self,
        browser_id: str,
        proxy: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
    ):
        self.browser_id = browser_id
        self.proxy = proxy
        self.user_data_dir = user_data_dir or f"{BROWSER_DATA_DIR}/{browser_id}"
        self.locale_override = locale
        self.timezone_override = timezone
        self.status = "closed"  # closed | launching | idle | error
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.fingerprint: Optional[Fingerprint] = None
        self.tabs: dict[str, TabInfo] = {}
        self._tab_counter = 0

    async def launch(self):
        """Start the browser with anti-detect fingerprinting."""
        if self.status not in ("closed", "error"):
            print(f"[Browser {self.browser_id}] Already running ({self.status})")
            return

        self.status = "launching"
        print(f"[Browser {self.browser_id}] Launching...")

        browser_dir = Path(self.user_data_dir)
        profile_dir = browser_dir / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        # 清理上次残留的 Chrome profile 锁文件
        for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            lock_path = profile_dir / lock_file
            if lock_path.exists() or lock_path.is_symlink():
                lock_path.unlink(missing_ok=True)
                print(f"[Browser {self.browser_id}] Removed stale lock: {lock_file}")

        try:
            # 1. 加载/生成指纹
            self.fingerprint = load_or_create_fingerprint(browser_dir, self.browser_id)

            # 2. GeoIP 自动匹配（如果有代理）
            if self.proxy:
                from server.services.geoip_utils import extract_ip_from_proxy, lookup_geoip
                ip = extract_ip_from_proxy(self.proxy)
                if ip:
                    geo = lookup_geoip(ip)
                    if not self.locale_override:
                        self.fingerprint.locale = geo["locale"]
                    if not self.timezone_override:
                        self.fingerprint.timezone = geo["timezone"]
                    self.fingerprint.latitude = geo["lat"]
                    self.fingerprint.longitude = geo["lon"]
                    # 保存更新后的 geoip 信息
                    save_fingerprint(browser_dir, self.fingerprint)

            # 手动覆盖
            if self.locale_override:
                self.fingerprint.locale = self.locale_override
            if self.timezone_override:
                self.fingerprint.timezone = self.timezone_override

            # 3. 构建启动参数
            launch_args = BASE_ARGS.copy()
            launch_args.append(f"--lang={self.fingerprint.locale}")

            # 4. launch_persistent_context
            self.playwright = await async_playwright().start()

            launch_kwargs = {
                "user_data_dir": str(profile_dir),
                "headless": False,  # 由 args 中的 --headless=new 控制
                "args": launch_args,
                "ignore_default_args": ["--headless", "--headless=old", "--enable-automation"],
                "user_agent": self.fingerprint.user_agent,
                "locale": self.fingerprint.locale,
                "timezone_id": self.fingerprint.timezone,
                "geolocation": {
                    "latitude": self.fingerprint.latitude,
                    "longitude": self.fingerprint.longitude,
                },
                "permissions": ["geolocation"],
                "viewport": DEFAULT_VIEWPORT,
                "accept_downloads": True,
                "extra_http_headers": {
                    "Accept-Language": f"{self.fingerprint.locale},{self.fingerprint.locale.split('-')[0]};q=0.9",
                },
            }
            if CHROME_PATH:
                launch_kwargs["executable_path"] = CHROME_PATH

            # 代理配置 - 使用本地 relay 预注入认证，避免 Playwright 407 重试问题
            if self.proxy:
                from server.services.proxy_relay import get_or_create_relay
                local_proxy_url = await get_or_create_relay(self.proxy)
                launch_kwargs["proxy"] = {
                    "server": local_proxy_url,
                    "bypass": "127.0.0.1,localhost",
                }

            self.context = await self.playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )

            # 5. 注入 stealth 脚本
            stealth_js = self._build_stealth_script()
            await self.context.add_init_script(stealth_js)

            # 关闭 persistent context 自带的空白页
            for page in self.context.pages:
                if page.url in ("about:blank", "chrome://newtab/"):
                    await page.close()

            self.status = "idle"
            print(
                f"[Browser {self.browser_id}] Ready "
                f"(proxy={self.proxy}, fp={self.fingerprint.fingerprint_id}, "
                f"locale={self.fingerprint.locale}, tz={self.fingerprint.timezone})"
            )

        except Exception as e:
            self.status = "error"
            print(f"[Browser {self.browser_id}] Launch failed: {e}")
            raise

    def _build_sec_ch_ua(self) -> str:
        """构建 Sec-Ch-Ua header，与 stealth.js brands 一致"""
        import re
        m = re.search(r'Chrome/(\d+)', self.fingerprint.user_agent)
        major = m.group(1) if m else '147'
        return f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not.A/Brand";v="8"'

    def _build_stealth_script(self) -> str:
        """加载 stealth.js 模板并注入指纹数据"""
        template = _STEALTH_JS_PATH.read_text(encoding="utf-8")
        fp_json = json.dumps(asdict(self.fingerprint), ensure_ascii=False)
        return template.replace("{{FINGERPRINT_JSON}}", fp_json)

    @staticmethod
    def _parse_proxy(proxy_url: str) -> dict | None:
        """解析代理 URL（与 g2api GeminiLauncher 一致）"""
        if not proxy_url:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            server = f"{parsed.scheme}://{parsed.hostname}"
            if parsed.port:
                server += f":{parsed.port}"
            proxy_config = {"server": server}
            if parsed.username:
                proxy_config["username"] = parsed.username
            if parsed.password:
                proxy_config["password"] = parsed.password
            return proxy_config
        except Exception as e:
            print(f"[Proxy] URL 解析失败: {e}")
            return None

    async def create_tab(self, url: str = "about:blank", viewport: dict | None = None) -> TabInfo:
        """Create a new tab (Page) within this browser context."""
        if not self.context or self.status != "idle":
            raise RuntimeError(f"Browser {self.browser_id} not ready ({self.status})")

        self._tab_counter += 1
        tab_id = f"{self.browser_id}_tab_{self._tab_counter}"

        page = await self.context.new_page()

        if viewport:
            await page.set_viewport_size(viewport)

        # Console log forwarding
        page.on("console", lambda msg: (
            None if any(s in msg.text for s in _SUPPRESSED_LOGS)
            else print(f"[{tab_id}] [{msg.type}]: {msg.text}")
        ))

        # Navigate
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[{tab_id}] Navigation warning: {e}")

        # Start publisher
        publisher = TabPublisher(page, tab_id)
        tab_info = TabInfo(tab_id, self.browser_id, page, publisher)
        self.tabs[tab_id] = tab_info
        asyncio.create_task(publisher.start())

        # Download interception
        tab_dir = DOWNLOADS_DIR / tab_id
        tab_dir.mkdir(parents=True, exist_ok=True)

        def on_download(download):
            asyncio.ensure_future(self._handle_download(tab_info, download, tab_dir))
        page.on("download", on_download)

        # File chooser interception
        def on_filechooser(fc):
            asyncio.ensure_future(self._handle_filechooser(tab_info, fc))
        page.on("filechooser", on_filechooser)

        # Popup interception (target="_blank" links)
        page.on("popup", lambda popup: asyncio.ensure_future(
            self._handle_popup(tab_info, popup)
        ))

        print(f"[Browser {self.browser_id}] Created tab: {tab_id} → {url}")
        return tab_info

    async def _handle_download(self, tab: TabInfo, download, tab_dir: Path):
        """Handle a browser download event."""
        try:
            filename = download.suggested_filename
            save_path = tab_dir / filename
            await download.save_as(str(save_path))
            size = save_path.stat().st_size
            import time
            entry = {
                "filename": filename,
                "path": str(save_path),
                "size": size,
                "timestamp": time.time(),
                "download_url": f"/api/files/{tab.tab_id}/{filename}",
            }
            tab.downloads.append(entry)
            print(f"[{tab.tab_id}] Download saved: {filename} ({size} bytes)")
            # Notify viewers
            from server.services.browser_manager import browser_manager
            await browser_manager.broadcast_to_viewers(tab.tab_id, {
                "type": "download_ready",
                **entry,
            })
        except Exception as e:
            print(f"[{tab.tab_id}] Download error: {e}")

    async def _handle_filechooser(self, tab: TabInfo, filechooser):
        """Handle file chooser dialog from the remote page."""
        tab.pending_filechooser = filechooser
        is_multi = filechooser.is_multiple()
        print(f"[{tab.tab_id}] File chooser opened (multiple={is_multi})")
        from server.services.browser_manager import browser_manager
        await browser_manager.broadcast_to_viewers(tab.tab_id, {
            "type": "filechooser_opened",
            "multiple": is_multi,
        })

    async def _handle_popup(self, source_tab: TabInfo, popup_page: Page):
        """Handle target=_blank popup: register as new tab, notify triggering viewer."""
        ws = source_tab.last_input_ws
        try:
            # Immediately notify viewer to show loading overlay
            if ws:
                try:
                    await ws.send_text(json.dumps({"type": "popup_loading"}))
                except Exception:
                    pass

            # Wait for popup to load
            try:
                await popup_page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

            self._tab_counter += 1
            tab_id = f"{self.browser_id}_tab_{self._tab_counter}"
            print(f"[Browser {self.browser_id}] Popup → new tab: {tab_id} ({popup_page.url[:80]})")

            # Console forwarding
            popup_page.on("console", lambda msg: (
                None if any(s in msg.text for s in _SUPPRESSED_LOGS)
                else print(f"[{tab_id}] [{msg.type}]: {msg.text}")
            ))

            # Start publisher
            publisher = TabPublisher(popup_page, tab_id)
            tab_info = TabInfo(tab_id, self.browser_id, popup_page, publisher)
            self.tabs[tab_id] = tab_info
            asyncio.create_task(publisher.start())

            # Download interception
            tab_dir = DOWNLOADS_DIR / tab_id
            tab_dir.mkdir(parents=True, exist_ok=True)
            popup_page.on("download", lambda dl: asyncio.ensure_future(
                self._handle_download(tab_info, dl, tab_dir)
            ))

            # File chooser
            popup_page.on("filechooser", lambda fc: asyncio.ensure_future(
                self._handle_filechooser(tab_info, fc)
            ))

            # Recursive popup handling
            popup_page.on("popup", lambda p: asyncio.ensure_future(
                self._handle_popup(tab_info, p)
            ))

            # Notify viewer: tab ready, switch to it
            if ws:
                try:
                    await ws.send_text(json.dumps({
                        "type": "popup_tab_created",
                        "tab_id": tab_id,
                        "url": popup_page.url,
                    }))
                except Exception:
                    pass

        except Exception as e:
            print(f"[Browser {self.browser_id}] Popup handling error: {e}")
            # Hide overlay on error
            if ws:
                try:
                    await ws.send_text(json.dumps({"type": "popup_tab_created", "tab_id": "", "error": str(e)}))
                except Exception:
                    pass

    async def close_tab(self, tab_id: str):
        """Close a specific tab."""
        tab = self.tabs.pop(tab_id, None)
        if not tab:
            return
        tab.publisher.close()
        if not tab.page.is_closed():
            await tab.page.close()
        print(f"[Browser {self.browser_id}] Closed tab: {tab_id}")

    async def navigate_tab(self, tab_id: str, url: str):
        """Navigate a tab to a new URL."""
        tab = self.tabs.get(tab_id)
        if not tab:
            raise KeyError(f"Tab {tab_id} not found")
        await tab.page.goto(url, timeout=60000, wait_until="domcontentloaded")
        return {"url": tab.page.url}

    async def set_viewport(self, tab_id: str, width: int, height: int):
        """Set viewport size for a tab."""
        tab = self.tabs.get(tab_id)
        if not tab:
            raise KeyError(f"Tab {tab_id} not found")
        await tab.page.set_viewport_size({"width": width, "height": height})

    async def go_back(self, tab_id: str):
        """Navigate back in history."""
        tab = self.tabs.get(tab_id)
        if not tab:
            raise KeyError(f"Tab {tab_id} not found")
        await tab.page.go_back(timeout=10000)
        return {"url": tab.page.url}

    async def go_forward(self, tab_id: str):
        """Navigate forward in history."""
        tab = self.tabs.get(tab_id)
        if not tab:
            raise KeyError(f"Tab {tab_id} not found")
        await tab.page.go_forward(timeout=10000)
        return {"url": tab.page.url}

    async def close(self):
        """Close the entire browser and all tabs."""
        self.status = "closing"
        # Close all tabs
        for tab_id in list(self.tabs.keys()):
            await self.close_tab(tab_id)

        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        self.status = "closed"
        print(f"[Browser {self.browser_id}] Closed")

    def to_dict(self) -> dict:
        fp_info = None
        if self.fingerprint:
            fp_info = {
                "id": self.fingerprint.fingerprint_id,
                "os": self.fingerprint.os,
                "locale": self.fingerprint.locale,
                "timezone": self.fingerprint.timezone,
                "ua": self.fingerprint.user_agent[:60] + "...",
            }
        return {
            "browser_id": self.browser_id,
            "proxy": self.proxy,
            "user_data_dir": self.user_data_dir,
            "status": self.status,
            "fingerprint": fp_info,
            "tab_count": len(self.tabs),
            "total_viewers": sum(len(t.viewers) for t in self.tabs.values()),
            "tabs": [t.to_dict() for t in self.tabs.values()],
        }
