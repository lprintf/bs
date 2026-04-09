"""
Management API — Browser and Tab CRUD
"""
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from server.services.browser_manager import browser_manager

router = APIRouter(prefix="/api/admin", tags=["Management"])


# ── Request Models ──

class CreateBrowserRequest(BaseModel):
    browser_id: Optional[str] = None
    proxy: Optional[str] = None
    user_data_dir: Optional[str] = None
    auto_start: bool = True


class UpdateBrowserRequest(BaseModel):
    proxy: Optional[str] = None
    user_data_dir: Optional[str] = None


class CreateTabRequest(BaseModel):
    url: str = "https://www.google.com"
    viewport_width: int = 1280
    viewport_height: int = 720


class NavigateRequest(BaseModel):
    url: str


class ViewportRequest(BaseModel):
    width: int
    height: int


# ── Dashboard ──

@router.get("/dashboard")
async def get_dashboard():
    return browser_manager.get_dashboard()


# ── Browser CRUD ──

@router.get("/browsers")
async def list_browsers():
    return {
        "total": len(browser_manager.browsers),
        "browsers": [b.to_dict() for b in browser_manager.browsers.values()],
    }


@router.post("/browsers")
async def create_browser(req: CreateBrowserRequest):
    try:
        browser = await browser_manager.create_browser(
            browser_id=req.browser_id,
            proxy=req.proxy,
            user_data_dir=req.user_data_dir,
            auto_start=req.auto_start,
        )
        return browser.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/browsers/{browser_id}")
async def get_browser(browser_id: str):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    return browser.to_dict()


@router.put("/browsers/{browser_id}")
async def update_browser(browser_id: str, req: UpdateBrowserRequest):
    try:
        updates = req.model_dump(exclude_unset=True)
        # Treat empty string proxy as None (remove proxy)
        if "proxy" in updates and (updates["proxy"] is None or updates["proxy"] == ""):
            updates["proxy"] = None
        browser = await browser_manager.update_browser(browser_id, updates)
        return browser.to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/browsers/{browser_id}")
async def delete_browser(browser_id: str):
    try:
        await browser_manager.delete_browser(browser_id)
        return {"message": f"Browser {browser_id} deleted"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/launch")
async def launch_browser(browser_id: str):
    try:
        await browser_manager.launch_browser(browser_id)
        browser = browser_manager.get_browser(browser_id)
        return {"message": f"Browser {browser_id} launched", "status": browser.status}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/close")
async def close_browser(browser_id: str):
    try:
        await browser_manager.close_browser(browser_id)
        browser = browser_manager.get_browser(browser_id)
        return {"message": f"Browser {browser_id} closed", "status": browser.status}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Tab CRUD ──

@router.get("/browsers/{browser_id}/tabs")
async def list_tabs(browser_id: str):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    return {
        "browser_id": browser_id,
        "total": len(browser.tabs),
        "tabs": [t.to_dict() for t in browser.tabs.values()],
    }


@router.post("/browsers/{browser_id}/tabs")
async def create_tab(browser_id: str, req: CreateTabRequest):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        viewport = {"width": req.viewport_width, "height": req.viewport_height}
        tab = await browser.create_tab(url=req.url, viewport=viewport)
        return tab.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/browsers/{browser_id}/tabs/{tab_id}")
async def delete_tab(browser_id: str, tab_id: str):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    if tab_id not in browser.tabs:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    await browser.close_tab(tab_id)
    return {"message": f"Tab {tab_id} closed"}


@router.post("/browsers/{browser_id}/tabs/{tab_id}/navigate")
async def navigate_tab(browser_id: str, tab_id: str, req: NavigateRequest):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        result = await browser.navigate_tab(tab_id, req.url)
        return {"success": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/tabs/{tab_id}/viewport")
async def set_viewport(browser_id: str, tab_id: str, req: ViewportRequest):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        await browser.set_viewport(tab_id, req.width, req.height)
        return {"success": True, "width": req.width, "height": req.height}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/tabs/{tab_id}/back")
async def go_back(browser_id: str, tab_id: str):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        result = await browser.go_back(tab_id)
        return {"success": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/tabs/{tab_id}/forward")
async def go_forward(browser_id: str, tab_id: str):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        result = await browser.go_forward(tab_id)
        return {"success": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/viewers")
async def list_viewers():
    """List all active viewer connections with details."""
    viewers = []
    for browser in browser_manager.browsers.values():
        for tab in browser.tabs.values():
            for i, ws in enumerate(tab.viewers):
                client = ws.client
                viewers.append({
                    "id": f"{tab.tab_id}_v{i}",
                    "tab_id": tab.tab_id,
                    "browser_id": tab.browser_id,
                    "client_host": client.host if client else "unknown",
                    "client_port": client.port if client else 0,
                })
    return {"viewers": viewers, "total": len(viewers)}


@router.delete("/viewers/{tab_id}/{viewer_index}")
async def kick_viewer(tab_id: str, viewer_index: int):
    """Disconnect a specific viewer."""
    tab = browser_manager.find_tab(tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    if viewer_index < 0 or viewer_index >= len(tab.viewers):
        raise HTTPException(status_code=404, detail="Viewer not found")
    ws = tab.viewers[viewer_index]
    try:
        await ws.close()
    except Exception:
        pass
    browser_manager.remove_viewer(tab_id, ws)
    return {"success": True}
