"""
WebSocket handler — Relays mouse/keyboard/IME input to Playwright pages.
Route: /ws/tab/{browser_id}/{tab_id}

滚动优化: fire-and-forget CDP 派发，不等往返。
"""
import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from server.services.browser_manager import browser_manager

router = APIRouter()


@router.websocket("/ws/tab/{browser_id}/{tab_id}")
async def ws_input(websocket: WebSocket, browser_id: int, tab_id: int):
    tab = browser_manager.find_tab(browser_id, tab_id)
    if not tab or tab.page.is_closed():
        await websocket.close(code=4004, reason="Tab not found")
        return

    await websocket.accept()
    browser_manager.add_viewer(browser_id, tab_id, websocket)
    page = tab.page

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            # Track which viewer sent input (for popup routing)
            tab.last_input_ws = websocket

            try:
                if t == "mouse":
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
                    # Fire-and-forget: 不等 CDP 往返
                    asyncio.create_task(_scroll_fire(page, msg.get("deltaX", 0), msg.get("deltaY", 0)))
                    # 滚动时切换编码器为 motion 模式（保帧率），停止后恢复 detail（保清晰度）
                    asyncio.create_task(_set_content_hint(page, "motion"))
                    _schedule_detail_revert(page)
                
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

                elif t == "viewport":
                    w = msg.get("width", 1280)
                    h = msg.get("height", 720)
                    w = max(320, min(3840, int(w)))
                    h = max(240, min(2160, int(h)))
                    await page.set_viewport_size({"width": w, "height": h})
                    if tab.publisher:
                        await tab.publisher.refresh_stream()
            except Exception as e:
                print(f"[ws:{browser_id}/{tab_id}] input error ({t}): {e}")
                    
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"WebSocket Error: {e}")
    finally:
        browser_manager.remove_viewer(browser_id, tab_id, websocket)
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


async def _set_content_hint(page, hint):
    """切换 contentHint: 'motion'(保帧率) / 'detail'(保清晰度)"""
    try:
        await page.evaluate(f"window.__setContentHint && window.__setContentHint('{hint}')")
    except Exception:
        pass


# 用于延迟恢复 detail 模式的定时器
_revert_tasks: dict = {}

def _schedule_detail_revert(page):
    """滚动停止 300ms 后恢复 detail 模式"""
    page_id = id(page)
    # 取消上一次的定时器
    if page_id in _revert_tasks and not _revert_tasks[page_id].done():
        _revert_tasks[page_id].cancel()
    _revert_tasks[page_id] = asyncio.create_task(_delayed_revert(page))

async def _delayed_revert(page):
    try:
        await asyncio.sleep(0.3)
        await _set_content_hint(page, "detail")
    except asyncio.CancelledError:
        pass
