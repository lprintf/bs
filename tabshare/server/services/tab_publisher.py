import asyncio
from playwright.async_api import Page

class TabPublisher:
    """
    Captures a Playwright Page's screen via getDisplayMedia and publishes
    it to the pion SFU using native WebRTC. Sends navigation events to
    viewers via tab_manager broadcast.
    """
    def __init__(self, page: Page, tab_id: str):
        self.page = page
        self.tab_id = tab_id
        self._closed = False
        self._nav_task: asyncio.Task | None = None

    async def start(self):
        print(f"TabPublisher[{self.tab_id[-8:]}]: starting...")
        self.page.on("framenavigated", self._on_navigated)
        await self._ensure_stream()

    def _on_navigated(self, frame):
        if frame == self.page.main_frame:
            print(f"TabPublisher[{self.tab_id[-8:]}]: page navigated")
            if self._nav_task and not self._nav_task.done():
                self._nav_task.cancel()
            self._nav_task = asyncio.ensure_future(self._handle_navigation())

    async def _handle_navigation(self):
        if self._closed:
            return
        # Immediately notify viewers
        await self._broadcast({"type": "nav_start"})
        await asyncio.sleep(0.3)
        if self._closed:
            return
        for attempt in range(3):
            if self._closed:
                return
            print(f"TabPublisher[{self.tab_id[-8:]}]: re-establish attempt {attempt + 1}/3")
            if await self._ensure_stream():
                print(f"TabPublisher[{self.tab_id[-8:]}]: stream published successfully")
                await self._broadcast({"type": "nav_done"})
                return
            await asyncio.sleep(1)
        print(f"TabPublisher[{self.tab_id[-8:]}]: failed to re-establish after 3 attempts")

    async def _broadcast(self, event: dict):
        """Send event to all viewers of this tab."""
        from server.services.tab_manager import tab_manager
        await tab_manager.broadcast_to_viewers(self.tab_id, event)

    async def _ensure_stream(self) -> bool:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

        try:
            is_ready = await self.page.evaluate("() => !!(window._webrtc && window._webrtc.ready)")
            if is_ready:
                return True
        except Exception:
            pass

        try:
            sfu_url = "http://127.0.0.1:8088/offer"
            room = self.tab_id
            ready = await self.page.evaluate("""async ([sfuUrl, room]) => {
                if (window._webrtc && window._webrtc.ready) return "ready";
                window._webrtc = window._webrtc || {};

                // Close old PC if exists
                if (window._webrtc.pc) {
                    try { window._webrtc.pc.close(); } catch(e) {}
                }

                try {
                    const stream = await navigator.mediaDevices.getDisplayMedia({
                        audio: false,
                        video: { displaySurface: "browser" },
                        preferCurrentTab: true
                    });

                    const pc = new RTCPeerConnection({
                        iceServers: [],
                        iceTransportPolicy: "all"
                    });

                    stream.getVideoTracks().forEach(t => pc.addTrack(t, stream));

                    const offer = await pc.createOffer();
                    await pc.setLocalDescription(offer);

                    const res = await fetch(sfuUrl + "?room=" + room + "&role=pub", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ sdp: pc.localDescription.sdp })
                    });

                    if (!res.ok) throw new Error("SFU offer failed: " + res.status);
                    const answer = await res.json();
                    await pc.setRemoteDescription(new RTCSessionDescription({
                        type: answer.type,
                        sdp: answer.sdp
                    }));

                    window._webrtc.stream = stream;
                    window._webrtc.pc = pc;
                    window._webrtc.ready = true;

                    pc.oniceconnectionstatechange = () => {
                        console.log("[pub] ICE state:", pc.iceConnectionState);
                        if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                            window._webrtc.ready = false;
                        }
                    };

                    return "ready";
                } catch(e) {
                    return e.toString();
                }
            }""", [sfu_url, room])

            if ready == "ready":
                print(f"TabPublisher[{self.tab_id[-8:]}]: stream published to SFU")
                return True
            else:
                print(f"TabPublisher[{self.tab_id[-8:]}]: SFU publish failed: {ready}")
                return False
        except Exception as e:
            print(f"TabPublisher[{self.tab_id[-8:]}]: publish eval failed: {e}")
            return False

    def close(self):
        self._closed = True
        try:
            self.page.remove_listener("framenavigated", self._on_navigated)
        except Exception:
            pass
