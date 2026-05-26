"""
WebSocket handler — Relays mouse/keyboard/IME input to Playwright pages.
Route: /ws/tab/{browser_id}/{tab_id}

滚动: fire-and-forget CDP — 不等往返，不节流。
"""
import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from server.services.browser_manager import browser_manager
from server.services.database import async_session, WorkbenchTabModel

router = APIRouter()


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

    allowed_tabs = await _workbench_allowed_tabs(workbench_id)
    if (browser_id, tab_id) not in allowed_tabs:
        await websocket.close(code=4003, reason="Tab is not in workbench")
        return

    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab or tab.page.is_closed():
        await websocket.close(code=4004, reason="Tab not found")
        return

    await websocket.accept()
    browser_manager.add_viewer(browser_id, tab_id, websocket)
    await _input_loop(websocket, tab, allow_switch=allowed_tabs)


async def _workbench_allowed_tabs(workbench_id: int) -> set[tuple[int, int]]:
    async with async_session() as session:
        result = await session.execute(
            select(WorkbenchTabModel).where(WorkbenchTabModel.workbench_id == workbench_id)
        )
        return {(row.browser_id, row.tab_id) for row in result.scalars().all()}


async def _input_loop(websocket: WebSocket, initial_tab, allow_switch: set[tuple[int, int]] | None = None):
    tab = initial_tab

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")
            page = tab.page

            # Track which viewer sent input (for popup routing)
            tab.last_input_ws = websocket

            try:
                if t == "switch_tab":
                    if allow_switch is None:
                        await websocket.send_text(json.dumps({
                            "type": "switch_tab_error",
                            "message": "Switching is not supported on this socket",
                        }))
                        continue
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
                    await websocket.send_text(json.dumps({
                        "type": "switch_tab_done",
                        "browser_id": new_browser_id,
                        "tab_id": new_tab_id,
                    }))

                elif t == "mouse":
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
                    # Fire-and-forget: 不等 CDP 往返，立即处理下一条消息
                    asyncio.create_task(_scroll_fire(page, msg.get("deltaX", 0), msg.get("deltaY", 0)))
                
                elif t == "keyboard":
                    key = msg.get("key", "")
                    if key:
                        if msg.get("eventType") == "keyDown":
                            await page.keyboard.down(key)
                        else:
                            await page.keyboard.up(key)
                        
                elif t == "insertText":
                    text = msg.get("text", "")
                    if text:
                        await page.keyboard.insert_text(text)

                elif t == "paste-file":
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
                    w = msg.get("width", 1280)
                    h = msg.get("height", 720)
                    w = max(320, min(3840, int(w)))
                    h = max(240, min(2160, int(h)))
                    await page.set_viewport_size({"width": w, "height": h})
                    await browser_manager.update_persistent_tab_viewport(tab.browser_id, tab.tab_id, w, h)
                    if tab.publisher:
                        await tab.publisher.refresh_stream()
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
