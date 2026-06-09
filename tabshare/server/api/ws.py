"""
WebSocket handler — Relays mouse/keyboard/IME input to Playwright pages.
Route: /ws/tab/{browser_id}/{tab_id}

滚动: fire-and-forget CDP — 不等往返，不节流。
"""
import json
import asyncio
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from server.services.browser_manager import browser_manager
from server.services.workbench_runtime import (
    WorkbenchRuntimeError,
    set_active_tab,
    workbench_allowed_tabs,
)

router = APIRouter()
_workbench_clients: dict[int, set[WebSocket]] = {}
_workbench_controllers: dict[int, WebSocket] = {}
_workbench_client_ids: dict[WebSocket, str] = {}
_workbench_clients_lock = asyncio.Lock()


async def close_workbench_clients(workbench_id: int, message: str = "Workbench deleted"):
    """Notify and close all open control sockets for a deleted workbench."""
    async with _workbench_clients_lock:
        clients = list(_workbench_clients.pop(workbench_id, set()))
        controller = _workbench_controllers.pop(workbench_id, None)
        for websocket in clients:
            _workbench_client_ids.pop(websocket, None)
        if controller is not None:
            _workbench_client_ids.pop(controller, None)

    payload = json.dumps({
        "type": "workbench_deleted",
        "workbench_id": workbench_id,
        "message": message,
    })
    for websocket in clients:
        try:
            await websocket.send_text(payload)
        except Exception:
            pass
        try:
            await websocket.close(code=4003, reason="Workbench deleted")
        except Exception:
            pass


async def notify_workbench_clients(workbench_id: int, event: dict):
    """Notify all open control sockets for a workbench without closing them."""
    async with _workbench_clients_lock:
        clients = list(_workbench_clients.get(workbench_id, set()))

    if not clients:
        return
    payload = json.dumps(event)
    for websocket in clients:
        try:
            await websocket.send_text(payload)
        except Exception:
            pass


async def _send_workbench_control_state(workbench_id: int):
    async with _workbench_clients_lock:
        clients = list(_workbench_clients.get(workbench_id, set()))
        controller = _workbench_controllers.get(workbench_id)
        controller_id = _workbench_client_ids.get(controller) if controller else None
        states = [
            (
                websocket,
                {
                    "type": "control_state",
                    "workbench_id": workbench_id,
                    "client_id": _workbench_client_ids.get(websocket),
                    "controller_id": controller_id,
                    "is_controller": websocket is controller,
                },
            )
            for websocket in clients
        ]

    for websocket, payload in states:
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass


async def _take_workbench_control(workbench_id: int, websocket: WebSocket, notify_old: bool = True):
    old_controller = None
    async with _workbench_clients_lock:
        old_controller = _workbench_controllers.get(workbench_id)
        _workbench_controllers[workbench_id] = websocket
        new_id = _workbench_client_ids.get(websocket)
    if notify_old and old_controller is not None and old_controller is not websocket:
        try:
            await old_controller.send_text(json.dumps({
                "type": "control_taken",
                "workbench_id": workbench_id,
                "by": new_id,
                "message": "控制权已被其他用户接管",
            }))
        except Exception:
            pass
    await _send_workbench_control_state(workbench_id)


async def _is_workbench_controller(workbench_id: int | None, websocket: WebSocket) -> bool:
    if workbench_id is None:
        return True
    async with _workbench_clients_lock:
        return _workbench_controllers.get(workbench_id) is websocket


async def _reject_non_controller(websocket: WebSocket, workbench_id: int | None) -> bool:
    if await _is_workbench_controller(workbench_id, websocket):
        return False
    await websocket.send_text(json.dumps({
        "type": "control_required",
        "workbench_id": workbench_id,
        "message": "请先获取控制权",
    }))
    return True


@router.websocket("/ws/tab/{browser_id}/{tab_id}")
async def ws_input(websocket: WebSocket, browser_id: int, tab_id: int):
    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab or tab.page.is_closed():
        await websocket.close(code=4004, reason="Tab not found")
        return

    await websocket.accept()
    browser_manager.add_viewer(browser_id, tab_id, websocket)
    await _input_loop(websocket, tab, allow_switch=None)


