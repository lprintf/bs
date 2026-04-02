import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--headless=new",
                "--use-fake-ui-for-media-stream",
            ]
        )
        page = await browser.new_page()
        await page.goto("https://www.google.com")
        
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
