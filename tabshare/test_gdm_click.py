import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/tmp/pw_ext_test_11",
            executable_path="/usr/bin/google-chrome",
            headless=False,
            args=[
                "--auto-select-tab-capture-source-by-title=WebRTC-Target",
                "--allow-http-screen-capture"
            ]
        )
        page = await browser.new_page()
        await page.goto("https://webrtc.github.io/samples/")
        await page.evaluate('document.title = "WebRTC-Target";')
        
        # Add a button to click
        await page.evaluate('''() => {
            const btn = document.createElement('button');
            btn.id = 'cap-btn';
            btn.innerText = 'Capture';
            btn.onclick = async () => {
                try {
                    window.stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
                    window.streamId = window.stream.id;
                } catch(e) {
                    window.streamId = "Error: " + e.message;
                }
            };
            document.body.appendChild(btn);
        }''')
        
        await page.click('#cap-btn')
        await asyncio.sleep(2)
        stream_id = await page.evaluate('window.streamId')
        print("Result:", stream_id)
        
        await browser.close()

asyncio.run(test())
