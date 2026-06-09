"""
Runtime state helpers for Workbenches.

The database owns the active tab and recording intent. WebSocket controller
ownership stays in ws.py because it is tied to live socket objects.
"""
import httpx
from sqlalchemy import select

from server.config import SFU_URL
from server.services.browser_manager import browser_manager
from server.services.database import async_session, WorkbenchModel, WorkbenchTabModel


class WorkbenchRuntimeError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def room_name(browser_id: int, tab_id: int) -> str:
    return f"{browser_id}_{tab_id}"


def _is_live_tab(browser_id: int, tab_id: int) -> bool:
    tab = browser_manager.find_tab(browser_id, tab_id)
    return bool(tab and not tab.page.is_closed())


async def _sfu_recording(room: str, action: str | None = None) -> dict:
    params = {"room": room}
    if action:
        params["action"] = action
    try:
        async with httpx.AsyncClient() as client:
            if action:
                resp = await client.post(f"{SFU_URL}/recording", params=params, timeout=5.0)
            else:
                resp = await client.get(f"{SFU_URL}/recording", params=params, timeout=5.0)
        data = resp.json() if resp.content else {}
    except Exception as exc:
        raise WorkbenchRuntimeError(502, f"SFU recording request failed: {exc}") from exc
    if resp.status_code >= 400:
        detail = data.get("error") or data.get("detail") or resp.text
        raise WorkbenchRuntimeError(resp.status_code, detail)
    return data


async def workbench_allowed_tabs(workbench_id: int) -> set[tuple[int, int]]:
    async with async_session() as session:
        result = await session.execute(
            select(WorkbenchTabModel).where(WorkbenchTabModel.workbench_id == workbench_id)
        )
        return {(row.browser_id, row.tab_id) for row in result.scalars().all()}


async def ensure_active_tab(workbench_id: int) -> dict:
    """Return a live active tab, choosing the first live workbench tab if needed."""
    recording_switch: tuple[str | None, str] | None = None
    async with async_session() as session:
        wb = await session.get(WorkbenchModel, workbench_id)
        if not wb:
            raise WorkbenchRuntimeError(404, f"Workbench {workbench_id} not found")

        result = await session.execute(
            select(WorkbenchTabModel)
            .where(WorkbenchTabModel.workbench_id == workbench_id)
            .order_by(WorkbenchTabModel.id)
        )
        rows = result.scalars().all()
        if not rows:
            raise WorkbenchRuntimeError(409, "Workbench has no tabs")

        candidates: list[WorkbenchTabModel] = []
        if wb.active_browser_id is not None and wb.active_tab_id is not None:
            candidates.extend(
                row
                for row in rows
                if row.browser_id == wb.active_browser_id and row.tab_id == wb.active_tab_id
            )
        candidates.extend(
            row
            for row in rows
            if not (
                row.browser_id == wb.active_browser_id
                and row.tab_id == wb.active_tab_id
            )
        )

        for row in candidates:
            if _is_live_tab(row.browser_id, row.tab_id):
                if wb.active_browser_id != row.browser_id or wb.active_tab_id != row.tab_id:
                    old_room = (
                        room_name(wb.active_browser_id, wb.active_tab_id)
                        if wb.active_browser_id is not None and wb.active_tab_id is not None
                        else None
                    )
                    new_room = room_name(row.browser_id, row.tab_id)
                    if wb.recording_enabled:
                        recording_switch = (old_room, new_room)
                    wb.active_browser_id = row.browser_id
                    wb.active_tab_id = row.tab_id
                    await session.commit()
                else:
                    new_room = room_name(row.browser_id, row.tab_id)

                if recording_switch:
                    old_room, new_room = recording_switch
                    if old_room and old_room != new_room:
                        await _sfu_recording(old_room, "stop")
                    result = await _sfu_recording(new_room, "start")
                    async with async_session() as update_session:
                        update_wb = await update_session.get(WorkbenchModel, workbench_id)
                        if update_wb:
                            update_wb.recording_room = new_room
                            update_wb.recording_file = result.get("file")
                            await update_session.commit()
                return {
                    "browser_id": row.browser_id,
                    "tab_id": row.tab_id,
                    "room": room_name(row.browser_id, row.tab_id),
                }

    raise WorkbenchRuntimeError(409, "Workbench has no active tabs")