@router.websocket("/ws/workbench/{workbench_id}")
async def ws_workbench_input(websocket: WebSocket, workbench_id: int):
    try:
        browser_id = int(websocket.query_params.get("browser_id", ""))
        tab_id = int(websocket.query_params.get("tab_id", ""))
    except Exception:
        await websocket.close(code=4000, reason="Invalid initial tab")
        return

    allowed_tabs = await workbench_allowed_tabs(workbench_id)
    if (browser_id, tab_id) not in allowed_tabs:
        await websocket.close(code=4003, reason="Tab is not in workbench")
        return

    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab or tab.page.is_closed():
        await websocket.close(code=4004, reason="Tab not found")
        return

    await websocket.accept()
    client_id = uuid.uuid4().hex[:10]
    became_controller = False
    async with _workbench_clients_lock:
        _workbench_clients.setdefault(workbench_id, set()).add(websocket)
        _workbench_client_ids[websocket] = client_id
        if workbench_id not in _workbench_controllers:
            _workbench_controllers[workbench_id] = websocket
            became_controller = True
    await _send_workbench_control_state(workbench_id)
    if became_controller:
        await websocket.send_text(json.dumps({
            "type": "control_granted",
            "workbench_id": workbench_id,
            "message": "已获得工作台控制权",
        }))
    try:
        browser_manager.add_viewer(browser_id, tab_id, websocket)
        await _input_loop(websocket, tab, allow_switch=allowed_tabs, workbench_id=workbench_id)
    finally:
        controller_changed = False
        async with _workbench_clients_lock:
            clients = _workbench_clients.get(workbench_id)
            if clients is not None:
                clients.discard(websocket)
                if not clients:
                    _workbench_clients.pop(workbench_id, None)
            _workbench_client_ids.pop(websocket, None)
            if _workbench_controllers.get(workbench_id) is websocket:
                _workbench_controllers.pop(workbench_id, None)
                controller_changed = True
                remaining = list(_workbench_clients.get(workbench_id, set()))
                if remaining:
                    _workbench_controllers[workbench_id] = remaining[0]
            if not _workbench_clients.get(workbench_id):
                _workbench_controllers.pop(workbench_id, None)
        if controller_changed:
            await _send_workbench_control_state(workbench_id)


