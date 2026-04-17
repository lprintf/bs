"""
BrowserManager — Global singleton managing all BrowserInstances.
Provides tab-level lookups and viewer broadcast for the WS/SFU layer.
"""
import json
from typing import Optional
from sqlalchemy import select
from server.services.browser_instance import BrowserInstance, TabInfo
from server.services.database import async_session, BrowserModel
from server.config import BROWSER_DATA_DIR


class BrowserManager:
    def __init__(self):
        self.browsers: dict[int, BrowserInstance] = {}

    # ── Persistence ──

    async def load_from_db(self):
        """Load browser configs from DB (all start as closed, no tabs)."""
        async with async_session() as session:
            result = await session.execute(select(BrowserModel).order_by(BrowserModel.id))
            rows = result.scalars().all()

        for row in rows:
            if row.id in self.browsers:
                continue
            browser = BrowserInstance(
                browser_id=row.id,
                name=row.name or "",
                proxy=row.proxy,
                user_data_dir=row.user_data_dir or f"{BROWSER_DATA_DIR}/{row.id}",
                chrome_version=row.chrome_version,
            )
            self.browsers[row.id] = browser
            print(f"[browser_manager] loaded browser {row.id} '{row.name}' (closed)")

    # ── Browser CRUD ──

    async def create_browser(
        self,
        name: str = "",
        proxy: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
        chrome_version: Optional[str] = None,
        auto_start: bool = True,
    ) -> BrowserInstance:
        # Insert into DB to get auto-increment ID
        db_row = BrowserModel(
            name=name,
            proxy=proxy,
            chrome_version=chrome_version,
        )
        async with async_session() as session:
            session.add(db_row)
            await session.commit()
            await session.refresh(db_row)

        # Set user_data_dir after we have the ID
        data_dir = user_data_dir or f"{BROWSER_DATA_DIR}/{db_row.id}"
        db_row.user_data_dir = data_dir
        async with async_session() as session:
            await session.merge(db_row)
            await session.commit()

        browser = BrowserInstance(
            browser_id=db_row.id,
            name=name,
            proxy=proxy,
            user_data_dir=data_dir,
            locale=locale,
            timezone=timezone,
            chrome_version=chrome_version,
        )
        self.browsers[db_row.id] = browser

        if auto_start:
            await browser.launch()

        return browser

    async def update_browser(self, browser_id: int, updates: dict) -> BrowserInstance:
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")

        if "name" in updates:
            browser.name = updates["name"]
        if "proxy" in updates:
            browser.proxy = updates["proxy"]
        if "chrome_version" in updates:
            old_ver = browser.chrome_version
            browser.chrome_version = updates["chrome_version"] or None
            if browser.chrome_version != old_ver:
                from server.services.fingerprint import load_or_create_fingerprint
                from pathlib import Path
                browser_dir = Path(browser.user_data_dir)
                browser.fingerprint = load_or_create_fingerprint(
                    browser_dir, str(browser_id),
                    chrome_version=browser.chrome_version,
                    force_regen=True,
                )

        # Persist to DB
        async with async_session() as session:
            result = await session.execute(
                select(BrowserModel).where(BrowserModel.id == browser_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.name = browser.name
                row.proxy = browser.proxy
                row.chrome_version = browser.chrome_version
                await session.commit()

        return browser

    async def delete_browser(self, browser_id: int):
        browser = self.browsers.pop(browser_id, None)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.close()

        async with async_session() as session:
            result = await session.execute(
                select(BrowserModel).where(BrowserModel.id == browser_id)
            )
            row = result.scalar_one_or_none()
            if row:
                await session.delete(row)
                await session.commit()

    async def launch_browser(self, browser_id: int):
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.launch()

    async def close_browser(self, browser_id: int):
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.close()

    def get_browser(self, browser_id: int) -> Optional[BrowserInstance]:
        return self.browsers.get(browser_id)

    # ── Tab-level lookups (cross-browser) ──

    def find_tab(self, browser_id: int, tab_id: int) -> Optional[TabInfo]:
        """Find a tab by browser_id and tab_id."""
        browser = self.browsers.get(browser_id)
        if browser:
            return browser.tabs.get(tab_id)
        return None

    def get_tab_page(self, browser_id: int, tab_id: int):
        """Get the Playwright Page for a tab."""
        tab = self.find_tab(browser_id, tab_id)
        return tab.page if tab else None

    # ── Viewer management ──

    def add_viewer(self, browser_id: int, tab_id: int, ws):
        tab = self.find_tab(browser_id, tab_id)
        if tab:
            tab.viewers.append(ws)

    def remove_viewer(self, browser_id: int, tab_id: int, ws):
        tab = self.find_tab(browser_id, tab_id)
        if tab:
            try:
                tab.viewers.remove(ws)
            except ValueError:
                pass

    async def broadcast_to_viewers(self, browser_id: int, tab_id: int, event: dict):
        """Push an event to all viewers of a tab."""
        tab = self.find_tab(browser_id, tab_id)
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
