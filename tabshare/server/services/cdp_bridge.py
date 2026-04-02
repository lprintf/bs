import asyncio
import json
from playwright.async_api import CDPSession, Page
from fastapi import WebSocket, WebSocketDisconnect

class CDPBridge:
    def __init__(self, page: Page, websocket: WebSocket):
        self.page = page
        self.websocket = websocket
        self.cdp: CDPSession | None = None
        self.is_streaming = False

    async def initialize(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)
        
        # 监听 screencast 帧
        self.cdp.on("Page.screencastFrame", self._on_screencast_frame)

    async def start(self):
        await self.initialize()
        await self.resume_stream()
        
        try:
            while True:
                data = await self.websocket.receive_text()
                await self.handle_message(data)
        except WebSocketDisconnect:
            print("WebSocket disconnected")
        except Exception as e:
            print(f"WS error: {e}")
        finally:
            await self.cleanup()

    async def resume_stream(self):
        if not self.is_streaming and self.cdp:
            print("Resuming screencast...")
            # format 可选: 'jpeg', 'png', 'webp'
            await self.cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": 80,
                "everyNthFrame": 1
            })
            self.is_streaming = True

    async def suspend_stream(self):
        if self.is_streaming and self.cdp:
            print("Suspending screencast...")
            await self.cdp.send("Page.stopScreencast")
            self.is_streaming = False

    def _on_screencast_frame(self, event: dict):
        # 这里的事件是由 playwright event loop 同步调用的
        # 我们需要在后台任务中发送并 Ack，以免阻塞后续事件
        asyncio.create_task(self._process_frame(event))

    async def _process_frame(self, event: dict):
        session_id = event.get("sessionId")
        data = event.get("data")
        metadata = event.get("metadata")

        try:
            # 推送给前端
            message = {
                "type": "frame",
                "data": data,
                "metadata": metadata
            }
            await self.websocket.send_text(json.dumps(message))
            
            # Ack 确认，告诉浏览器可以发下一帧了
            if self.cdp:
                await self.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception as e:
            print(f"Error processing frame: {e}")

    async def handle_message(self, message: str):
        try:
            payload = json.loads(message)
            msg_type = payload.get("type")
            
            if msg_type == "suspend":
                await self.suspend_stream()
            elif msg_type == "resume":
                await self.resume_stream()
            elif msg_type == "mouse":
                await self._handle_mouse(payload)
            elif msg_type == "keyboard":
                await self._handle_keyboard(payload)
            elif msg_type == "scroll":
                await self._handle_scroll(payload)
            elif msg_type == "resize":
                # handle viewport sync if needed
                await self._handle_resize(payload)
            elif msg_type == "goto":
                url = payload.get("url")
                if url:
                    await self.page.goto(url)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"Error handling message: {e}")

    async def _handle_mouse(self, payload: dict):
        if not self.cdp: return
        event_type = payload.get("eventType") # mouseMoved, mousePressed, mouseReleased
        x = payload.get("x", 0)
        y = payload.get("y", 0)
        button = payload.get("button", "none") # none, left, middle, right, back, forward
        
        await self.cdp.send("Input.dispatchMouseEvent", {
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "clickCount": 1 if event_type in ["mousePressed", "mouseReleased"] else 0
        })

    async def _handle_keyboard(self, payload: dict):
        if not self.cdp: return
        event_type = payload.get("eventType") # keyDown, keyUp
        key = payload.get("key")
        code = payload.get("code")
        
        await self.cdp.send("Input.dispatchKeyEvent", {
            "type": event_type,
            "key": key,
            "windowsVirtualKeyCode": payload.get("keyCode", 0),
            "nativeVirtualKeyCode": payload.get("keyCode", 0),
            "unmodifiedText": key if len(key) == 1 else "",
            "text": key if len(key) == 1 else ""
        })

    async def _handle_scroll(self, payload: dict):
        if not self.cdp: return
        x = payload.get("x", 0)
        y = payload.get("y", 0)
        deltaX = payload.get("deltaX", 0)
        deltaY = payload.get("deltaY", 0)
        
        await self.cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": deltaX,
            "deltaY": deltaY
        })

    async def _handle_resize(self, payload: dict):
        width = payload.get("width")
        height = payload.get("height")
        if width and height:
            if self.page:
                await self.page.set_viewport_size({"width": width, "height": height})

    async def cleanup(self):
        if self.cdp:
            try:
                await self.cdp.detach()
            except Exception:
                pass
            self.cdp = None
