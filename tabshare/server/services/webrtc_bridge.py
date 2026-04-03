import json
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from playwright.async_api import Page


class WebRTCBridge:
    def __init__(self, page: Page, websocket: WebSocket):
        self.page = page
        self.websocket = websocket

    async def start(self):
        print("WebRTCBridge: injecting getDisplayMedia capture + WebRTC into page...")

        # Step 1: Ensure stream is captured (only once per page)
        await self.page.evaluate("""() => {
            if (window._webrtc && window._webrtc.ready && window._webrtc.stream) {
                // Stream already exists and is ready, reuse it
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
        print("WebRTCBridge: waiting for getDisplayMedia to resolve...")
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

        # Diagnostic
        track_info = await self.page.evaluate("""() => {
            const t = window._webrtc.stream.getVideoTracks()[0];
            const s = t.getSettings();
            return JSON.stringify({label:t.label, readyState:t.readyState, enabled:t.enabled,
                muted:t.muted, width:s.width, height:s.height, frameRate:s.frameRate, displaySurface:s.displaySurface});
        }""")
        print(f"WebRTCBridge: captured track info: {track_info}")

        print("WebRTCBridge: stream ready, waiting for viewer offer...")

        # Notify viewer that stream is ready
        await self.websocket.send_json({"type": "webrtc", "signal": {"ready": True}})

        # Step 3: Listen for WS messages
        await self._listen_to_ws()

    async def _handle_offer(self, offer_sdp):
        """Forward offer to page, get answer + ICE candidates back directly via evaluate."""
        print("WebRTCBridge: received offer, creating answer in page...")

        # This evaluate creates the PC, handles the offer, waits for ICE gathering,
        # and returns everything in one shot. No expose_function needed.
        result = await self.page.evaluate("""async (offerSdp) => {
            const pc = new RTCPeerConnection();
            window._webrtc.pc = pc;

            // Add captured stream tracks
            window._webrtc.stream.getTracks().forEach(track => {
                pc.addTrack(track, window._webrtc.stream);
            });

            // Gather ICE candidates
            const candidates = [];
            const gatheringComplete = new Promise((resolve) => {
                pc.onicecandidate = (event) => {
                    if (event.candidate) {
                        candidates.push(event.candidate.toJSON());
                    } else {
                        resolve(); // null candidate = gathering done
                    }
                };
                // Safety timeout
                setTimeout(resolve, 3000);
            });

            await pc.setRemoteDescription(new RTCSessionDescription(offerSdp));
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);

            // Wait for ICE gathering
            await gatheringComplete;

            console.log("WebRTC answer created, ICE candidates:", candidates.length);

            return {
                answer: { type: pc.localDescription.type, sdp: pc.localDescription.sdp },
                iceCandidates: candidates
            };
        }""", offer_sdp)

        # Send answer to viewer
        await self.websocket.send_json({
            "type": "webrtc",
            "signal": {"answer": result["answer"]}
        })
        print(f"WebRTCBridge: sent answer + {len(result['iceCandidates'])} ICE candidates to viewer")

        # Send ICE candidates to viewer
        for ice in result["iceCandidates"]:
            await self.websocket.send_json({
                "type": "webrtc",
                "signal": {"ice": ice}
            })

    async def _handle_ice(self, ice_candidate):
        """Add viewer's ICE candidate to page's PeerConnection."""
        await self.page.evaluate("""async (ice) => {
            if (window._webrtc.pc) {
                await window._webrtc.pc.addIceCandidate(new RTCIceCandidate(ice));
            }
        }""", ice_candidate)

    async def _listen_to_ws(self):
        """Listen for viewer's WebRTC signals."""
        try:
            while True:
                raw = await self.websocket.receive_text()
                msg = json.loads(raw)

                if msg.get("type") == "webrtc":
                    signal = msg.get("signal", {})
                    if "offer" in signal:
                        await self._handle_offer(signal["offer"])
                    elif "ice" in signal:
                        await self._handle_ice(signal["ice"])
        except WebSocketDisconnect:
            print("WebRTCBridge: viewer disconnected")
        except Exception as e:
            print(f"WebRTCBridge: WS error: {e}")
