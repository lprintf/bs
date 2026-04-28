#!/usr/bin/env python3
"""直接通过 CDP 获取 WebRTC encoder stats，诊断硬件编码是否生效"""
import asyncio
import json
import subprocess
import urllib.request


def get_cdp_targets():
    """获取所有 Chrome CDP targets"""
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)
        return json.loads(resp.read())
    except Exception:
        return []


async def check_via_playwright():
    """通过 rebrowser_playwright 连接并检查"""
    try:
        from rebrowser_playwright.async_api import async_playwright
    except ImportError:
        print("rebrowser_playwright not available")
        return

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        except Exception as e:
            print(f"CDP connect failed: {e}")
            return

        for ctx in browser.contexts:
            for page in ctx.pages:
                try:
                    stats = await page.evaluate("""async () => {
                        if (!window._webrtc || !window._webrtc.pc) return 'NO_WEBRTC_PC';
                        try {
                            const stats = await window._webrtc.pc.getStats();
                            let result = [];
                            stats.forEach(r => {
                                if (r.type === 'outbound-rtp' && r.kind === 'video') {
                                    result.push({
                                        encoderImpl: r.encoderImplementation || '?',
                                        qlr: r.qualityLimitationReason || 'none',
                                        targetBitrate_kbps: Math.round((r.targetBitrate || 0) / 1000),
                                        fps: r.framesPerSecond || 0,
                                        framesEncoded: r.framesEncoded || 0,
                                        keyFrames: r.keyFramesEncoded || 0,
                                        qpSum: r.qpSum || 0,
                                    });
                                }
                            });
                            return result.length > 0 ? JSON.stringify(result, null, 2) : 'NO_OUTBOUND_RTP';
                        } catch(e) {
                            return 'STATS_ERROR:' + e.toString();
                        }
                    }""")
                    url = page.url[:60] if page.url else "?"
                    print(f"\n=== Page: {url} ===")
                    print(stats)
                    if stats not in ("NO_WEBRTC_PC", "NO_OUTBOUND_RTP"):
                        return  # 找到一个有效的就够了
                except Exception as e:
                    continue

        print("\nNo active WebRTC streams found in any page")


def check_gpu_usage():
    """检查 GPU 引擎占用"""
    engines = [
        "/sys/class/drm/card0/engine/vcs0",
        "/sys/class/drm/card0/engine/vcs1",
        "/sys/class/drm/card0/engine/vecs0",
    ]
    for eng in engines:
        try:
            with open(f"{eng}/name") as f:
                name = f.read().strip()
            print(f"  {name}: present")
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    print("=== GPU Engine Check ===")
    check_gpu_usage()

    print("\n=== CDP Targets ===")
    targets = get_cdp_targets()
    if targets:
        for t in targets[:3]:
            print(f"  [{t.get('type')}] {t.get('title', '?')[:50]} - {t.get('url', '?')[:60]}")
    else:
        print("  No CDP targets (port 9222 not responding)")
        print("  Note: Chrome was launched with --remote-debugging-pipe, not --remote-debugging-port")

    print("\n=== Encoder Stats (via Playwright) ===")
    asyncio.run(check_via_playwright())
