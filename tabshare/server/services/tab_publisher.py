import asyncio
import traceback
from playwright.async_api import Page
from server.config import LIVEKIT_URL
from server.services.livekit_helpers import generate_publisher_token

class TabPublisher:
    """
    Manages capturing a Playwright Page's screen and publishing it
    to the LiveKit Server. Runs lifecycle independently of viewers.
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
            print(f"TabPublisher[{self.tab_id[-8:]}]: page navigated, preparing to reconnect stream")
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
            
        # Retry up to 3 times to inject and publish tracks
        for attempt in range(3):
            if self._closed:
                return
            print(f"TabPublisher[{self.tab_id[-8:]}]: re-establish attempt {attempt + 1}/3")
            if await self._ensure_stream():
                print(f"TabPublisher[{self.tab_id[-8:]}]: stream published successfully after navigation")
                return
            await asyncio.sleep(1)
            
        print(f"TabPublisher[{self.tab_id[-8:]}]: failed to re-establish stream after 3 attempts")

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

        try:
            # Inject SDK directly from the downloaded vendor file
            await self.page.add_script_tag(path="server/static/vendor/livekit-client.umd.min.js")
        except Exception as e:
            print(f"TabPublisher[{self.tab_id[-8:]}]: failed to inject LiveKit SDK: {e}")
            return False

        token = generate_publisher_token(self.tab_id)

        try:
            proxy_url = "http://127.0.0.1:8085/livekit-proxy"
            ready = await self.page.evaluate("""async ([url, token]) => {
                if (window._webrtc && window._webrtc.ready) return "ready";
                window._webrtc = window._webrtc || {};
                
                try {
                    const stream = await navigator.mediaDevices.getDisplayMedia({
                        audio: false,
                        video: { displaySurface: "browser" },
                        preferCurrentTab: true
                    });
                    
                    const room = new LivekitClient.Room();
                    await room.connect(url, token);
                    
                    const track = stream.getVideoTracks()[0];
                    await room.localParticipant.publishTrack(track, {
                        name: "screen",
                        simulcast: false,
                        source: LivekitClient.Track.Source.ScreenShare
                    });
                    
                    window._webrtc.stream = stream;
                    window._webrtc.room = room;
                    window._webrtc.ready = true;
                    return "ready";
                } catch(e) {
                    return e.toString();
                }
            }""", [proxy_url, token])
            
            if ready == "ready":
                print(f"TabPublisher[{self.tab_id[-8:]}]: LiveKit stream injected and published")
                return True
            else:
                print(f"TabPublisher[{self.tab_id[-8:]}]: SDK publish failed: {ready}")
                return False
        except Exception as e:
            print(f"TabPublisher[{self.tab_id[-8:]}]: LiveKit publish eval failed: {e}")
            return False

    def close(self):
        self._closed = True
        try:
            self.page.remove_listener("framenavigated", self._on_navigated)
        except Exception:
            pass
