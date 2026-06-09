import asyncio
import httpx
from rebrowser_playwright.async_api import Page
from server.config import SFU_URL

class TabPublisher:
    """
    Captures a Playwright Page's screen via getDisplayMedia and publishes
    it to the pion SFU using native WebRTC. Sends navigation events to
    viewers via tab_manager broadcast.
    """
    def __init__(self, page: Page, room_name: str):
        self.page = page
        self.tab_id = room_name  # room_name = "{browser_id}_{tab_id}"
        self._closed = False
        self._paused = True  # 初始暂停，等 viewer 连接后再推流
        self._nav_task: asyncio.Task | None = None
        self._publish_task: asyncio.Task | None = None
        self._publish_lock = asyncio.Lock()  # 防止并发 publish

    @property
    def _browser_id(self) -> int:
        return int(self.tab_id.split('_')[0])

    @property
    def _tab_id(self) -> int:
        return int(self.tab_id.split('_')[1])

    async def start(self):
        """注册导航监听，但不立即推流（等待 viewer 连接）"""
        print(f"TabPublisher[{self.tab_id}]: registered (paused, waiting for viewers)")
        self.page.on("framenavigated", self._on_navigated)
        # 不立即推流，由 resume() 触发

    async def _retry_loop(self):
        """后台持续重试 publish，直到成功、关闭或暂停"""
        delay = 3
        while not self._closed and not self._paused:
            await asyncio.sleep(delay)
            if self._closed or self._paused:
                return
            print(f"TabPublisher[{self.tab_id}]: retry publish...")
            if await self._ensure_stream():
                print(f"TabPublisher[{self.tab_id}]: retry succeeded")
                return
            delay = min(delay + 2, 15)  # 逐步增大间隔，最大 15s

    async def resume(self) -> bool:
        """有 viewer 连接时恢复推流"""
        if self._closed:
            return False
        was_paused = self._paused
        self._paused = False
        if was_paused:
            print(f"TabPublisher[{self.tab_id}]: resuming (viewer connected)")
        if not await self._ensure_stream():
            asyncio.ensure_future(self._retry_loop())
            return False
        return True

    async def ensure_active(self, timeout_ms: int = 8000) -> bool:
        """Resume publishing and wait until the SFU has fresh RTP for this room."""
        if self._closed:
            return False

        quick_wait = max(250, min(1200, timeout_ms // 3))
        if await self.resume() and await self._wait_sfu_ready(quick_wait):
            return True

        # Browser-side WebRTC can occasionally report ready while the SFU room is stale.
        # Re-capture once so fast switching does not attach to an empty target room.
        print(f"TabPublisher[{self.tab_id}]: stream not fresh, forcing republish")
        self._cancel_publish()
        await self._clear_webrtc()
        self._paused = False
        if not await self._ensure_stream():
            asyncio.ensure_future(self._retry_loop())
            return False
        return await self._wait_sfu_ready(max(1000, timeout_ms - quick_wait))

    async def pause(self):
        """所有 viewer 断开后暂停推流，释放编码器资源"""
        if self._closed or self._paused:
            return
        self._paused = True
        self._cancel_publish()
        print(f"TabPublisher[{self.tab_id}]: pausing (no viewers)")
        await self._clear_webrtc()

    async def _clear_webrtc(self):
        """Close the page-side publisher PeerConnection and release the capture track."""
        try:
            await self.page.evaluate("""() => {
                if (window._webrtc) {
                    if (window._webrtc._statsInterval) {
                        try { clearInterval(window._webrtc._statsInterval); } catch(e) {}
                        window._webrtc._statsInterval = null;
                    }
                    if (window._webrtc.pc) { try { window._webrtc.pc.close(); } catch(e) {} }
                    if (window._webrtc.stream) {
                        window._webrtc.stream.getTracks().forEach(t => t.stop());
                    }
                    window._webrtc.ready = false;
                    window._webrtc.pc = null;
                    window._webrtc.stream = null;
                }
            }""")
        except Exception as e:
            print(f"TabPublisher[{self.tab_id}]: WebRTC cleanup error: {e}")

    def _on_navigated(self, frame):
        if frame == self.page.main_frame:
            print(f"TabPublisher[{self.tab_id}]: page navigated")
            if self._nav_task and not self._nav_task.done():
                self._nav_task.cancel()
            self._nav_task = asyncio.ensure_future(self._handle_navigation())

    async def _handle_navigation(self):
        if self._closed or self._paused:
            return
        self._cancel_publish()
        # Immediately notify viewers
        await self._broadcast({"type": "nav_start"})
        await asyncio.sleep(0.3)
        if self._closed:
            return
        for attempt in range(3):
            if self._closed:
                return
            print(f"TabPublisher[{self.tab_id}]: re-establish attempt {attempt + 1}/3")
            if await self._ensure_stream():
                print(f"TabPublisher[{self.tab_id}]: stream published successfully")
                await self._broadcast({"type": "nav_done", "url": self.page.url})
                # Request keyframe from SFU so viewers see new page immediately
                await self._request_pli()
                return
            await asyncio.sleep(1)
        print(f"TabPublisher[{self.tab_id}]: failed to re-establish after 3 attempts")

    async def _request_pli(self):
        """Ask SFU to send PLI to publisher for immediate keyframe."""
        try:
            async with httpx.AsyncClient() as client:
                # Use a dummy subscriber switch trick won't work; just re-publish triggers keyframe.
                # Send a direct PLI request - we'll add a /pli endpoint to SFU
                resp = await client.post(
                    f"{SFU_URL}/pli?room={self.tab_id}",
                    timeout=3.0,
                )
                if resp.status_code == 200:
                    print(f"TabPublisher[{self.tab_id}]: PLI requested")
        except Exception as e:
            print(f"TabPublisher[{self.tab_id}]: PLI request failed: {e}")

    async def _wait_sfu_ready(self, timeout_ms: int) -> bool:
        """Wait for the SFU room to have a fresh local RTP track."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{SFU_URL}/ready?room={self.tab_id}&timeout_ms={max(1, timeout_ms)}",
                    timeout=max(1.0, timeout_ms / 1000 + 1.0),
                )
            if resp.status_code == 200:
                return True
            # Backward compatibility while an older SFU binary is still running.
            if resp.status_code == 404 and "404 page not found" in resp.text:
                await asyncio.sleep(0.3)
                return True
            print(f"TabPublisher[{self.tab_id}]: SFU not ready: {resp.status_code} {resp.text.strip()}")
            return False
        except Exception as e:
            print(f"TabPublisher[{self.tab_id}]: SFU readiness check failed: {e}")
            return False

    async def _broadcast(self, event: dict):
        """Send event to all viewers of this tab."""
        from server.services.browser_manager import browser_manager
        await browser_manager.broadcast_to_viewers(self._browser_id, self._tab_id, event)

    async def _ensure_stream(self) -> bool:
        async with self._publish_lock:
            return await self._ensure_stream_inner()

    async def _ensure_stream_inner(self) -> bool:
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
            room = self.tab_id

            # Step 1: 在浏览器里获取 getDisplayMedia + createOffer (不涉及网络)
            # SDP munging: 根据配置强制使用指定 codec
            from server.config import VIDEO_CODEC
            codec_pref = VIDEO_CODEC  # h264, vp8, vp9, av1

            offer_sdp = await self.page.evaluate("""async (codecPref) => {
                window._webrtc = window._webrtc || {};

                // Close old PC if exists
                if (window._webrtc.pc) {
                    try { window._webrtc.pc.close(); } catch(e) {}
                }

                // SDP munging: keep only the specified codec family
                function forceCodec(sdp, targetCodec) {
                    const lines = sdp.split('\\r\\n');
                    const result = [];
                    let inVideo = false;
                    const keepPTs = new Set();
                    const removePTs = new Set();
                    const highPTs = new Set(); // H.264 High Profile PTs
                    const ptToFmtp = {};

                    // Map codec names
                    const codecNames = {
                        'h264': 'h264/',
                        'vp8': 'vp8/',
                        'vp9': 'vp9/',
                        'av1': 'av1/'
                    };
                    const target = codecNames[targetCodec] || 'h264/';

                    // First pass: categorize payload types
                    for (const line of lines) {
                        if (line.startsWith('m=video')) {
                            inVideo = true;
                        } else if (line.startsWith('m=') && !line.startsWith('m=video')) {
                            inVideo = false;
                        }
                        if (inVideo) {
                            const rtpMatch = line.match(/^a=rtpmap:(\\d+)\\s+(.+)/);
                            if (rtpMatch) {
                                const pt = rtpMatch[1];
                                const codec = rtpMatch[2].toLowerCase();
                                if (codec.startsWith(target)) {
                                    keepPTs.add(pt);
                                } else {
                                    removePTs.add(pt);
                                }
                            }
                            const fmtpMatch = line.match(/^a=fmtp:(\\d+)\\s+(.*)/);
                            if (fmtpMatch) {
                                ptToFmtp[fmtpMatch[1]] = fmtpMatch[2];
                            }
                        }
                    }

                    // H.264 specific: identify High Profile PTs for reordering
                    if (targetCodec === 'h264') {
                        for (const pt of keepPTs) {
                            const fmtp = ptToFmtp[pt] || '';
                            const plMatch = fmtp.match(/profile-level-id=([0-9a-fA-F]+)/);
                            if (plMatch && plMatch[1].startsWith('64')) {
                                highPTs.add(pt);
                            }
                        }
                    }

                    // If target codec not found, return original SDP (no munging)
                    if (keepPTs.size === 0) return sdp;

                    // Find associated RTX payload types to remove
                    for (const line of lines) {
                        const aptMatch = line.match(/^a=fmtp:(\\d+)\\s+apt=(\\d+)/);
                        if (aptMatch) {
                            const pt = aptMatch[1];
                            const apt = aptMatch[2];
                            if (removePTs.has(apt)) {
                                removePTs.add(pt);
                            }
                        }
                    }

                    // Second pass: filter and reorder
                    inVideo = false;
                    for (const line of lines) {
                        if (line.startsWith('m=video')) {
                            inVideo = true;
                            const parts = line.split(' ');
                            const filtered = parts.slice(3).filter(pt => !removePTs.has(pt));
                            // H.264: High Profile first
                            if (targetCodec === 'h264' && highPTs.size > 0) {
                                const high = filtered.filter(pt => highPTs.has(pt));
                                const rest = filtered.filter(pt => !highPTs.has(pt));
                                result.push(parts.slice(0, 3).concat(high, rest).join(' '));
                            } else {
                                result.push(parts.slice(0, 3).concat(filtered).join(' '));
                            }
                            continue;
                        } else if (line.startsWith('m=') && !line.startsWith('m=video')) {
                            inVideo = false;
                        }

                        if (inVideo) {
                            const ptMatch = line.match(/^a=(?:rtpmap|fmtp|rtcp-fb):(\\d+)/);
                            if (ptMatch && removePTs.has(ptMatch[1])) continue;
                        }
                        result.push(line);
                    }
                    return result.join('\\r\\n');
                }

                try {
                    const stream = await navigator.mediaDevices.getDisplayMedia({
                        audio: false,
                        video: {
                            displaySurface: "browser",
                            frameRate: { ideal: 60, max: 60 }
                        },
                        preferCurrentTab: true
                    });

                    // 告诉编码器这是屏幕内容（文字/细节始终优先）
                    stream.getVideoTracks().forEach(t => {
                        t.contentHint = 'detail';
                    });

                    const pc = new RTCPeerConnection({
                        iceServers: [],
                        iceTransportPolicy: "all"
                    });

                    stream.getVideoTracks().forEach(t => {
                        const sender = pc.addTrack(t, stream);
                        const params = sender.getParameters();
                        if (params.encodings && params.encodings.length > 0) {
                            params.encodings[0].maxBitrate = 60000000; // 60 Mbps
                            params.encodings[0].maxFramerate = 60;
                            sender.setParameters(params).catch(() => {});
                        }
                    });

                    const offer = await pc.createOffer();
                    // Munge SDP: keep only the configured codec
                    const mungedSdp = forceCodec(offer.sdp, codecPref);
                    await pc.setLocalDescription({type: 'offer', sdp: mungedSdp});

                    window._webrtc.stream = stream;
                    window._webrtc.pc = pc;

                    // 定期采集编码器质量诊断
                    window._webrtc._statsInterval = setInterval(async () => {
                        try {
                            const stats = await pc.getStats();
                            stats.forEach(r => {
                                if (r.type === 'outbound-rtp' && r.kind === 'video') {
                                    console.log('[encoder-stats]',
                                        'qlr=' + (r.qualityLimitationReason || 'none'),
                                        'targetBitrate=' + Math.round((r.targetBitrate||0)/1000) + 'kbps',
                                        'encoderImpl=' + (r.encoderImplementation || '?'),
                                        'fps=' + (r.framesPerSecond || 0),
                                        'keyFrames=' + (r.keyFramesEncoded || 0),
                                        'totalFrames=' + (r.framesEncoded || 0),
                                        'qpSum=' + (r.qpSum || 0)
                                    );
                                }
                            });
                        } catch(e) {}
                    }, 3000);

                    return pc.localDescription.sdp;
                } catch(e) {
                    return "ERROR:" + e.toString();
                }
            }""", codec_pref)

            if not offer_sdp or offer_sdp.startswith("ERROR:"):
                print(f"TabPublisher[{self.tab_id}]: getDisplayMedia failed: {offer_sdp}")
                return False

            # Step 2: Python 端直接请求 SFU（完全绕过浏览器代理）
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{SFU_URL}/offer?room={room}&role=pub",
                    json={"sdp": offer_sdp},
                    timeout=10.0,
                )
            if resp.status_code != 200:
                print(f"TabPublisher[{self.tab_id}]: SFU returned {resp.status_code}: {resp.text}")
                return False

            answer = resp.json()

            # Step 3: 把 SFU answer 设回浏览器的 PeerConnection
            result = await self.page.evaluate("""async (answer) => {
                try {
                    await window._webrtc.pc.setRemoteDescription(
                        new RTCSessionDescription({ type: answer.type, sdp: answer.sdp })
                    );
                    window._webrtc.ready = true;

                    window._webrtc.pc.oniceconnectionstatechange = () => {
                        console.log("[pub] ICE state:", window._webrtc.pc.iceConnectionState);
                        if (window._webrtc.pc.iceConnectionState === "failed" ||
                            window._webrtc.pc.iceConnectionState === "disconnected") {
                            window._webrtc.ready = false;
                        }
                    };
                    return "ready";
                } catch(e) {
                    return "ERROR:" + e.toString();
                }
            }""", answer)

            if result == "ready":
                print(f"TabPublisher[{self.tab_id}]: stream published to SFU")
                return True
            else:
                print(f"TabPublisher[{self.tab_id}]: setRemoteDescription failed: {result}")
                return False
        except Exception as e:
            print(f"TabPublisher[{self.tab_id}]: publish eval failed: {e}")
            return False

    def _cancel_publish(self):
        """取消正在进行的 publish task（防止并发踩踏）"""
        if self._publish_task and not self._publish_task.done():
            self._publish_task.cancel()
            self._publish_task = None

    async def refresh_stream(self):
        """Force re-capture stream (e.g. after viewport resize)."""
        if self._closed:
            return
        self._cancel_publish()
        print(f"TabPublisher[{self.tab_id}]: refreshing stream (viewport changed)")
        await self._clear_webrtc()
        if self._paused:
            return
        self._publish_task = asyncio.ensure_future(self._ensure_stream())

    def close(self):
        self._closed = True
        try:
            self.page.remove_listener("framenavigated", self._on_navigated)
        except Exception:
            pass
