"""
BrowserManager — Global singleton managing all BrowserInstances.
Provides tab-level lookups and viewer broadcast for the WS/SFU layer.
"""
import json
from typing import Optional
from sqlalchemy import delete, func, select
from server.services.browser_instance import BrowserInstance, TabInfo, _url_hostname
from server.services.database import (
    async_session,
    BrowserModel,
    TabModel,
    WorkbenchModel,
    WorkbenchTabModel,
)
from server.config import BROWSER_DATA_DIR


class BrowserManager:
    def __init__(self):
        self.browsers: dict[int, BrowserInstance] = {}

    # ── Persistence ──

    async def load_from_db(self):
        """Load browser configs from DB and restore browsers that were left open."""
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
                restore_on_start=row.restore_on_start,
            )
            self.browsers[row.id] = browser
            if row.restore_on_start:
                try:
                    print(f"[browser_manager] restoring browser {row.id} '{row.name}'")
                    await browser.launch()
                    await self.restore_browser_tabs(browser)
                except Exception as e:
                    print(f"[browser_manager] restore browser {row.id} failed: {e}")
            else:
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
            restore_on_start=False,
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
            restore_on_start=False,
        )
        self.browsers[db_row.id] = browser

        if auto_start:
            await browser.launch()
            await self.restore_browser_tabs(browser)
            await self.set_browser_restore_state(db_row.id, True)

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
            await session.execute(
                delete(WorkbenchTabModel).where(WorkbenchTabModel.browser_id == browser_id)
            )
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
        await self.restore_browser_tabs(browser)
        await self.set_browser_restore_state(browser_id, True)

    async def close_browser(self, browser_id: int):
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")
        await browser.close()
        await self.set_browser_restore_state(browser_id, False)

    async def set_browser_restore_state(self, browser_id: int, restore_on_start: bool):
        browser = self.get_browser(browser_id)
        if browser:
            browser.restore_on_start = restore_on_start
        async with async_session() as session:
            result = await session.execute(
                select(BrowserModel).where(BrowserModel.id == browser_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.restore_on_start = restore_on_start
                await session.commit()

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

    # ── Persistent tabs ──

    async def restore_browser_tabs(self, browser: BrowserInstance):
        """Recreate persisted tab Pages for a launched browser."""
        if browser.status != "idle":
            return

        async with async_session() as session:
            result = await session.execute(
                select(TabModel)
                .where(TabModel.browser_id == browser.browser_id, TabModel.enabled == True)
                .order_by(TabModel.tab_id)
            )
            tab_rows = result.scalars().all()

            wb_result = await session.execute(
                select(WorkbenchTabModel, WorkbenchModel)
                .join(WorkbenchModel, WorkbenchModel.id == WorkbenchTabModel.workbench_id)
                .where(WorkbenchTabModel.browser_id == browser.browser_id)
            )
            workbench_by_tab = {
                wb_tab.tab_id: (wb_tab, wb)
                for wb_tab, wb in wb_result.all()
            }

        for tab_row in tab_rows:
            if tab_row.tab_id in browser.tabs:
                continue
            wb_pair = workbench_by_tab.get(tab_row.tab_id)
            restore_url = tab_row.url
            if wb_pair:
                wb_tab, _wb = wb_pair
                if _url_hostname(restore_url) != wb_tab.allowed_hostname:
                    restore_url = f"https://{wb_tab.allowed_hostname}/"
            viewport = {
                "width": tab_row.viewport_width,
                "height": tab_row.viewport_height,
            }
            try:
                tab = await browser.create_tab(
                    url=restore_url,
                    viewport=viewport,
                    tab_id=tab_row.tab_id,
                    persistent=True,
                    created_by=tab_row.created_by,
                )
                await self.update_persistent_tab_url(browser.browser_id, tab.tab_id, tab.page.url)
                if wb_pair:
                    wb_tab, wb = wb_pair
                    await browser.assign_tab_to_workbench(
                        tab.tab_id,
                        wb.id,
                        wb.name,
                        wb_tab.allowed_hostname,
                    )
            except Exception as e:
                print(f"[browser_manager] restore tab {browser.browser_id}/{tab_row.tab_id} failed: {e}")

    async def create_persistent_tab(
        self,
        browser_id: int,
        url: str,
        viewport: dict,
        created_by: str = "admin",
    ) -> TabInfo:
        browser = self.get_browser(browser_id)
        if not browser:
            raise KeyError(f"Browser {browser_id} not found")

        async with async_session() as session:
            result = await session.execute(
                select(func.max(TabModel.tab_id)).where(TabModel.browser_id == browser_id)
            )
            max_db_id = result.scalar()
            next_id = max((max_db_id if max_db_id is not None else -1) + 1, browser._tab_counter)
            row = TabModel(
                browser_id=browser_id,
                tab_id=next_id,
                url=url,
                viewport_width=viewport.get("width", 1280),
                viewport_height=viewport.get("height", 720),
                created_by=created_by,
                enabled=True,
            )
            session.add(row)
            await session.commit()

        try:
            tab = await browser.create_tab(
                url=url,
                viewport=viewport,
                tab_id=next_id,
                persistent=True,
                created_by=created_by,
            )
            await self.update_persistent_tab_url(browser_id, tab.tab_id, tab.page.url)
        except Exception:
            async with async_session() as session:
                result = await session.execute(
                    select(TabModel).where(
                        TabModel.browser_id == browser_id,
                        TabModel.tab_id == next_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row:
                    await session.delete(row)
                    await session.commit()
            raise

        return tab

    async def delete_persistent_tab(self, browser_id: int, tab_id: int):
        async with async_session() as session:
            result = await session.execute(
                select(WorkbenchTabModel).where(
                    WorkbenchTabModel.browser_id == browser_id,
                    WorkbenchTabModel.tab_id == tab_id,
                )
            )
            if result.scalar_one_or_none():
                raise ValueError("Tab belongs to a workbench")

            result = await session.execute(
                select(TabModel).where(TabModel.browser_id == browser_id, TabModel.tab_id == tab_id)
            )
            tab_row = result.scalar_one_or_none()
            if tab_row:
                await session.delete(tab_row)
                await session.commit()

    async def update_persistent_tab_url(self, browser_id: int, tab_id: int, url: str):
        async with async_session() as session:
            wb_result = await session.execute(
                select(WorkbenchTabModel).where(
                    WorkbenchTabModel.browser_id == browser_id,
                    WorkbenchTabModel.tab_id == tab_id,
                )
            )
            wb_tab = wb_result.scalar_one_or_none()
            if wb_tab and _url_hostname(url) != wb_tab.allowed_hostname:
                return

            result = await session.execute(
                select(TabModel).where(TabModel.browser_id == browser_id, TabModel.tab_id == tab_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.url = url
                await session.commit()

    async def update_persistent_tab_viewport(self, browser_id: int, tab_id: int, width: int, height: int):
        async with async_session() as session:
            result = await session.execute(
                select(TabModel).where(TabModel.browser_id == browser_id, TabModel.tab_id == tab_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.viewport_width = width
                row.viewport_height = height
                await session.commit()

    async def prepare_tab_stream(self, browser_id: int, tab_id: int, timeout_ms: int = 8000) -> bool:
        tab = self.find_tab(browser_id, tab_id)
        if not tab or tab.page.is_closed():
            raise KeyError(f"Tab {browser_id}/{tab_id} not found")
        if not tab.publisher:
            return False
        return await tab.publisher.ensure_active(timeout_ms=timeout_ms)

    # ── Viewer management ──

    def add_viewer(self, browser_id: int, tab_id: int, ws):
        tab = self.find_tab(browser_id, tab_id)
        if tab:
            was_empty = len(tab.viewers) == 0
            tab.viewers.append(ws)
            # 第一个 viewer 连接时恢复推流
            if was_empty and tab.publisher:
                import asyncio
                asyncio.ensure_future(tab.publisher.resume())

    def remove_viewer(self, browser_id: int, tab_id: int, ws):
        tab = self.find_tab(browser_id, tab_id)
        if tab:
            try:
                tab.viewers.remove(ws)
            except ValueError:
                pass
            # 最后一个 viewer 断开时暂停推流，释放编码器
            if len(tab.viewers) == 0 and tab.publisher:
                import asyncio
                asyncio.ensure_future(tab.publisher.pause())

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
