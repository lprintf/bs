from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from server.services.browser_manager import browser_manager
from server.api import ws, management
from server.config import SFU_URL
from contextlib import asynccontextmanager
import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create a default browser with a tab on startup
    browser = await browser_manager.create_browser(browser_id="b1")
    tab = await browser.create_tab(url="https://www.google.com")
    print(f"Default browser ready: {browser.browser_id}, tab: {tab.tab_id}")
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

@app.get("/viewer/{tab_id}")
async def viewer(tab_id: str):
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
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=502)

@app.post("/api/eval/{tab_id}")
async def eval_js(tab_id: str, request: Request):
    """Debug: evaluate JS in a tab."""
    body = await request.json()
    js = body.get("js", "1+1")
    tab = browser_manager.find_tab(tab_id)
    if not tab:
        return JSONResponse({"error": "Tab not found"}, status_code=404)
    try:
        result = await tab.page.evaluate(js)
        return {"result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── File serving ──
from server.services.browser_instance import DOWNLOADS_DIR

@app.get("/api/files/{tab_id}/{filename}")
async def serve_download(tab_id: str, filename: str):
    """Serve a file downloaded by the remote browser."""
    file_path = DOWNLOADS_DIR / tab_id / filename
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
