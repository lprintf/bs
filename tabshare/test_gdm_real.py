import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pw_ext_test_10",
            executable_path="/usr/bin/google-chrome",
            headless=False,
            args=[
                "--auto-select-tab-capture-source-by-title=WebRTC-Target",
                "--allow-http-screen-capture"
            ]
        )
        page = await browser.new_page()
        await page.goto("https://www.google.com")
        await page.evaluate('document.title = "WebRTC-Target";')
        try:
            res = await page.evaluate('''async () => {
                try {
                    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
                    return "GDM Success: " + stream.id;
                } catch(e) {
                    return "GDM Error: " + e.message;
                }
            }''')
            print(res)
        except Exception as e:
            print("Evaluate error:", e)
        await browser.close()

asyncio.run(test())
