"""
BrowserInstance — 管理单个 Playwright persistent context（独立指纹/代理/cookie）
每个 BrowserInstance 下可有多个 Tab（Page + TabPublisher）
"""
import asyncio
import json
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, BrowserContext, Page
from server.config import CHROME_PATH
from server.services.tab_publisher import TabPublisher

# Chromium launch args shared across all instances
BASE_ARGS = [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--use-fake-ui-for-media-stream",
    "--auto-select-desktop-capture-source=Entire screen",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--unsafely-treat-insecure-origin-as-secure=http://127.0.0.1:8088",
]

_SUPPRESSED_LOGS = ["Failed to execute 'postMessage' on 'DOMWindow'"]

DEFAULT_VIEWPORT = {"width": 1280, "height": 720}

DATA_DIR = Path("/data")
DOWNLOADS_DIR = DATA_DIR / "downloads"
UPLOADS_DIR = DATA_DIR / "uploads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


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
    """Manages one isolated Playwright browser context."""

    def __init__(
        self,
        browser_id: str,
        proxy: Optional[str] = None,
        user_data_dir: Optional[str] = None,
    ):
        self.browser_id = browser_id
        self.proxy = proxy
        self.user_data_dir = user_data_dir or f"data/browsers/{browser_id}"
        self.status = "closed"  # closed | launching | idle | error
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.tabs: dict[str, TabInfo] = {}
        self._tab_counter = 0

    async def launch(self):
        """Start the browser."""
        if self.status not in ("closed", "error"):
            print(f"[Browser {self.browser_id}] Already running ({self.status})")
            return

        self.status = "launching"
        print(f"[Browser {self.browser_id}] Launching...")

        try:
            self.playwright = await async_playwright().start()

            launch_args = BASE_ARGS.copy()
            if self.proxy:
                launch_args.append(f"--proxy-server={self.proxy}")

            launch_kwargs = {
                "headless": True,
                "args": launch_args,
            }
            if CHROME_PATH:
                launch_kwargs["executable_path"] = CHROME_PATH

            browser = await self.playwright.chromium.launch(**launch_kwargs)
            self.context = await browser.new_context(
                viewport=DEFAULT_VIEWPORT,
                accept_downloads=True,
            )
            self._browser = browser

            self.status = "idle"
            print(f"[Browser {self.browser_id}] Ready (proxy={self.proxy})")

        except Exception as e:
            self.status = "error"
            print(f"[Browser {self.browser_id}] Launch failed: {e}")
            raise

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
            await page.goto(url, timeout=15000)
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
        await tab.page.goto(url, timeout=15000)
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
        if hasattr(self, '_browser') and self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        self.status = "closed"
        print(f"[Browser {self.browser_id}] Closed")

    def to_dict(self) -> dict:
        return {
            "browser_id": self.browser_id,
            "proxy": self.proxy,
            "user_data_dir": self.user_data_dir,
            "status": self.status,
            "tab_count": len(self.tabs),
            "total_viewers": sum(len(t.viewers) for t in self.tabs.values()),
            "tabs": [t.to_dict() for t in self.tabs.values()],
        }
