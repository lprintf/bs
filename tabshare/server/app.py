from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from server.services.browser_manager import browser_manager
from server.services.database import init_db
from server.api import ws, management
from server.config import SFU_URL
from contextlib import asynccontextmanager
import httpx
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables
    await init_db()
    # Load persisted browser configs (all start as closed)
    await browser_manager.load_from_db()
    count = len(browser_manager.browsers)
    print(f"Loaded {count} browser(s) from database")
    yield
    await browser_manager.shutdown()

app = FastAPI(lifespan=lifespan)
app.include_router(ws.router)
app.include_router(management.router)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
async def root():
    return RedirectResponse("/admin")

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    admin_spa = static_dir / "admin-spa" / "index.html"
    if admin_spa.exists():
        return FileResponse(admin_spa)
    admin_html = static_dir / "admin.html"
    if admin_html.exists():
        return FileResponse(admin_html)
    return HTMLResponse("<h1>Admin page not found</h1>", status_code=404)

@app.get("/viewer/{browser_id}/{tab_id}")
async def viewer(browser_id: int, tab_id: int):
    viewer_file = static_dir / "viewer.html"
    if not viewer_file.exists():
        return HTMLResponse("<h1>viewer.html not found</h1>", status_code=404)
    with open(viewer_file, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(html_content)

@app.get("/workbench")
async def workbench():
    return RedirectResponse("/admin")

@app.get("/workbench/{workbench_id}")
async def workbench_by_id(workbench_id: int):
    workbench_file = static_dir / "workbench.html"
    if not workbench_file.exists():
        return HTMLResponse("<h1>workbench.html not found</h1>", status_code=404)
    with open(workbench_file, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(html_content)

@app.api_route("/api/sfu/offer", methods=["POST"])
async def sfu_offer_proxy(request: Request):
    """Proxy SDP offer to Go SFU (for viewer connections from outside)."""
    body = await request.body()
    query = str(request.url.query)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SFU_URL}/offer?{query}",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=10.0,
            )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=502)

@app.api_route("/api/sfu/switch", methods=["POST"])
async def sfu_switch_proxy(request: Request):
    """Proxy track switch to Go SFU (fast tab switching without re-negotiation)."""
    query = str(request.url.query)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SFU_URL}/switch?{query}",
                timeout=5.0,
            )
        try:
            data = resp.json()
        except Exception:
            data = {"error": resp.text}
        return JSONResponse(content=data, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=502)

@app.api_route("/api/sfu/ready", methods=["GET", "POST"])
async def sfu_ready_proxy(request: Request):
    """Proxy SFU room readiness checks."""
    query = str(request.url.query)
    try:
        async with httpx.AsyncClient() as client:
            if request.method == "GET":
                resp = await client.get(f"{SFU_URL}/ready?{query}", timeout=5.0)
            else:
                resp = await client.post(f"{SFU_URL}/ready?{query}", timeout=5.0)
        try:
            data = resp.json()
        except Exception:
            data = {"error": resp.text}
        return JSONResponse(content=data, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=502)

@app.api_route("/api/sfu/recording", methods=["GET", "POST"])
async def sfu_recording_proxy(request: Request):
    """Proxy recording control to Go SFU."""
    query = str(request.url.query)
    try:
        async with httpx.AsyncClient() as client:
            if request.method == "GET":
                resp = await client.get(f"{SFU_URL}/recording?{query}", timeout=5.0)
            else:
                resp = await client.post(f"{SFU_URL}/recording?{query}", timeout=5.0)
        try:
            data = resp.json()
        except Exception:
            data = {"error": resp.text}
        return JSONResponse(content=data, status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=502)

@app.post("/api/eval/{browser_id}/{tab_id}")
async def eval_js(browser_id: int, tab_id: int, request: Request):
    """Debug: evaluate JS in a tab."""
    body = await request.json()
    js = body.get("js", "1+1")
    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab:
        return JSONResponse({"error": "Tab not found"}, status_code=404)
    try:
        result = await tab.page.evaluate(js)
        return {"result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/gpu-info/{browser_id}/{tab_id}")
