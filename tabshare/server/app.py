from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from server.services.tab_manager import tab_manager
from server.api import ws, livekit_api, ws_proxy
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # on startup
    await tab_manager.start()
    # Create an initial tab for demonstration
    tab_id, page = await tab_manager.new_tab()
    await page.goto("https://www.google.com")
    yield
    # on shutdown
    await tab_manager.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(ws.router)
app.include_router(livekit_api.router)
app.include_router(ws_proxy.router)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
async def root():
    # Helper to just redirect to the first tab viewer or show a list
    if not tab_manager.tabs:
        return HTMLResponse("<h1>No tabs available</h1>")
    
    first_tab_id = list(tab_manager.tabs.keys())[0]
    return RedirectResponse(f"/viewer/{first_tab_id}")

@app.get("/viewer/{tab_id}")
async def viewer(tab_id: str):
    viewer_file = static_dir / "viewer.html"
    if not viewer_file.exists():
        return HTMLResponse("<h1>viewer.html not found</h1>", status_code=404)
    with open(viewer_file, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    # Simple template substitution for tab_id if we want, 
    # but the frontend can just parse the URL.
    return HTMLResponse(html_content)

if __name__ == "__main__":
    import uvicorn
    from server.config import HOST, PORT
    uvicorn.run("server.app:app", host=HOST, port=PORT)
