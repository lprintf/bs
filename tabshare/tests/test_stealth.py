"""
stealth 反检测验证脚本
用法: docker exec bs-tabshare-1 python3 /app/tests/test_stealth.py
"""
import asyncio
import json
import os
from pathlib import Path


async def main():
    from rebrowser_playwright.async_api import async_playwright

    STEALTH_JS = Path("/app/server/services/stealth.js").read_text()
    fp = {
        "fingerprint_id": "test_001",
        "os": "mac",
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.50 Safari/537.36",
        "screen_width": 1920,
        "screen_height": 1080,
        "color_depth": 24,
        "pixel_ratio": 2,
        "hardware_concurrency": 8,
        "device_memory": 8,
        "canvas_noise_seed": 12345,
        "webgl_noise_seed": 67890,
        "audio_noise_seed": 11111,
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M3",
        "locale": "en-US",
        "timezone": "Asia/Singapore",
        "latitude": 1.3521,
        "longitude": 103.8198,
        "fonts": ["Arial", "Helvetica", "Times New Roman"],
        "do_not_track": "unset",
    }
    stealth = STEALTH_JS.replace("{{FINGERPRINT_JSON}}", json.dumps(fp))

    chrome_path = os.environ.get("CHROME_PATH", "")

    p = await async_playwright().start()
    launch_kwargs = {
        "headless": False,
        "args": [
            "--headless=new",
            "--no-sandbox",
            "--use-gl=angle",
            "--use-angle=swiftshader-webgl",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=WebRtcHideLocalIpsWithMdns",
        ],
        "ignore_default_args": ["--headless", "--headless=old", "--enable-automation"],
        "user_agent": fp["user_agent"],
    }
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path

    ctx = await p.chromium.launch_persistent_context("/tmp/stealth_test", **launch_kwargs)
    await ctx.add_init_script(stealth)

    for run in range(3):
        page = await ctx.new_page()
        results = await page.evaluate(
            """() => {
            const c = {};

            // --- CDP / Automation detection ---
            c.webdriver = navigator.webdriver;
            c.webdriver_in = 'webdriver' in navigator;
            c.cdc_vars = Object.keys(document)
                .filter(k => k.match(/cdc_|__webdriver|__driver|__selenium/)).length;

            // Error.stack CDP leak
            try { null[0](); } catch(e) {
                c.stack_utility = e.stack.includes('UtilityScript');
                c.stack_playwright = e.stack.includes('playwright') || e.stack.includes('patchright');
                c.stack_cdp = e.stack.includes('Runtime.evaluate') || e.stack.includes('CDP');
                c.stack_sample = e.stack.substring(0, 200);
            }

            // --- Browser APIs ---
            c.chrome_exists = !!window.chrome;
            c.chrome_runtime = !!window.chrome?.runtime;
            c.chrome_csi = typeof window.chrome?.csi;
            c.chrome_loadTimes = typeof window.chrome?.loadTimes;
            c.toString_native = navigator.permissions.query.toString().includes('[native code]');

            // --- Fingerprint ---
            c.notif_perm = typeof Notification !== 'undefined' ? Notification.permission : 'N/A';
            c.plugins_len = navigator.plugins.length;
            c.plugins_type = navigator.plugins instanceof PluginArray;
            c.hw_concurrency = navigator.hardwareConcurrency;
            c.device_memory = navigator.deviceMemory;
            c.languages = JSON.stringify(navigator.languages);
            c.webgl_ok = !!document.createElement('canvas').getContext('webgl');

            // --- WebGL vendor/renderer ---
            try {
                const gl = document.createElement('canvas').getContext('webgl');
                const ext = gl.getExtension('WEBGL_debug_renderer_info');
                c.webgl_vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : 'N/A';
                c.webgl_renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'N/A';
            } catch(e) { c.webgl_vendor = 'ERR'; c.webgl_renderer = 'ERR'; }

            // --- Transparent pixel test ---
            try {
                const cv = document.createElement('canvas');
                cv.width = 1; cv.height = 1;
                const ctx = cv.getContext('2d');
                const px = ctx.getImageData(0, 0, 1, 1).data;
                c.transparent_pixel = {r: px[0], g: px[1], b: px[2], a: px[3]};
            } catch(e) { c.transparent_pixel = 'ERR'; }

            // --- Screen ---
            c.screen = `${screen.width}x${screen.height}`;
            c.outer = `${window.outerWidth}x${window.outerHeight}`;
            c.inner = `${window.innerWidth}x${window.innerHeight}`;
            c.dpr = window.devicePixelRatio;

            return c;
        }"""
        )

        print(f"\n{'='*50}")
        print(f"  Run {run + 1}")
        print(f"{'='*50}")
        for k, v in results.items():
            # Color coding
            if k in ("webdriver", "webdriver_in", "cdc_vars", "stack_utility", "stack_playwright", "stack_cdp"):
                status = "✅" if not v or v == 0 else "❌"
            elif k in ("chrome_exists", "chrome_runtime", "toString_native", "plugins_type", "webgl_ok"):
                status = "✅" if v else "❌"
            elif k == "notif_perm":
                status = "✅" if v == "default" else "❌"
            elif k == "chrome_csi" or k == "chrome_loadTimes":
                status = "✅" if v == "function" else "❌"
            else:
                status = "  "
            print(f"  {status} {k}: {v}")

        await page.close()

    await ctx.close()
    await p.stop()
    print("\n✅ Done")


if __name__ == "__main__":
    asyncio.run(main())
