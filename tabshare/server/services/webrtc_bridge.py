import json
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from playwright.async_api import Page


class WebRTCBridge:
    def __init__(self, page: Page, websocket: WebSocket):
        self.page = page
        self.websocket = websocket
        self._cdp = None

    async def start(self):
        print("WebRTCBridge: starting...")

        # Get CDP session for input dispatch
        self._cdp = await self.page.context.new_cdp_session(self.page)

        # Step 1: Ensure stream is captured (only once per page)
        await self.page.evaluate("""() => {
            if (window._webrtc && window._webrtc.ready && window._webrtc.stream) {
                console.log("Reusing existing stream:", window._webrtc.stream.id);
                return;
            }
            window._webrtc = { stream: null, ready: false };

            (async () => {
                try {
                    console.log("Calling getDisplayMedia with preferCurrentTab...");
                    const stream = await navigator.mediaDevices.getDisplayMedia({
                        audio: false,
                        video: { displaySurface: "browser" },
                        preferCurrentTab: true
                    });
                    console.log("getDisplayMedia success, stream id:", stream.id);
                    window._webrtc.stream = stream;
                    window._webrtc.ready = true;
                } catch(e) {
                    console.error("getDisplayMedia failed:", e.name, e.message);
                }
            })();
        }""")

        # Step 2: Wait for stream to be ready
        ready = await self.page.evaluate("""() => {
            return new Promise((resolve) => {
                if (window._webrtc.ready) { resolve("ready"); return; }
                const check = setInterval(() => {
                    if (window._webrtc.ready) { clearInterval(check); resolve("ready"); }
                }, 50);
                setTimeout(() => { clearInterval(check); resolve("timeout"); }, 5000);
            });
        }""")

        if ready != "ready":
            print(f"WebRTCBridge: capture failed: {ready}")
            await self.websocket.send_json({"type": "error", "message": f"Capture failed: {ready}"})
            return

        track_info = await self.page.evaluate("""() => {
            const t = window._webrtc.stream.getVideoTracks()[0];
            const s = t.getSettings();
            return JSON.stringify({readyState:t.readyState, width:s.width, height:s.height, frameRate:s.frameRate});
        }""")
        print(f"WebRTCBridge: stream {track_info}")

        # Notify viewer
        await self.websocket.send_json({"type": "webrtc", "signal": {"ready": True}})

        # Listen for WS messages
        await self._listen_to_ws()

    async def _handle_offer(self, offer_sdp):
        """Create PeerConnection on page side, return answer + ICE candidates."""
        result = await self.page.evaluate("""async (offerSdp) => {
            const pc = new RTCPeerConnection();
            window._webrtc.pc = pc;

            window._webrtc.stream.getTracks().forEach(track => {
                pc.addTrack(track, window._webrtc.stream);
            });

            const candidates = [];
            const gatheringComplete = new Promise((resolve) => {
                pc.onicecandidate = (event) => {
                    if (event.candidate) candidates.push(event.candidate.toJSON());
                    else resolve();
                };
                setTimeout(resolve, 3000);
            });

            await pc.setRemoteDescription(new RTCSessionDescription(offerSdp));
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            await gatheringComplete;

            console.log("WebRTC answer created, ICE candidates:", candidates.length);

            return {
                answer: { type: pc.localDescription.type, sdp: pc.localDescription.sdp },
                iceCandidates: candidates
            };
        }""", offer_sdp)

        await self.websocket.send_json({
            "type": "webrtc",
            "signal": {"answer": result["answer"]}
        })
        for ice in result["iceCandidates"]:
            await self.websocket.send_json({
                "type": "webrtc",
                "signal": {"ice": ice}
            })
        print(f"WebRTCBridge: sent answer + {len(result['iceCandidates'])} ICE candidates")

    async def _handle_ice(self, ice_candidate):
        """Add viewer's ICE candidate to page's PeerConnection."""
        await self.page.evaluate("""async (ice) => {
            if (window._webrtc.pc) {
                await window._webrtc.pc.addIceCandidate(new RTCIceCandidate(ice));
            }
        }""", ice_candidate)

    # ─── Input Dispatch (via CDP) ───

    async def _handle_mouse(self, msg):
        event_type = msg.get("eventType", "mouseMoved")
        x, y = msg.get("x", 0), msg.get("y", 0)
        button = msg.get("button", "none")
        click_count = 1 if event_type == "mousePressed" else 0
        try:
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": x, "y": y,
                "button": button,
                "clickCount": click_count,
            })
        except Exception:
            pass

    async def _handle_scroll(self, msg):
        x, y = msg.get("x", 0), msg.get("y", 0)
        dx, dy = msg.get("deltaX", 0), msg.get("deltaY", 0)
        try:
            await self._cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": x, "y": y,
                "deltaX": dx, "deltaY": dy,
            })
        except Exception:
            pass

    async def _handle_keyboard(self, msg):
        event_type = msg.get("eventType", "keyDown")
        key = msg.get("key", "")
        code = msg.get("code", "")
        try:
            cdp_type = "keyDown" if event_type == "keyDown" else "keyUp"
            params = {"type": cdp_type, "key": key, "code": code}
            if len(key) == 1:
                params["text"] = key
            await self._cdp.send("Input.dispatchKeyEvent", params)
        except Exception:
            pass

    # ─── WebSocket Message Loop ───

    async def _listen_to_ws(self):
        try:
            while True:
                raw = await self.websocket.receive_text()
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "webrtc":
                    signal = msg.get("signal", {})
                    if "offer" in signal:
                        await self._handle_offer(signal["offer"])
                    elif "ice" in signal:
                        await self._handle_ice(signal["ice"])
                elif msg_type == "mouse":
                    await self._handle_mouse(msg)
                elif msg_type == "scroll":
                    await self._handle_scroll(msg)
                elif msg_type == "keyboard":
                    await self._handle_keyboard(msg)

        except WebSocketDisconnect:
            print("WebRTCBridge: viewer disconnected")
        except Exception as e:
            print(f"WebRTCBridge: error: {e}")
        finally:
            # Detach CDP session
            try:
                await self._cdp.detach()
            except Exception:
                pass
