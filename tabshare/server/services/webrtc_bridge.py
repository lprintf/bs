import json
import asyncio
import traceback
import uuid
from fastapi import WebSocket, WebSocketDisconnect
from playwright.async_api import Page


class WebRTCBridge:
    def __init__(self, page: Page, websocket: WebSocket):
        self.page = page
        self.websocket = websocket
        self.session_id = uuid.uuid4().hex[:8]  # unique per viewer
        self._cdp = None
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

        self._cdp = await self.page.context.new_cdp_session(self.page)
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

        try:
            await self.page.evaluate("""() => {
                if (window._webrtc && window._webrtc.ready && window._webrtc.stream) {
                    const tracks = window._webrtc.stream.getVideoTracks();
                    if (tracks.length > 0 && tracks[0].readyState === 'live') {
                        console.log("Reusing existing stream:", window._webrtc.stream.id);
                        return;
                    }
                    console.log("Stream dead, re-capturing...");
                }
                window._webrtc = window._webrtc || {};
                window._webrtc.stream = null;
                window._webrtc.ready = false;
                window._webrtc.pcs = window._webrtc.pcs || {};

                (async () => {
                    try {
                        const stream = await navigator.mediaDevices.getDisplayMedia({
                            audio: false,
                            video: { displaySurface: "browser" },
                            preferCurrentTab: true
                        });
                        console.log("getDisplayMedia success:", stream.id);
                        window._webrtc.stream = stream;
                        window._webrtc.ready = true;
                    } catch(e) {
                        console.error("getDisplayMedia failed:", e.name, e.message);
                    }
                })();
            }""")
        except Exception as e:
            print(f"WebRTCBridge[{self.session_id}]: capture eval failed: {e}")
            return False

        try:
            ready = await self.page.evaluate("""() => {
                return new Promise((resolve) => {
                    if (window._webrtc && window._webrtc.ready) { resolve("ready"); return; }
                    const check = setInterval(() => {
                        if (window._webrtc && window._webrtc.ready) { clearInterval(check); resolve("ready"); }
                    }, 50);
                    setTimeout(() => { clearInterval(check); resolve("timeout"); }, 5000);
                });
            }""")
        except Exception as e:
            print(f"WebRTCBridge[{self.session_id}]: wait eval failed: {e}")
            return False

        if ready != "ready":
            print(f"WebRTCBridge[{self.session_id}]: capture timeout")
            return False

        try:
            info = await self.page.evaluate("""() => {
                const t = window._webrtc.stream.getVideoTracks()[0];
                const s = t.getSettings();
                return JSON.stringify({w:s.width, h:s.height, fps:s.frameRate});
            }""")
            print(f"WebRTCBridge[{self.session_id}]: stream {info}")
        except Exception:
            pass

        return True

    async def _handle_offer(self, offer_sdp):
        sid = self.session_id
        try:
            result = await self.page.evaluate("""async ([offerSdp, sid]) => {
                if (!window._webrtc || !window._webrtc.stream) {
                    throw new Error("No stream available");
                }
                if (!window._webrtc.pcs) window._webrtc.pcs = {};

                // Close old PC for this session
                if (window._webrtc.pcs[sid]) {
                    try { window._webrtc.pcs[sid].close(); } catch(e) {}
                }

                const pc = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                window._webrtc.pcs[sid] = pc;

                window._webrtc.stream.getTracks().forEach(track => {
                    pc.addTrack(track, window._webrtc.stream);
                });

                const candidates = [];
                const gatheringComplete = new Promise((resolve) => {
                    pc.onicecandidate = (e) => {
                        if (e.candidate) candidates.push(e.candidate.toJSON());
                        else resolve();
                    };
                    setTimeout(resolve, 3000);
                });

                await pc.setRemoteDescription(new RTCSessionDescription(offerSdp));
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                await gatheringComplete;

                console.log("Answer created for session", sid, "ICE:", candidates.length);

                return {
                    answer: { type: pc.localDescription.type, sdp: pc.localDescription.sdp },
                    iceCandidates: candidates
                };
            }""", [offer_sdp, sid])
        except Exception as e:
            print(f"WebRTCBridge[{sid}]: offer eval failed: {e}")
            return

        if not await self._safe_send({
            "type": "webrtc", "signal": {"answer": result["answer"]}
        }):
            print(f"WebRTCBridge[{sid}]: viewer gone before answer sent")
            return
        for ice in result["iceCandidates"]:
            if not await self._safe_send({"type": "webrtc", "signal": {"ice": ice}}):
                return
        print(f"WebRTCBridge[{sid}]: sent answer + {len(result['iceCandidates'])} ICE")

    async def _handle_ice(self, ice_candidate):
        sid = self.session_id
        try:
            await self.page.evaluate("""async ([ice, sid]) => {
                if (window._webrtc && window._webrtc.pcs && window._webrtc.pcs[sid]) {
                    await window._webrtc.pcs[sid].addIceCandidate(new RTCIceCandidate(ice));
                }
            }""", [ice_candidate, sid])
        except Exception:
            pass

    # ─── Input Dispatch (via CDP) ───

    async def _handle_mouse(self, msg):
        try:
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": msg.get("eventType", "mouseMoved"),
                "x": msg.get("x", 0), "y": msg.get("y", 0),
                "button": msg.get("button", "none"),
                "clickCount": 1 if msg.get("eventType") == "mousePressed" else 0,
            })
        except Exception:
            pass

    async def _handle_scroll(self, msg):
        try:
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": msg.get("x", 0), "y": msg.get("y", 0),
                "deltaX": msg.get("deltaX", 0), "deltaY": msg.get("deltaY", 0),
            })
        except Exception:
            pass

    async def _handle_keyboard(self, msg):
        try:
            key = msg.get("key", "")
            params = {
                "type": "keyDown" if msg.get("eventType") == "keyDown" else "keyUp",
                "key": key, "code": msg.get("code", ""),
            }
            if len(key) == 1:
                params["text"] = key
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception:
            pass

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

                if t == "webrtc":
                    signal = msg.get("signal", {})
                    if "offer" in signal:
                        await self._handle_offer(signal["offer"])
                    elif "ice" in signal:
                        await self._handle_ice(signal["ice"])
                elif t == "mouse":
                    await self._handle_mouse(msg)
                elif t == "scroll":
                    await self._handle_scroll(msg)
                elif t == "keyboard":
                    await self._handle_keyboard(msg)

        except Exception as e:
            print(f"WebRTCBridge[{self.session_id}]: error: {e}")
            traceback.print_exc()
        finally:
            self._closed = True
            print(f"WebRTCBridge[{self.session_id}]: viewer disconnected")
            # Clean up session PC on page
            sid = self.session_id
            try:
                await self.page.evaluate("""(sid) => {
                    if (window._webrtc && window._webrtc.pcs && window._webrtc.pcs[sid]) {
                        try { window._webrtc.pcs[sid].close(); } catch(e) {}
                        delete window._webrtc.pcs[sid];
                    }
                }""", sid)
            except Exception:
                pass
            try:
                self.page.remove_listener("framenavigated", self._on_navigated)
            except Exception:
                pass
            try:
                await self._cdp.detach()
            except Exception:
                pass
