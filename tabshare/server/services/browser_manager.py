"""
BrowserManager — Global singleton managing all BrowserInstances.
Provides tab-level lookups and viewer broadcast for the WS/SFU layer.
"""
import json
from typing import Optional
from server.services.browser_instance import BrowserInstance, TabInfo


class BrowserManager:
    def __init__(self):
        self.browsers: dict[str, BrowserInstance] = {}
        self._id_counter = 0

    # ── Browser CRUD ──

    async def create_browser(
        self,
        browser_id: Optional[str] = None,
        proxy: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        auto_start: bool = True,
    ) -> BrowserInstance:
        if not browser_id:
            self._id_counter += 1
            browser_id = f"b{self._id_counter}"

        if browser_id in self.browsers:
            raise ValueError(f"Browser {browser_id} already exists")

        browser = BrowserInstance(
            browser_id=browser_id,
            proxy=proxy,
            user_data_dir=user_data_dir,
        )
        self.browsers[browser_id] = browser

        if auto_start:
            await browser.launch()

        return browser

    async def update_browser(self, browser_id: str, updates: dict) -> BrowserInstance:
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")

        if "proxy" in updates:
            browser.proxy = updates["proxy"]
        if "user_data_dir" in updates:
            browser.user_data_dir = updates["user_data_dir"]

        return browser

    async def delete_browser(self, browser_id: str):
        browser = self.browsers.pop(browser_id, None)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.close()

    async def launch_browser(self, browser_id: str):
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.launch()

    async def close_browser(self, browser_id: str):
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.close()

    def get_browser(self, browser_id: str) -> Optional[BrowserInstance]:
        return self.browsers.get(browser_id)

    # ── Tab-level lookups (cross-browser) ──

    def find_tab(self, tab_id: str) -> Optional[TabInfo]:
        """Find a tab across all browsers."""
        for browser in self.browsers.values():
            if tab_id in browser.tabs:
                return browser.tabs[tab_id]
        return None

    def get_tab_page(self, tab_id: str):
        """Get the Playwright Page for a tab."""
        tab = self.find_tab(tab_id)
        return tab.page if tab else None

    # ── Viewer management ──

    def add_viewer(self, tab_id: str, ws):
        tab = self.find_tab(tab_id)
        if tab:
            tab.viewers.append(ws)

    def remove_viewer(self, tab_id: str, ws):
        tab = self.find_tab(tab_id)
        if tab:
            try:
                tab.viewers.remove(ws)
            except ValueError:
                pass

    async def broadcast_to_viewers(self, tab_id: str, event: dict):
        """Push an event to all viewers of a tab."""
        tab = self.find_tab(tab_id)
        if not tab:
            return
        msg = json.dumps(event)
        for ws in tab.viewers[:]:
            try:
                await ws.send_text(msg)
            except Exception:
                pass

    # ── Dashboard ──

    def get_dashboard(self) -> dict:
        total_browsers = len(self.browsers)
        running = sum(1 for b in self.browsers.values() if b.status == "idle")
        total_tabs = sum(len(b.tabs) for b in self.browsers.values())
        total_viewers = sum(
            len(t.viewers)
            for b in self.browsers.values()
            for t in b.tabs.values()
        )
        return {
            "total_browsers": total_browsers,
            "running_browsers": running,
            "total_tabs": total_tabs,
            "total_viewers": total_viewers,
            "browsers": [b.to_dict() for b in self.browsers.values()],
        }

    async def shutdown(self):
        """Shutdown all browsers."""
        for browser_id in list(self.browsers.keys()):
            try:
                await self.browsers[browser_id].close()
            except Exception as e:
                print(f"Error closing browser {browser_id}: {e}")
        self.browsers.clear()


browser_manager = BrowserManager()
