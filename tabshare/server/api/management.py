"""
Management API — Browser and Tab CRUD
"""
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from server.services.browser_manager import browser_manager
from server.services.browser_instance import UPLOADS_DIR, _url_hostname
from server.services.database import async_session, TabModel, WorkbenchModel, WorkbenchTabModel

router = APIRouter(prefix="/api/admin", tags=["Management"])


# ── Request Models ──

class CreateBrowserRequest(BaseModel):
    name: str = ""
    proxy: Optional[str] = None
    user_data_dir: Optional[str] = None
    locale: Optional[str] = None
    timezone: Optional[str] = None
    chrome_version: Optional[str] = None
    auto_start: bool = True


class UpdateBrowserRequest(BaseModel):
    name: Optional[str] = None
    proxy: Optional[str] = None
    chrome_version: Optional[str] = None


class CreateTabRequest(BaseModel):
    url: str = "https://www.bing.com"
    viewport_width: int = 1280
    viewport_height: int = 720


class NavigateRequest(BaseModel):
    url: str


class ViewportRequest(BaseModel):
    width: int
    height: int


class WorkbenchTabRef(BaseModel):
    browser_id: int
    tab_id: int


class WorkbenchRequest(BaseModel):
    name: str = ""
    tabs: list[WorkbenchTabRef]


def _workbench_url(workbench_id: int) -> str:
    return f"/workbench/{workbench_id}"


async def _get_workbench_row(workbench_id: int):
    async with async_session() as session:
        wb = await session.get(WorkbenchModel, workbench_id)
        if not wb:
            return None, []
        result = await session.execute(
            select(WorkbenchTabModel)
            .where(WorkbenchTabModel.workbench_id == workbench_id)
            .order_by(WorkbenchTabModel.id)
        )
        return wb, result.scalars().all()


async def _serialize_workbench(wb: WorkbenchModel, rows: list[WorkbenchTabModel], apply_runtime: bool = False) -> dict:
    tabs = []
    active_tabs = []

    for row in rows:
        tab = browser_manager.find_tab(row.browser_id, row.tab_id)
        if tab and not tab.page.is_closed():
            browser = browser_manager.get_browser(row.browser_id)
            if apply_runtime and browser:
                try:
                    tab = await browser.assign_tab_to_workbench(
                        row.tab_id,
                        wb.id,
                        wb.name,
                        row.allowed_hostname,
                    )
                except Exception:
                    pass
            tab_data = tab.to_dict()
            tab_data["workbench_tab_id"] = row.id
            tab_data["available"] = True
            tabs.append(tab_data)
            active_tabs.append(tab_data)
        else:
            tabs.append({
                "workbench_tab_id": row.id,
                "browser_id": row.browser_id,
                "tab_id": row.tab_id,
                "url": None,
                "viewport": None,
                "viewer_count": 0,
                "status": "missing",
                "available": False,
                "domain_locked": True,
                "allowed_hostname": row.allowed_hostname,
                "workbench_id": wb.id,
                "workbench_name": wb.name,
            })

    data = wb.to_dict()
    data.update({
        "tabs": tabs,
        "active_tabs": active_tabs,
        "tab_count": len(tabs),
        "active_tab_count": len(active_tabs),
        "url": _workbench_url(wb.id),
    })
    return data


# ── Dashboard ──

@router.get("/dashboard")
async def get_dashboard():
    return browser_manager.get_dashboard()


# ── Chrome Versions ──

@router.get("/chrome-versions")
async def get_chrome_versions():
    """返回可用的 Chrome 版本号列表（供前端选择）"""
    from server.services.fingerprint import _CHROME_VERSIONS, _detect_chrome_version
    real_ver = _detect_chrome_version()
    return {
        "real_version": real_ver,
        "available_versions": _CHROME_VERSIONS,
    }


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
            name=req.name,
            proxy=req.proxy,
            user_data_dir=req.user_data_dir,
            locale=req.locale,
            timezone=req.timezone,
            chrome_version=req.chrome_version,
            auto_start=req.auto_start,
        )
        return browser.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/browsers/{browser_id}")
