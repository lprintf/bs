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

if __name__ == "__main__":
    import uvicorn
    from server.config import HOST, PORT
    uvicorn.run("server.app:app", host=HOST, port=PORT)
