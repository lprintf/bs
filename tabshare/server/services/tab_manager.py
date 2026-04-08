import asyncio
import os
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from server.config import CHROME_PATH
from server.services.tab_publisher import TabPublisher

class TabManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context: BrowserContext | None = None
        self.tabs: dict[str, Page] = {}
        self.publishers: dict[str, TabPublisher] = {}
        self.tab_counter = 0

    async def start(self):
        print("Starting TabManager...")
        self.playwright = await async_playwright().start()
        
        launch_kwargs = {
            "headless": True,
            "args": [
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-fake-ui-for-media-stream",
                "--auto-select-desktop-capture-source=Entire screen",
                "--disable-features=WebRtcHideLocalIpsWithMdns",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--unsafely-treat-insecure-origin-as-secure=ws://livekit:7880,http://livekit:7880",
            ],
        }
        
        if CHROME_PATH:
            launch_kwargs["executable_path"] = CHROME_PATH

        self.browser = await self.playwright.chromium.launch(**launch_kwargs)
        self.context = await self.browser.new_context(viewport={'width': 1280, 'height': 720})
        print("TabManager started.")

    async def stop(self):
        if self.context:
            await self.context.close()
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
        _SUPPRESSED = ["Failed to execute 'postMessage' on 'DOMWindow'"]
        page.on("console", lambda msg: (
            None if any(s in msg.text for s in _SUPPRESSED)
            else print(f"PAGE LOG [{msg.type}]: {msg.text}")
        ))
        
        # Instantiate and start the Tab Publisher
        publisher = TabPublisher(page, tab_id)
        self.publishers[tab_id] = publisher
        asyncio.create_task(publisher.start())
        
        print(f"Created new tab: {tab_id}")
        return tab_id, page

    def get_tab(self, tab_id: str) -> Page | None:
        return self.tabs.get(tab_id)

    async def close_tab(self, tab_id: str):
        if tab_id in self.publishers:
            pub = self.publishers.pop(tab_id)
            pub.close()
            
        if tab_id in self.tabs:
            page = self.tabs.pop(tab_id)
            if not page.is_closed():
                await page.close()

tab_manager = TabManager()