async def set_active_tab(workbench_id: int, browser_id: int, tab_id: int) -> dict:
    """Set the workbench active tab and move recording to that room if enabled."""
    new_room = room_name(browser_id, tab_id)

    async with async_session() as session:
        wb = await session.get(WorkbenchModel, workbench_id)
        if not wb:
            raise WorkbenchRuntimeError(404, f"Workbench {workbench_id} not found")
        result = await session.execute(
            select(WorkbenchTabModel).where(
                WorkbenchTabModel.workbench_id == workbench_id,
                WorkbenchTabModel.browser_id == browser_id,
                WorkbenchTabModel.tab_id == tab_id,
            )
        )
        if not result.scalar_one_or_none():
            raise WorkbenchRuntimeError(403, "Tab is not in this workbench")
        if not _is_live_tab(browser_id, tab_id):
            raise WorkbenchRuntimeError(404, "Target tab not found")

        old_room = (
            room_name(wb.active_browser_id, wb.active_tab_id)
            if wb.active_browser_id is not None and wb.active_tab_id is not None
            else None
        )
        recording_enabled = bool(wb.recording_enabled)
        wb.active_browser_id = browser_id
        wb.active_tab_id = tab_id
        await session.commit()

    recording: dict | None = None
    if recording_enabled and old_room != new_room:
        if old_room:
            await _sfu_recording(old_room, "stop")
        recording = await _sfu_recording(new_room, "start")
        async with async_session() as session:
            wb = await session.get(WorkbenchModel, workbench_id)
            if wb:
                wb.recording_room = new_room
                wb.recording_file = recording.get("file")
                await session.commit()

    return {
        "workbench_id": workbench_id,
        "browser_id": browser_id,
        "tab_id": tab_id,
        "room": new_room,
        "recording": recording,
    }


async def set_recording(workbench_id: int, enabled: bool) -> dict:
    """Start/stop recording for the current workbench active room."""
    active = await ensure_active_tab(workbench_id) if enabled else None
    active_room = active["room"] if active else None

    async with async_session() as session:
        wb = await session.get(WorkbenchModel, workbench_id)
        if not wb:
            raise WorkbenchRuntimeError(404, f"Workbench {workbench_id} not found")
        previous_room = wb.recording_room
        fallback_room = (
            room_name(wb.active_browser_id, wb.active_tab_id)
            if wb.active_browser_id is not None and wb.active_tab_id is not None
            else None
        )

    if enabled:
        if not active_room:
            raise WorkbenchRuntimeError(409, "Workbench has no active tab to record")
        if previous_room and previous_room != active_room:
            await _sfu_recording(previous_room, "stop")
        result = await _sfu_recording(active_room, "start")
        async with async_session() as session:
            wb = await session.get(WorkbenchModel, workbench_id)
            if wb:
                wb.recording_enabled = True
                wb.recording_room = active_room
                wb.recording_file = result.get("file")
                await session.commit()
        return {
            "workbench_id": workbench_id,
            "recording_enabled": True,
            "recording_room": active_room,
            "recording_file": result.get("file"),
            "sfu": result,
        }

    stop_room = previous_room or fallback_room
    result = await _sfu_recording(stop_room, "stop") if stop_room else {}
    async with async_session() as session:
        wb = await session.get(WorkbenchModel, workbench_id)
        if wb:
            wb.recording_enabled = False
            wb.recording_room = None
            wb.recording_file = result.get("file") or wb.recording_file
            await session.commit()
    return {
        "workbench_id": workbench_id,
        "recording_enabled": False,
        "recording_room": None,
        "recording_file": result.get("file"),
        "sfu": result,
    }


async def get_recording_status(workbench_id: int) -> dict:
    active = await ensure_active_tab(workbench_id)
    async with async_session() as session:
        wb = await session.get(WorkbenchModel, workbench_id)
        if not wb:
            raise WorkbenchRuntimeError(404, f"Workbench {workbench_id} not found")
        room = wb.recording_room or active["room"]
        enabled = bool(wb.recording_enabled)
        recording_file = wb.recording_file

    sfu_status = await _sfu_recording(room)
    return {
        "workbench_id": workbench_id,
        "active_room": active["room"],
        "recording_enabled": enabled and bool(sfu_status.get("recording")),
        "recording_room": room if sfu_status.get("recording") else None,
        "recording_file": sfu_status.get("file") or recording_file,
        "sfu": sfu_status,
    }