async def get_browser(browser_id: int):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    return browser.to_dict()


@router.put("/browsers/{browser_id}")
async def update_browser(browser_id: int, req: UpdateBrowserRequest):
    try:
        updates = req.model_dump(exclude_unset=True)
        if "proxy" in updates and (updates["proxy"] is None or updates["proxy"] == ""):
            updates["proxy"] = None
        browser = await browser_manager.update_browser(browser_id, updates)
        return browser.to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/browsers/{browser_id}")
async def delete_browser(browser_id: int):
    try:
        await browser_manager.delete_browser(browser_id)
        return {"message": f"Browser {browser_id} deleted"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/launch")
async def launch_browser(browser_id: int):
    try:
        await browser_manager.launch_browser(browser_id)
        browser = browser_manager.get_browser(browser_id)
        return {"message": f"Browser {browser_id} launched", "status": browser.status}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/close")
async def close_browser(browser_id: int):
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
async def list_tabs(browser_id: int):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    return {
        "browser_id": browser_id,
        "total": len(browser.tabs),
        "tabs": [t.to_dict() for t in browser.tabs.values()],
    }


@router.post("/browsers/{browser_id}/tabs")
async def create_tab(browser_id: int, req: CreateTabRequest):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        viewport = {"width": req.viewport_width, "height": req.viewport_height}
        tab = await browser_manager.create_persistent_tab(
            browser_id=browser_id,
            url=req.url,
            viewport=viewport,
            created_by="admin",
        )
        return tab.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/browsers/{browser_id}/tabs/{tab_id}")
async def delete_tab(browser_id: int, tab_id: int):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    if tab_id not in browser.tabs:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    try:
        await browser_manager.delete_persistent_tab(browser_id, tab_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await browser.close_tab(tab_id)
    return {"message": f"Tab {tab_id} closed"}


@router.post("/browsers/{browser_id}/tabs/{tab_id}/prepare-stream")
async def prepare_tab_stream(browser_id: int, tab_id: int, timeout_ms: int = 8000):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        ready = await browser_manager.prepare_tab_stream(browser_id, tab_id, timeout_ms)
        return {
            "success": ready,
            "ready": ready,
            "browser_id": browser_id,
            "tab_id": tab_id,
            "room": f"{browser_id}_{tab_id}",
        }
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        return {
            "success": False,
            "ready": False,
            "browser_id": browser_id,
            "tab_id": tab_id,
            "error": str(e),
        }


@router.post("/browsers/{browser_id}/tabs/{tab_id}/navigate")
async def navigate_tab(browser_id: int, tab_id: int, req: NavigateRequest):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        result = await browser.navigate_tab(tab_id, req.url)
        await browser_manager.update_persistent_tab_url(browser_id, tab_id, result["url"])
        return {"success": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/tabs/{tab_id}/viewport")
async def set_viewport(browser_id: int, tab_id: int, req: ViewportRequest):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        await browser.set_viewport(tab_id, req.width, req.height)
        await browser_manager.update_persistent_tab_viewport(browser_id, tab_id, req.width, req.height)
        return {"success": True, "width": req.width, "height": req.height}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/tabs/{tab_id}/back")
async def go_back(browser_id: int, tab_id: int):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        result = await browser.go_back(tab_id)
        await browser_manager.update_persistent_tab_url(browser_id, tab_id, result["url"])
        return {"success": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browsers/{browser_id}/tabs/{tab_id}/forward")
async def go_forward(browser_id: int, tab_id: int):
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    try:
        result = await browser.go_forward(tab_id)
        await browser_manager.update_persistent_tab_url(browser_id, tab_id, result["url"])
        return {"success": True, **result}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workbenches")
async def create_workbench(req: WorkbenchRequest):
    """Persist a fixed tab group for the workbench page and lock tab domains."""
    if not req.tabs:
        raise HTTPException(status_code=400, detail="Select at least one tab")

    seen: set[tuple[int, int]] = set()
    selected = []

    for ref in req.tabs:
        key = (ref.browser_id, ref.tab_id)
        if key in seen:
            continue
        seen.add(key)

        browser = browser_manager.get_browser(ref.browser_id)
        if not browser:
            raise HTTPException(status_code=404, detail=f"Browser {ref.browser_id} not found")
        tab = browser.tabs.get(ref.tab_id)
        if not tab or tab.page.is_closed():
            raise HTTPException(status_code=404, detail=f"Tab {ref.tab_id} not found")
        if tab.workbench_id:
            raise HTTPException(
                status_code=409,
                detail=f"Tab {ref.browser_id}/{ref.tab_id} already belongs to workbench {tab.workbench_name or tab.workbench_id}",
            )
        if not tab.persistent or tab.created_by != "admin":
            raise HTTPException(
                status_code=400,
                detail=f"Tab {ref.browser_id}/{ref.tab_id} is not an admin-created persistent tab",
            )
        hostname = _url_hostname(tab.page.url)
        if not hostname:
            raise HTTPException(status_code=400, detail=f"Tab {ref.browser_id}/{ref.tab_id} has no HTTP(S) domain to lock")
        selected.append((browser, tab, hostname))

    if not selected:
        raise HTTPException(status_code=400, detail="Select at least one tab")

    name = req.name.strip() or "Workbench"

    async with async_session() as session:
        for _browser, tab, _hostname in selected:
            tab_result = await session.execute(
                select(TabModel).where(
                    TabModel.browser_id == tab.browser_id,
                    TabModel.tab_id == tab.tab_id,
                    TabModel.created_by == "admin",
                    TabModel.enabled == True,
                )
            )
            if not tab_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=400,
                    detail=f"Tab {tab.browser_id}/{tab.tab_id} is not an admin-created persistent tab",
                )
            existing = await session.execute(
                select(WorkbenchTabModel).where(
                    WorkbenchTabModel.browser_id == tab.browser_id,
                    WorkbenchTabModel.tab_id == tab.tab_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                wb = await session.get(WorkbenchModel, row.workbench_id)
                label = wb.name if wb else row.workbench_id
                raise HTTPException(
                    status_code=409,
                    detail=f"Tab {tab.browser_id}/{tab.tab_id} already belongs to workbench {label}",
                )

        wb = WorkbenchModel(name=name)
        session.add(wb)
        await session.flush()
        for _browser, tab, hostname in selected:
            session.add(WorkbenchTabModel(
                workbench_id=wb.id,
                browser_id=tab.browser_id,
                tab_id=tab.tab_id,
                allowed_hostname=hostname,
            ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(status_code=409, detail="One or more tabs already belong to another workbench")

    for browser, tab, hostname in selected:
        await browser.assign_tab_to_workbench(tab.tab_id, wb.id, name, hostname)

    wb_row, tab_rows = await _get_workbench_row(wb.id)
    return await _serialize_workbench(wb_row, tab_rows, apply_runtime=True)


@router.get("/workbenches")
async def list_workbenches():
    """List persisted workbenches."""
    async with async_session() as session:
        result = await session.execute(select(WorkbenchModel).order_by(WorkbenchModel.id))
        rows = result.scalars().all()

    workbenches = []
    for wb in rows:
        wb_row, tab_rows = await _get_workbench_row(wb.id)
        if wb_row:
            workbenches.append(await _serialize_workbench(wb_row, tab_rows))
    return {"workbenches": workbenches, "total": len(workbenches)}


@router.get("/workbenches/{workbench_id}")
async def get_workbench(workbench_id: int):
    """Load a persisted workbench and re-apply runtime locks to active tabs."""
    wb, rows = await _get_workbench_row(workbench_id)
    if not wb:
        raise HTTPException(status_code=404, detail=f"Workbench {workbench_id} not found")
    data = await _serialize_workbench(wb, rows, apply_runtime=True)
    if not data["active_tabs"]:
        raise HTTPException(status_code=409, detail="Workbench has no active tabs")
    return data


@router.delete("/workbenches/{workbench_id}")
async def delete_workbench(workbench_id: int):
    """Delete a persisted workbench and release active tab assignments."""
    wb, rows = await _get_workbench_row(workbench_id)
    if not wb:
        raise HTTPException(status_code=404, detail=f"Workbench {workbench_id} not found")
    workbench_name = wb.name or f"Workbench {workbench_id}"

    async with async_session() as session:
        await session.execute(delete(WorkbenchTabModel).where(WorkbenchTabModel.workbench_id == workbench_id))
        await session.execute(delete(WorkbenchModel).where(WorkbenchModel.id == workbench_id))
        await session.commit()

    for row in rows:
        browser = browser_manager.get_browser(row.browser_id)
        if browser:
            browser.clear_tab_workbench(row.tab_id, workbench_id)

    from server.api.ws import close_workbench_clients
    await close_workbench_clients(workbench_id, f"工作台 {workbench_name} 已删除")

    return {
        "success": True,
        "message": f"Workbench {workbench_id} deleted",
        "workbench_id": workbench_id,
        "released_tabs": len(rows),
    }


@router.get("/viewers")
async def list_viewers():
    """List all active viewer connections with details."""
    viewers = []
    for browser in browser_manager.browsers.values():
        for tab in browser.tabs.values():
            for i, ws in enumerate(tab.viewers):
                client = ws.client
                viewers.append({
                    "id": f"{tab.browser_id}_{tab.tab_id}_v{i}",
                    "tab_id": tab.tab_id,
                    "browser_id": tab.browser_id,
                    "client_host": client.host if client else "unknown",
                    "client_port": client.port if client else 0,
                })
    return {"viewers": viewers, "total": len(viewers)}


@router.delete("/viewers/{browser_id}/{tab_id}/{viewer_index}")
async def kick_viewer(browser_id: int, tab_id: int, viewer_index: int):
    """Disconnect a specific viewer."""
    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail="Tab not found")
    if viewer_index < 0 or viewer_index >= len(tab.viewers):
        raise HTTPException(status_code=404, detail="Viewer not found")
    ws = tab.viewers[viewer_index]
    try:
        await ws.close()
    except Exception:
        pass
    browser_manager.remove_viewer(browser_id, tab_id, ws)
    return {"success": True}


# ── File Transfer ──

@router.post("/browsers/{browser_id}/tabs/{tab_id}/upload")
async def upload_file(
    browser_id: int,
    tab_id: int,
    file: UploadFile = File(...),
    set_input: str | None = None,
):
    """Upload a file. If set_input is a CSS selector, set it on that file input."""
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    tab = browser.tabs.get(tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")

    # Save file
    room_name = f"{browser_id}_{tab_id}"
    tab_dir = UPLOADS_DIR / room_name
    tab_dir.mkdir(parents=True, exist_ok=True)
    save_path = tab_dir / file.filename
    content = await file.read()
    save_path.write_bytes(content)

    result = {"filename": file.filename, "size": len(content), "path": str(save_path)}

    # Auto-inject via pending file chooser dialog
    if tab.pending_filechooser:
        try:
            await tab.pending_filechooser.set_files(str(save_path))
            tab.pending_filechooser = None
            result["injected"] = True
            result["method"] = "filechooser"
        except Exception as e:
            result["inject_error"] = str(e)
            tab.pending_filechooser = None
    elif set_input:
        try:
            await tab.page.set_input_files(set_input, str(save_path))
            result["injected"] = True
            result["method"] = "set_input"
        except Exception as e:
            result["inject_error"] = str(e)

    return result


@router.get("/browsers/{browser_id}/tabs/{tab_id}/downloads")
async def list_downloads(browser_id: int, tab_id: int):
    """List files downloaded by the remote browser tab."""
    browser = browser_manager.get_browser(browser_id)
    if not browser:
        raise HTTPException(status_code=404, detail=f"Browser {browser_id} not found")
    tab = browser.tabs.get(tab_id)
    if not tab:
        raise HTTPException(status_code=404, detail=f"Tab {tab_id} not found")
    return {"downloads": tab.downloads}
