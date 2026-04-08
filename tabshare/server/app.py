from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from server.services.tab_manager import tab_manager
from server.api import ws
from server.config import SFU_URL
from contextlib import asynccontextmanager
import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    await tab_manager.start()
    tab_id, page = await tab_manager.new_tab()
    await page.goto("https://www.google.com")
    yield
    await tab_manager.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(ws.router)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
async def root():
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
