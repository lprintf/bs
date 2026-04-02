import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    async with async_playwright() as p:
        ext_path = os.path.abspath('extension')
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pw_ext_test",
            headless=False,
            args=[
                f"--disable-extensions-except={ext_path}",
                f"--load-extension={ext_path}",
            ]
        )
        print("Browser launched")
        
        # Wait for service worker
        if not browser.service_workers:
            # Wait a bit
            print("Waiting for service worker...")
            await asyncio.sleep(2)
        
        for sw in browser.service_workers:
            print("Found service worker:", sw.url)
            sw.on("console", lambda msg: print(f"SW LOG: {msg.text}"))
            sw.on("pageerror", lambda err: print(f"SW ERR: {err}"))
            
        await asyncio.sleep(5)
        await browser.close()

asyncio.run(main())
