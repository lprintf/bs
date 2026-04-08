import json
import asyncio
import traceback
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from playwright.async_api import Page


class WebRTCBridge:
    def __init__(self, page: Page, websocket: WebSocket, tab_id: str):
        self.page = page
        self.websocket = websocket
        self.tab_id = tab_id
        self.session_id = uuid.uuid4().hex[:8]  # unique per viewer
        self._closed = False
        self._nav_task: asyncio.Task | None = None

    async def _safe_send(self, data: dict) -> bool:
        if self._closed:
            return False
        try:
            await self.websocket.send_json(data)
            return True
        except Exception:
            self._closed = True
            return False

    async def start(self):
        print(f"WebRTCBridge[{self.session_id}]: starting...")

        self.page.on("framenavigated", self._on_navigated)

        if not await self._ensure_stream():
            return

        if not await self._safe_send({"type": "webrtc", "signal": {"ready": True}}):
            return

        await self._listen_to_ws()

    def _on_navigated(self, frame):
        if frame == self.page.main_frame:
            print(f"WebRTCBridge[{self.session_id}]: page navigated")
            # Immediately notify viewer to show loading state
            asyncio.ensure_future(self._safe_send({
                "type": "webrtc", "signal": {"navigating": True}
            }))
            # Debounce re-establishment
            if self._nav_task and not self._nav_task.done():
                self._nav_task.cancel()
            self._nav_task = asyncio.ensure_future(self._handle_navigation())

    async def _handle_navigation(self):
        if self._closed:
            return
        # Debounce: wait for navigation to settle
        await asyncio.sleep(1.5)
        if self._closed:
            return
        # Retry up to 3 times
        for attempt in range(3):
            if self._closed:
                return
            print(f"WebRTCBridge[{self.session_id}]: re-establish attempt {attempt + 1}/3")
            if await self._ensure_stream():
                print(f"WebRTCBridge[{self.session_id}]: stream ready, signaling viewer")
                await self._safe_send({"type": "webrtc", "signal": {"ready": True}})
                return
            await asyncio.sleep(1)
        print(f"WebRTCBridge[{self.session_id}]: failed to re-establish after 3 attempts")

    async def _ensure_stream(self) -> bool:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

        # If already ready, return immediately
        try:
            is_ready = await self.page.evaluate("() => !!(window._webrtc && window._webrtc.ready)")
            if is_ready:
                return True
        except Exception:
            pass

        from server.config import LIVEKIT_URL
        from server.services.livekit_helpers import generate_publisher_token

        try:
            # Inject SDK directly from the downloaded vendor file
            # Inside the container, this is mounted at /app/server/static/vendor/...
            await self.page.add_script_tag(path="server/static/vendor/livekit-client.umd.min.js")
        except Exception as e:
            print(f"WebRTCBridge[{self.session_id}]: failed to inject LiveKit SDK: {e}")
            return False

        token = generate_publisher_token(self.tab_id)

        try:
            # Proxy WebSocket to avoid Google CSP / Mixed Content
            proxy_url = "http://127.0.0.1:8085/livekit-proxy"
            ready = await self.page.evaluate("""async ([url, token]) => {
                if (window._webrtc && window._webrtc.ready) return "ready";
                window._webrtc = window._webrtc || {};
                
                try {
                    console.log("Getting display media...");
                    const stream = await navigator.mediaDevices.getDisplayMedia({
                        audio: false,
                        video: { displaySurface: "browser" },
                        preferCurrentTab: true
                    });
                    
                    console.log("Connecting to room...");
                    // The SDK exposes the global "LivekitClient" object
                    const room = new LivekitClient.Room();
                    await room.connect(url, token);
                    console.log("Room connected!");
                    
                    const track = stream.getVideoTracks()[0];
                    console.log("Publishing track...");
                    await room.localParticipant.publishTrack(track, {
                        name: "screen",
                        simulcast: false,
                        source: LivekitClient.Track.Source.ScreenShare
                    });
                    console.log("Track published!");
                    
                    window._webrtc.stream = stream;
                    window._webrtc.room = room;
                    window._webrtc.ready = true;
                    return "ready";
                } catch(e) {
                    console.error("Livekit Error:", e);
                    return e.toString();
                }
            }""", [proxy_url, token])
            
            if ready == "ready":
                return True
            else:
                print(f"WebRTCBridge[{self.session_id}]: SDK publish failed: {ready}")
                return False
        except Exception as e:
            print(f"WebRTCBridge[{self.session_id}]: LiveKit publish eval failed: {e}")
            return False

    # ─── Input Dispatch (via Playwright API) ───

    async def _handle_mouse(self, msg):
        try:
            x, y = msg.get("x", 0), msg.get("y", 0)
            btn = msg.get("button", "none")
            
            # map JS buttons to Playwright buttons
            pw_btn = "left" if btn == "left" else "middle" if btn == "middle" else "right"

            if msg.get("eventType") == "mouseMoved":
                await self.page.mouse.move(x, y)
            elif msg.get("eventType") == "mousePressed":
                await self.page.mouse.down(button=pw_btn if btn != "none" else "left")
            elif msg.get("eventType") == "mouseReleased":
                await self.page.mouse.up(button=pw_btn if btn != "none" else "left")
        except Exception as e:
            print(f"Mouse error ({msg}): {e}")

    async def _handle_scroll(self, msg):
        try:
            await self.page.mouse.wheel(msg.get("deltaX", 0), msg.get("deltaY", 0))
        except Exception:
            pass

    async def _handle_keyboard(self, msg):
        try:
            key = msg.get("key", "")
            if not key:
                return
            if msg.get("eventType") == "keyDown":
                await self.page.keyboard.down(key)
            else:
                await self.page.keyboard.up(key)
        except Exception as e:
            print(f"Keyboard error ({msg}): {e}")

    async def _handle_insert_text(self, msg):
        try:
            text = msg.get("text", "")
            if text:
                await self.page.keyboard.insert_text(text)
        except Exception as e:
            print(f"InsertText error ({msg}): {e}")

    # ─── WebSocket Message Loop ───

    async def _listen_to_ws(self):
        try:
            while not self._closed:
                try:
                    raw = await self.websocket.receive_text()
                except (WebSocketDisconnect, RuntimeError):
                    break

                msg = json.loads(raw)
                t = msg.get("type")

                if t == "mouse":
                    await self._handle_mouse(msg)
                elif t == "scroll":
                    await self._handle_scroll(msg)
                elif t == "keyboard":
                    await self._handle_keyboard(msg)
                elif t == "insertText":
                    await self._handle_insert_text(msg)

        except Exception as e:
            print(f"WebRTCBridge[{self.session_id}]: error: {e}")
            traceback.print_exc()
        finally:
            self._closed = True
            print(f"WebRTCBridge[{self.session_id}]: viewer disconnected")
            try:
                self.page.remove_listener("framenavigated", self._on_navigated)
            except Exception:
                pass