async def gpu_info(browser_id: int, tab_id: int):
    """Debug: 获取 Chrome GPU/WebGL + WebRTC 编码器信息"""
    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab:
        return JSONResponse({"error": "Tab not found"}, status_code=404)
    try:
        # WebGL 信息
        js_info = await tab.page.evaluate("""() => {
            const info = {};
            const c = document.createElement('canvas');
            for (const ctx of ['webgl2', 'webgl', 'experimental-webgl']) {
                const gl = c.getContext(ctx, {failIfMajorPerformanceCaveat: false});
                if (gl) {
                    info.context = ctx;
                    info.renderer = gl.getParameter(gl.RENDERER);
                    info.vendor = gl.getParameter(gl.VENDOR);
                    info.version = gl.getParameter(gl.VERSION);
                    info.shadingLanguage = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
                    const ext = gl.getExtension('WEBGL_debug_renderer_info');
                    if (ext) {
                        info.unmaskedRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
                        info.unmaskedVendor = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
                    }
                    break;
                }
            }
            if (!info.context) info.error = 'No WebGL context available';
            info.hardwareConcurrency = navigator.hardwareConcurrency;
            info.platform = navigator.platform;
            return info;
        }""")

        # WebRTC encoder stats
        encoder_info = await _get_encoder_stats(tab)

        return {"webgl": js_info, "encoder": encoder_info}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _get_encoder_stats(tab) -> dict:
    """从 tab 的 WebRTC PeerConnection 中提取编码器状态"""
    try:
        return await tab.page.evaluate("""async () => {
            if (!window._webrtc || !window._webrtc.pc) {
                return { error: 'No WebRTC PeerConnection' };
            }
            const pc = window._webrtc.pc;
            const result = {
                iceState: pc.iceConnectionState,
                signalingState: pc.signalingState,
            };
            try {
                const stats = await pc.getStats();
                stats.forEach(r => {
                    if (r.type === 'outbound-rtp' && r.kind === 'video') {
                        result.encoderImplementation = r.encoderImplementation || 'unknown';
                        result.qualityLimitationReason = r.qualityLimitationReason || 'none';
                        result.targetBitrate = r.targetBitrate ? Math.round(r.targetBitrate / 1000) : null;
                        result.framesPerSecond = r.framesPerSecond || 0;
                        result.framesEncoded = r.framesEncoded || 0;
                        result.keyFramesEncoded = r.keyFramesEncoded || 0;
                        result.qpSum = r.qpSum || 0;
                        result.bytesSent = r.bytesSent || 0;
                        result.codec = r.codecId || null;
                        result.isHardware = !!(r.encoderImplementation && (
                            r.encoderImplementation.includes('ExternalEncoder') ||
                            r.encoderImplementation.includes('Vaapi') ||
                            r.encoderImplementation.includes('VAAPI') ||
                            r.encoderImplementation.includes('VideoToolbox') ||
                            r.encoderImplementation.includes('NVENC') ||
                            r.encoderImplementation.includes('MediaCodec')
                        ));
                    }
                    if (r.type === 'codec' && r.mimeType && r.mimeType.includes('video')) {
                        result.codecMime = r.mimeType;
                        result.codecPayloadType = r.payloadType;
                    }
                });
            } catch(e) {
                result.statsError = e.toString();
            }
            return result;
        }""")
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/encoder-stats")
async def encoder_stats_all():
    """批量查询所有活跃 tab 的编码器状态 — 用于诊断硬件编码是否生效"""
    results = []
    for browser in browser_manager.browsers.values():
        if browser.status != "idle":
            continue
        for tab_id, tab in browser.tabs.items():
            if tab.page.is_closed():
                continue
            info = await _get_encoder_stats(tab)
            info["browser_id"] = browser.browser_id
            info["tab_id"] = tab_id
            info["room"] = tab.room_name
            results.append(info)

    # 汇总
    hw_count = sum(1 for r in results if r.get("isHardware"))
    sw_count = sum(1 for r in results if r.get("encoderImplementation") and not r.get("isHardware"))
    err_count = sum(1 for r in results if r.get("error"))

    return {
        "summary": {
            "total": len(results),
            "hardware": hw_count,
            "software": sw_count,
            "error_or_no_pc": err_count,
        },
        "tabs": results,
    }


# ── File serving ──
from server.services.browser_instance import DOWNLOADS_DIR

@app.get("/api/files/{room_name}/{filename}")
async def serve_download(room_name: str, filename: str):
    """Serve a file downloaded by the remote browser."""
    file_path = DOWNLOADS_DIR / room_name / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(
        str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )

# ── Codec config API ──

@app.get("/api/codec")
async def get_codec():
    """获取当前视频编码偏好"""
    from server.config import VIDEO_CODEC
    return {"codec": VIDEO_CODEC, "options": ["h264", "vp8", "vp9", "av1"]}

@app.put("/api/codec")
async def set_codec(request: Request):
    """设置视频编码偏好（需重建 stream 生效）"""
    import server.config as cfg
    body = await request.json()
    codec = body.get("codec", "").lower()
    if codec not in ("h264", "vp8", "vp9", "av1"):
        return JSONResponse({"error": f"unsupported codec: {codec}"}, status_code=400)
    cfg.VIDEO_CODEC = codec
    os.environ["VIDEO_CODEC"] = codec
    return {"codec": codec, "message": "Codec preference updated. Reconnect viewers to apply."}

if __name__ == "__main__":
    import uvicorn
    from server.config import HOST, PORT
    uvicorn.run("server.app:app", host=HOST, port=PORT)
