from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from server.services.tab_manager import tab_manager
import json

router = APIRouter()

@router.websocket("/ws/tab/{tab_id}")
async def websocket_endpoint(websocket: WebSocket, tab_id: str):
    await websocket.accept()
    
    page = tab_manager.get_tab(tab_id)
    if not page:
        await websocket.send_json({"error": "Tab not found"})
        await websocket.close()
        return

    # Register this viewer for server-push events
    tab_manager.add_viewer(tab_id, websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "mouse":
                btn = msg.get("button", "none")
                pw_btn = "left" if btn == "left" else "middle" if btn == "middle" else "right"
                x, y = msg.get("x", 0), msg.get("y", 0)

                if msg.get("eventType") == "mouseMoved":
                    await page.mouse.move(x, y)
                elif msg.get("eventType") == "mousePressed":
                    await page.mouse.down(button=pw_btn if btn != "none" else "left")
                elif msg.get("eventType") == "mouseReleased":
                    await page.mouse.up(button=pw_btn if btn != "none" else "left")
                    
            elif t == "scroll":
                await page.mouse.wheel(msg.get("deltaX", 0), msg.get("deltaY", 0))
                
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
                    
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"WebSocket Error: {e}")
    finally:
        tab_manager.remove_viewer(tab_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
