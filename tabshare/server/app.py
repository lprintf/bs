from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from server.services.browser_manager import browser_manager
from server.services.database import init_db
from server.api import ws, management
from server.config import SFU_URL
from contextlib import asynccontextmanager
import httpx

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

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
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
    """Debug: 获取 Chrome GPU/WebGL 信息"""
    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab:
        return JSONResponse({"error": "Tab not found"}, status_code=404)
    try:
        # 方法1: 通过 JS 检测 WebGL
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

        return {"webgl": js_info}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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

if __name__ == "__main__":
    import uvicorn
    from server.config import HOST, PORT
    uvicorn.run("server.app:app", host=HOST, port=PORT)
