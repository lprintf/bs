import asyncio
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from server.config import CHROME_PATH

class TabManager:
    def __init__(self):
        self.playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.tabs: dict[str, Page] = {}
        self.tab_counter = 0

    async def start(self):
        print("Starting TabManager...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 720}
        )
        print("TabManager started.")

    async def stop(self):
        print("Stopping TabManager...")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def new_tab(self) -> tuple[str, Page]:
        if not self.context:
            raise RuntimeError("TabManager not started")
        
        page = await self.context.new_page()
        self.tab_counter += 1
        tab_id = f"tab_{self.tab_counter}"
        self.tabs[tab_id] = page
        print(f"Created new tab: {tab_id}")
        return tab_id, page

    def get_tab(self, tab_id: str) -> Page | None:
        return self.tabs.get(tab_id)

    async def close_tab(self, tab_id: str):
        if tab_id in self.tabs:
            page = self.tabs.pop(tab_id)
            if not page.is_closed():
                await page.close()
            print(f"Closed tab: {tab_id}")

tab_manager = TabManager()