async def _input_loop(
    websocket: WebSocket,
    initial_tab,
    allow_switch: set[tuple[int, int]] | None = None,
    workbench_id: int | None = None,
):
    tab = initial_tab

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")
            page = tab.page

            # Track the controller that sent input (for popup routing).
            if t != "request_control" and await _is_workbench_controller(workbench_id, websocket):
                tab.last_input_ws = websocket

            try:
                if t == "request_control":
                    if workbench_id is None:
                        continue
                    await _take_workbench_control(workbench_id, websocket)
                    await websocket.send_text(json.dumps({
                        "type": "control_granted",
                        "workbench_id": workbench_id,
                        "message": "已接管工作台控制权",
                    }))

                elif t == "switch_tab":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    if allow_switch is None:
                        await websocket.send_text(json.dumps({
                            "type": "switch_tab_error",
                            "message": "Switching is not supported on this socket",
                        }))
                        continue
                    if workbench_id is not None:
                        allow_switch = await workbench_allowed_tabs(workbench_id)
                        if not allow_switch:
                            await websocket.send_text(json.dumps({
                                "type": "workbench_deleted",
                                "workbench_id": workbench_id,
                                "message": "工作台已删除",
                            }))
                            await websocket.close(code=4003, reason="Workbench deleted")
                            return
                    new_browser_id = int(msg.get("browser_id"))
                    new_tab_id = int(msg.get("tab_id"))
                    if (new_browser_id, new_tab_id) not in allow_switch:
                        await websocket.send_text(json.dumps({
                            "type": "switch_tab_error",
                            "message": "Tab is not in this workbench",
                        }))
                        continue
                    new_tab = browser_manager.find_tab(new_browser_id, new_tab_id)
                    if not new_tab or new_tab.page.is_closed():
                        await websocket.send_text(json.dumps({
                            "type": "switch_tab_error",
                            "message": "Target tab not found",
                        }))
                        continue
                    browser_manager.move_viewer(
                        tab.browser_id,
                        tab.tab_id,
                        new_browser_id,
                        new_tab_id,
                        websocket,
                    )
                    tab = new_tab
                    tab.last_input_ws = websocket
                    if workbench_id is not None:
                        try:
                            await set_active_tab(workbench_id, new_browser_id, new_tab_id)
                        except WorkbenchRuntimeError as exc:
                            await websocket.send_text(json.dumps({
                                "type": "switch_tab_error",
                                "message": exc.detail,
                            }))
                            continue
                        await notify_workbench_clients(workbench_id, {
                            "type": "active_tab_changed",
                            "workbench_id": workbench_id,
                            "browser_id": new_browser_id,
                            "tab_id": new_tab_id,
                            "room": f"{new_browser_id}_{new_tab_id}",
                        })
                    await websocket.send_text(json.dumps({
                        "type": "switch_tab_done",
                        "browser_id": new_browser_id,
                        "tab_id": new_tab_id,
                    }))

                elif t == "mouse":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    btn = msg.get("button", "none")
                    pw_btn = "left" if btn == "left" else "middle" if btn == "middle" else "right"
                    x, y = msg.get("x", 0), msg.get("y", 0)

                    if msg.get("eventType") == "mouseMoved":
                        await page.mouse.move(x, y)
                    elif msg.get("eventType") == "mousePressed":
                        await page.mouse.move(x, y)
                        await page.mouse.down(button=pw_btn if btn != "none" else "left")
                    elif msg.get("eventType") == "mouseReleased":
                        await page.mouse.move(x, y)
                        await page.mouse.up(button=pw_btn if btn != "none" else "left")
                    
                elif t == "scroll":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    # Fire-and-forget: 不等 CDP 往返，立即处理下一条消息
                    asyncio.create_task(_scroll_fire(page, msg.get("deltaX", 0), msg.get("deltaY", 0)))
                
                elif t == "keyboard":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    key = msg.get("key", "")
                    if key:
                        if msg.get("eventType") == "keyDown":
                            await page.keyboard.down(key)
                        else:
                            await page.keyboard.up(key)
                        
                elif t == "insertText":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    text = msg.get("text", "")
                    if text:
                        await page.keyboard.insert_text(text)

                elif t == "paste-file":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    import base64
                    file_data = msg.get("data", "")
                    file_name = msg.get("name", "clipboard.png")
                    file_mime = msg.get("mime", "image/png")
                    if file_data and len(file_data) < 7_000_000:  # ~5MB base64
                        await page.evaluate("""([b64, name, mime]) => {
                            const byteChars = atob(b64);
                            const byteArray = new Uint8Array(byteChars.length);
                            for (let i = 0; i < byteChars.length; i++) {
                                byteArray[i] = byteChars.charCodeAt(i);
                            }
                            const file = new File([byteArray], name, { type: mime });
                            const dt = new DataTransfer();
                            dt.items.add(file);
                            const evt = new ClipboardEvent('paste', {
                                bubbles: true, cancelable: true, clipboardData: dt
                            });
                            (document.activeElement || document.body).dispatchEvent(evt);
                        }""", [file_data, file_name, file_mime])
                        print(f"[ws:{tab.browser_id}/{tab.tab_id}] paste-file: {file_name} ({file_mime})")

                elif t == "translate":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    restore = msg.get("restore", False)
                    target_lang = msg.get("lang", "zh-CN")
                    try:
                        if restore:
                            await page.evaluate("""() => {
                                if (!window._origTexts) return;
                                for (const [id, text] of window._origTexts) {
                                    const el = document.querySelector(`[data-t-id="${id}"]`);
                                    if (el && el.firstChild) el.firstChild.textContent = text;
                                }
                                window._origTexts = null;
                            }""")
                            print(f"[ws:{tab.browser_id}/{tab.tab_id}] translate restored")
                        else:
                            await page.evaluate("""async (targetLang) => {
                                const walker = document.createTreeWalker(
                                    document.body, NodeFilter.SHOW_TEXT,
                                    { acceptNode: n => n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP }
                                );
                                const nodes = [];
                                while (walker.nextNode()) {
                                    const n = walker.currentNode;
                                    if (n.parentElement && !['SCRIPT','STYLE','NOSCRIPT'].includes(n.parentElement.tagName)) {
                                        nodes.push(n);
                                    }
                                }
                                // Save originals
                                window._origTexts = [];
                                nodes.forEach((n, i) => {
                                    const id = '_t_' + i;
                                    n.parentElement.setAttribute('data-t-id', id);
                                    window._origTexts.push([id, n.textContent]);
                                });
                                // Batch translate
                                for (let i = 0; i < nodes.length; i += 100) {
                                    const batch = nodes.slice(i, i + 100);
                                    const texts = batch.map(n => n.textContent.trim()).filter(Boolean);
                                    if (!texts.length) continue;
                                    const q = texts.map(t => `q=${encodeURIComponent(t)}`).join('&');
                                    try {
                                        const resp = await fetch(
                                            `https://translate.googleapis.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl=${targetLang}&${q}`
                                        );
                                        const results = await resp.json();
                                        const translations = Array.isArray(results[0]) ? results.map(r => r[0]) : [results[0]];
                                        let ti = 0;
                                        for (const node of batch) {
                                            if (node.textContent.trim() && ti < translations.length) {
                                                node.textContent = translations[ti++];
                                            }
                                        }
                                    } catch(e) { console.warn('Translate batch error:', e); }
                                }
                            }""", target_lang)
                            print(f"[ws:{tab.browser_id}/{tab.tab_id}] translate → {target_lang}")
                    except Exception as e:
                        print(f"[ws:{tab.browser_id}/{tab.tab_id}] translate error: {e}")

                elif t == "viewport":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    w = msg.get("width", 1280)
                    h = msg.get("height", 720)
                    w = max(320, min(3840, int(w)))
                    h = max(240, min(2160, int(h)))
                    await page.set_viewport_size({"width": w, "height": h})
                    await browser_manager.update_persistent_tab_viewport(tab.browser_id, tab.tab_id, w, h)
                    if tab.publisher:
                        await tab.publisher.refresh_stream()

                elif t == "navigate":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    url = msg.get("url", "")
                    browser = browser_manager.get_browser(tab.browser_id)
                    if not browser:
                        raise KeyError(f"Browser {tab.browser_id} not found")
                    result = await browser.navigate_tab(tab.tab_id, url)
                    await browser_manager.update_persistent_tab_url(tab.browser_id, tab.tab_id, result["url"])
                    await websocket.send_text(json.dumps({"type": "nav_done", **result}))

                elif t == "back":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    browser = browser_manager.get_browser(tab.browser_id)
                    if not browser:
                        raise KeyError(f"Browser {tab.browser_id} not found")
                    result = await browser.go_back(tab.tab_id)
                    await browser_manager.update_persistent_tab_url(tab.browser_id, tab.tab_id, result["url"])
                    await websocket.send_text(json.dumps({"type": "nav_done", **result}))

                elif t == "forward":
                    if await _reject_non_controller(websocket, workbench_id):
                        continue
                    browser = browser_manager.get_browser(tab.browser_id)
                    if not browser:
                        raise KeyError(f"Browser {tab.browser_id} not found")
                    result = await browser.go_forward(tab.tab_id)
                    await browser_manager.update_persistent_tab_url(tab.browser_id, tab.tab_id, result["url"])
                    await websocket.send_text(json.dumps({"type": "nav_done", **result}))
            except Exception as e:
                print(f"[ws:{tab.browser_id}/{tab.tab_id}] input error ({t}): {e}")
                    
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"WebSocket Error: {e}")
    finally:
        browser_manager.remove_viewer(tab.browser_id, tab.tab_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass


async def _scroll_fire(page, dx, dy):
    """Fire-and-forget scroll — 不阻塞 WS 消息循环"""
    try:
        await page.mouse.wheel(dx, dy)
    except Exception:
        pass
