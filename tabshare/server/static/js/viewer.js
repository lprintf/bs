(() => {
    const statusEl = document.getElementById('status');
    const video = document.getElementById('screen');
    const navOverlay = document.getElementById('nav-overlay');
    const imeTrap = document.getElementById('ime-trap');
    const pathParts = window.location.pathname.split('/');
    const pageMode = window.TABSHARE_VIEWER_MODE || (pathParts[1] === 'workbench' ? 'workbench' : 'viewer');
    const isWorkbench = pageMode === 'workbench';
    // Viewer URL: /viewer/{browserId}/{tabId}; Workbench URL: /workbench/{workbenchId}
    const initialWorkbenchId = isWorkbench ? parseInt(pathParts[pathParts.length - 1]) : null;
    const initialBrowserId = isWorkbench ? null : parseInt(pathParts[pathParts.length - 2]);
    const initialTabId = isWorkbench ? null : parseInt(pathParts[pathParts.length - 1]);
    let activeBrowserId = initialBrowserId;
    let activeTabId = initialTabId;
    let activeRoom = activeBrowserId !== null && activeTabId !== null ? `${activeBrowserId}_${activeTabId}` : '';
    const urlInput = document.getElementById('urlInput');
    const tabLabel = document.getElementById('tabLabel');
    const tabBar = document.getElementById('tabBar');
    let myTabs = [];

    if (isWorkbench) document.body.classList.add('workbench');

    // ── Toolbar Navigation ──
    async function navApi(path, body) {
        try {
            const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
            if (body) opts.body = JSON.stringify(body);
            const res = await fetch(path, opts);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showNotice(data.detail || data.error || 'Request failed');
            }
            return data;
        } catch (e) {
            console.error('Nav error:', e);
            showNotice('Request failed: ' + e.message);
        }
    }

    function navGo() {
        let url = urlInput.value.trim();
        if (!url) return;
        if (!url.match(/^https?:\/\//)) url = 'https://' + url;
        if (isWorkbench && !isUrlAllowedForActiveTab(url)) return;
        urlInput.value = url;
        navApi(`/api/admin/browsers/${activeBrowserId}/tabs/${activeTabId}/navigate`, { url });
    }

    async function navBack() {
        const result = await navApi(`/api/admin/browsers/${activeBrowserId}/tabs/${activeTabId}/back`);
        if (result && result.url) urlInput.value = result.url;
    }

    async function navForward() {
        const result = await navApi(`/api/admin/browsers/${activeBrowserId}/tabs/${activeTabId}/forward`);
        if (result && result.url) urlInput.value = result.url;
    }

    function navReload() {
        if (urlInput.value) {
            navApi(`/api/admin/browsers/${activeBrowserId}/tabs/${activeTabId}/navigate`, { url: urlInput.value });
        }
    }

    // Enter key in URL bar
    urlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); navGo(); }
        e.stopPropagation(); // Don't forward to page
    });
    // Prevent toolbar inputs from capturing keyboard events meant for the page
    urlInput.addEventListener('keyup', (e) => e.stopPropagation());

    let ws, pc;
    let sfuSubId = null; // SFU subscriber ID for fast track switching
    let displayedRoom = '';
    let _popupOverlayTimer = null;
    let statusFadeTimer = null;
    let pendingControlSwitch = null;
    let switchInProgress = false;
    let workbenchDeleted = false;
    let workbenchDeleteRedirectTimer = null;

    function setStatus(text, type) {
        statusEl.textContent = text;
        statusEl.className = `status-${type}`;
        statusEl.style.opacity = '1';
        clearTimeout(statusFadeTimer);
        if (type === 'connected') {
            statusFadeTimer = setTimeout(() => { statusEl.style.opacity = '0'; }, 3000);
        }
    }

    function showNavOverlay() {
        navOverlay.classList.add('visible');
    }

    function hideNavOverlay() {
        navOverlay.classList.remove('visible');
    }

    function activeTabMeta() {
        return myTabs.find(t => t.browser_id === activeBrowserId && t.tab_id === activeTabId);
    }

    function showNotice(message, type = 'error') {
        const toast = document.createElement('div');
        toast.className = 'dl-toast';
        toast.innerHTML = `
        <span>${message}</span>
        <span class="dl-close" onclick="this.parentElement.remove()">×</span>
    `;
        document.body.appendChild(toast);
        setTimeout(() => { if (toast.parentElement) toast.remove(); }, type === 'error' ? 6000 : 3500);
    }

    function isUrlAllowedForActiveTab(url) {
        const allowedHostname = activeTabMeta()?.allowed_hostname;
        if (!allowedHostname) return true;
        try {
            const hostname = new URL(url).hostname.toLowerCase();
            if (hostname === allowedHostname) return true;
        } catch (e) {
            showNotice('Invalid URL');
            return false;
        }
        showNotice(`工作台页面只能停留在 ${allowedHostname}`);
        return false;
    }

    async function connectSFU() {
        if (pc) { try { pc.close(); } catch (e) { } pc = null; }
        if (!activeRoom) return;
        const connectRoom = activeRoom;
        try {
            setStatus('Connecting to stream…', 'connecting');
            await prepareActiveStream(6000);

            pc = new RTCPeerConnection({
                iceServers: [],
                iceTransportPolicy: 'all'
            });

            // We only receive video, add a transceiver
            pc.addTransceiver('video', { direction: 'recvonly' });

            pc.ontrack = (ev) => {
                console.log('[viewer] got track:', ev.track.kind);
                if (ev.track.kind === 'video') {
                    video.srcObject = ev.streams[0] || new MediaStream([ev.track]);
                    displayedRoom = connectRoom;
                    setStatus('Connected', 'connected');
                    hideNavOverlay();
                    imeTrap.focus({ preventScroll: true });
                    // Re-register frame watch for new stream
                    if (typeof startFrameWatch === 'function') startFrameWatch();
                }
            };

            pc.oniceconnectionstatechange = () => {
                console.log('[viewer] ICE state:', pc.iceConnectionState);
                if (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected') {
                    setStatus('Disconnected', 'error');
                    showNavOverlay();
                    setTimeout(connectSFU, 2000);
                }
            };

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            const res = await fetch(`/api/sfu/offer?room=${activeRoom}&role=sub`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sdp: pc.localDescription.sdp })
            });

            if (!res.ok) {
                const text = await res.text();
                throw new Error(`SFU error: ${res.status} ${text}`);
            }

            const answer = await res.json();
            if (answer.sub_id) sfuSubId = answer.sub_id;
            await pc.setRemoteDescription(new RTCSessionDescription({
                type: answer.type,
                sdp: answer.sdp
            }));

            console.log('[viewer] SDP answer set, waiting for ICE+media');
        } catch (e) {
            console.error('SFU connect error:', e);
            setStatus('Waiting for publisher…', 'connecting');
            setTimeout(connectSFU, 3000);
        }
    }

    function connectWS(options = {}) {
        const connectStream = options.connectStream !== false;
        if (!activeRoom) {
            setStatus('No tab', 'error');
            return Promise.resolve(false);
        }
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = isWorkbench
            ? `${protocol}//${location.host}/ws/workbench/${initialWorkbenchId}?browser_id=${activeBrowserId}&tab_id=${activeTabId}`
            : `${protocol}//${location.host}/ws/tab/${activeBrowserId}/${activeTabId}`;
        ws = new WebSocket(wsUrl);

        return new Promise((resolve) => {
            let settled = false;
            const settle = (value) => {
                if (!settled) {
                    settled = true;
                    resolve(value);
                }
            };

            ws.onopen = () => {
                setStatus('Waiting for stream…', 'connecting');
                applyResolution();
                renderTabBar();
                fetchCurrentTabUrl();
                // Connect SFU after viewport is applied (give backend time to resize)
                if (connectStream) setTimeout(connectSFU, 300);
                settle(true);
            };

            ws.onmessage = async (ev) => {
                try {
                    const msg = JSON.parse(ev.data);
                    if (msg.type === 'workbench_deleted') {
                        workbenchDeleted = true;
                        if (pendingControlSwitch) {
                            pendingControlSwitch.reject(new Error(msg.message || 'Workbench deleted'));
                            pendingControlSwitch = null;
                        }
                        hideNavOverlay();
                        showNotice(msg.message || '工作台已删除');
                        setStatus('Workbench deleted', 'error');
                        if (!workbenchDeleteRedirectTimer) {
                            workbenchDeleteRedirectTimer = setTimeout(() => {
                                location.href = '/admin';
                            }, 1400);
                        }
                    } else if (msg.type === 'workbench_updated') {
                        if (isWorkbench) {
                            const currentKey = activeBrowserId !== null ? `${activeBrowserId}:${activeTabId}` : '';
                            if (currentKey) {
                                const params = new URLSearchParams(location.search);
                                params.set('active', currentKey);
                                history.replaceState(null, '', `/workbench/${initialWorkbenchId}?${params.toString()}`);
                            }
                            await loadWorkbenchTabs();
                            renderTabBar();
                            const nextKey = activeBrowserId !== null ? `${activeBrowserId}:${activeTabId}` : '';
                            showNotice(msg.message || '工作台已更新');
                            if (currentKey && nextKey && currentKey !== nextKey) {
                                switchTab(activeBrowserId, activeTabId);
                            }
                        }
                    } else if (msg.type === 'switch_tab_done') {
                        if (pendingControlSwitch) {
                            pendingControlSwitch.resolve(msg);
                            pendingControlSwitch = null;
                        }
                    } else if (msg.type === 'switch_tab_error') {
                        if (pendingControlSwitch) {
                            pendingControlSwitch.reject(new Error(msg.message || 'Control switch failed'));
                            pendingControlSwitch = null;
                        }
                        showNotice(msg.message || '工作台切换失败');
                    } else if (msg.type === 'nav_start') {
                        showNavOverlay();
                        setStatus('Page loading…', 'connecting');
                    } else if (msg.type === 'nav_done') {
                        if (msg.url) urlInput.value = msg.url;
                    } else if (msg.type === 'download_ready') {
                        showDownloadToast(msg);
                    } else if (msg.type === 'filechooser_opened') {
                        console.log('Remote file chooser opened, prompting local file picker');
                        // Show a toast hint and auto-trigger the file picker
                        const toast = document.createElement('div');
                        toast.className = 'dl-toast';
                        toast.innerHTML = `
                        📂 <span>Remote page requests a file. Select a file to upload.</span>
                        <span class="dl-close" onclick="this.parentElement.remove()">×</span>
                    `;
                        document.body.appendChild(toast);
                        setTimeout(() => { if (toast.parentElement) toast.remove(); }, 8000);
                        document.getElementById('fileUpload').click();
                    } else if (msg.type === 'popup_loading') {
                        // Show loading overlay while popup tab is being created
                        showNavOverlay();
                        setStatus('Opening new tab…', 'connecting');
                        if (_popupOverlayTimer) clearTimeout(_popupOverlayTimer);
                        _popupOverlayTimer = setTimeout(() => {
                            hideNavOverlay();
                            _popupOverlayTimer = null;
                        }, 9000);
                    } else if (msg.type === 'popup_tab_created') {
                        // New tab ready, switch to it
                        console.log('Popup created new tab:', msg.tab_id);
                        if (_popupOverlayTimer) clearTimeout(_popupOverlayTimer);
                        _popupOverlayTimer = null;
                        hideNavOverlay();
                        if (isWorkbench) {
                            showNotice(msg.error || '工作台不支持弹出新页面');
                        } else if (msg.tab_id >= 0) {
                            if (!myTabs.some(t => t.browser_id === activeBrowserId && t.tab_id === msg.tab_id)) {
                                myTabs.push({ browser_id: activeBrowserId, tab_id: msg.tab_id });
                            }
                            saveTabs();
                            switchTab(activeBrowserId, msg.tab_id);
                        }
                    } else if (msg.type === 'popup_blocked') {
                        hideNavOverlay();
                        showNotice(msg.message || '工作台不支持弹出新页面');
                    } else if (msg.type === 'navigation_blocked') {
                        hideNavOverlay();
                        if (msg.current_url) urlInput.value = msg.current_url;
                        showNotice(msg.message || '工作台页面不能跳转到其他域名');
                    }
                } catch (e) { }
            };

            ws.onclose = (ev) => {
                console.log('WebSocket closed');
                settle(false);
                if (workbenchDeleted || (isWorkbench && ev.code === 4003)) {
                    workbenchDeleted = true;
                    setStatus('Workbench unavailable', 'error');
                    if (!workbenchDeleteRedirectTimer) {
                        showNotice('工作台不存在或已删除');
                        workbenchDeleteRedirectTimer = setTimeout(() => {
                            location.href = '/admin';
                        }, 1400);
                    }
                    return;
                }
                setTimeout(() => connectWS({ connectStream }), 2000);
            };

            ws.onerror = () => {
                settle(false);
            };
        });

    }

    // ─── Resolution & Viewport ───
    const resSelect = document.getElementById('resSelect');
    const vpInfo = document.getElementById('vpInfo');
    let currentRes = { w: 1920, h: 1080 }; // default

    function sendViewport(w, h) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'viewport', width: w, height: h }));
        }
    }

    function applyResolution() {
        const val = resSelect.value;
        let w, h;
        if (val === 'auto') {
            const area = document.getElementById('videoArea');
            w = area.clientWidth;
            h = area.clientHeight;
        } else {
            [w, h] = val.split('x').map(Number);
        }
        currentRes = { w, h };
        sendViewport(w, h);
        updateVpInfo();
    }

    function updateVpInfo() {
        const vw = video.videoWidth || currentRes.w;
        const vh = video.videoHeight || currentRes.h;
        vpInfo.textContent = `${vw}×${vh}`;
    }

    resSelect.addEventListener('change', applyResolution);

    // On window resize, if auto mode, update viewport
    let _vpTimer = null;
    window.addEventListener('resize', () => {
        clearTimeout(_vpTimer);
        _vpTimer = setTimeout(() => {
            if (resSelect.value === 'auto') applyResolution();
        }, 500);
    });

    // Update info when video metadata loads
    video.addEventListener('loadedmetadata', updateVpInfo);
    video.addEventListener('resize', updateVpInfo);

    // ─── Scale Mode ───
    let currentScale = 'fit';
    function setScale(mode) {
        currentScale = mode;
        video.className = `scale-${mode}`;
        document.getElementById('scaleFit').classList.toggle('active', mode === 'fit');
        document.getElementById('scaleFill').classList.toggle('active', mode === 'fill');
        document.getElementById('scaleReal').classList.toggle('active', mode === 'real');
    }

    // ─── Fullscreen ───
    function toggleFullscreen() {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(() => { });
        } else {
            document.exitFullscreen();
        }
    }

    // ─── Input Mapping ───

    function getRenderedVideoRect() {
        // Calculate the actual rendered rectangle of the video
        // accounting for object-fit: contain letterboxing
        const rect = video.getBoundingClientRect();
        const vw = video.videoWidth || 1;
        const vh = video.videoHeight || 1;

        if (currentScale === 'real') {
            // 1:1 mode: video is at native size, centered
            return { rx: rect.left, ry: rect.top, rw: rect.width, rh: rect.height, vw, vh };
        }

        // For fit and fill modes with object-fit: contain
        const containerAR = rect.width / rect.height;
        const videoAR = vw / vh;
        let rw, rh, rx, ry;

        if (videoAR > containerAR) {
            // Video is wider — letterboxed top/bottom
            rw = rect.width;
            rh = rect.width / videoAR;
            rx = rect.left;
            ry = rect.top + (rect.height - rh) / 2;
        } else {
            // Video is taller — pillarboxed left/right
            rh = rect.height;
            rw = rect.height * videoAR;
            rx = rect.left + (rect.width - rw) / 2;
            ry = rect.top;
        }
        return { rx, ry, rw, rh, vw, vh };
    }

    function mapCoords(e) {
        const { rx, ry, rw, rh, vw, vh } = getRenderedVideoRect();
        const x = ((e.clientX - rx) / rw) * vw;
        const y = ((e.clientY - ry) / rh) * vh;
        // Clamp to video bounds
        return {
            x: Math.max(0, Math.min(vw, Math.round(x))),
            y: Math.max(0, Math.min(vh, Math.round(y)))
        };
    }

    function buttonName(b) {
        return b === 0 ? 'left' : b === 1 ? 'middle' : b === 2 ? 'right' : 'none';
    }

    function sendInput(data) {
        if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(data));
        }
    }

    // ─── Mouse Events (on Video) ───

    video.addEventListener('mousemove', (e) => {
        e.preventDefault();
        const { x, y } = mapCoords(e);
        sendInput({ type: 'mouse', eventType: 'mouseMoved', x, y, button: 'none' });
    });
    // Helper to check if an element is part of any toolbar/tab-bar
    function isToolbarElement(el) {
        return document.getElementById('toolbar').contains(el)
            || document.getElementById('toolbar-sub').contains(el)
            || document.getElementById('tabBar').contains(el);
    }

    video.addEventListener('mousedown', (e) => {
        e.preventDefault();
        // Only grab focus if not interacting with toolbar
        if (!isToolbarElement(document.activeElement)) {
            imeTrap.focus({ preventScroll: true });
        }
        const { x, y } = mapCoords(e);
        sendInput({ type: 'mouse', eventType: 'mousePressed', x, y, button: buttonName(e.button) });
    });

    video.addEventListener('mouseup', (e) => {
        e.preventDefault();
        const { x, y } = mapCoords(e);
        sendInput({ type: 'mouse', eventType: 'mouseReleased', x, y, button: buttonName(e.button) });
    });

    video.addEventListener('contextmenu', e => e.preventDefault());

    video.addEventListener('wheel', (e) => {
        e.preventDefault();
        const { x, y } = mapCoords(e);
        sendInput({ type: 'scroll', x, y, deltaX: e.deltaX, deltaY: e.deltaY });
    }, { passive: false });

    // ─── File Paste & Drop (images ≤5MB) ───

    const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

    async function sendFileViaWS(file) {
        if (!file || !file.type.startsWith('image/')) return;
        if (file.size > MAX_FILE_SIZE) {
            console.warn(`File too large: ${(file.size / 1024 / 1024).toFixed(1)}MB (max 5MB)`);
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            const base64 = reader.result.split(',')[1]; // strip data:...;base64,
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: 'paste-file',
                    name: file.name || 'clipboard.png',
                    mime: file.type,
                    data: base64,
                }));
                console.log(`Pasted: ${file.name || 'image'} (${(file.size / 1024).toFixed(0)}KB)`);
            }
        };
        reader.readAsDataURL(file);
    }

    // Paste: Ctrl+V with image in clipboard
    document.addEventListener('paste', (e) => {
        if (!e.clipboardData || !e.clipboardData.files.length) return;
        for (const file of e.clipboardData.files) {
            if (file.type.startsWith('image/')) {
                e.preventDefault();
                sendFileViaWS(file);
                return;
            }
        }
    });

    // Drop: drag image file onto viewer
    video.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        video.style.outline = '3px dashed #667eea';
        video.style.outlineOffset = '-3px';
    });
    video.addEventListener('dragleave', () => {
        video.style.outline = '';
        video.style.outlineOffset = '';
    });
    video.addEventListener('drop', (e) => {
        e.preventDefault();
        video.style.outline = '';
        video.style.outlineOffset = '';
        if (e.dataTransfer.files.length) {
            sendFileViaWS(e.dataTransfer.files[0]);
        }
    });

    // ─── Keyboard & IME Events (on Hidden Textarea) ───

    let isComposing = false;

    // Release all modifier keys on remote to prevent stuck keys
    // (IME toggle via Shift/Ctrl swallows the keyup event)
    function resetModifiers() {
        for (const mod of ['Shift', 'Control', 'Alt', 'Meta']) {
            sendInput({ type: 'keyboard', eventType: 'keyUp', key: mod, code: '' });
        }
    }

    imeTrap.addEventListener('compositionstart', () => {
        console.log("IME: compositionstart");
        isComposing = true;
        resetModifiers();
    });

    imeTrap.addEventListener('compositionend', (e) => {
        console.log("IME: compositionend", e.data);
        isComposing = false;
        if (e.data) {
            sendInput({ type: 'insertText', text: e.data });
        }
        // 延迟清空，防止立刻触发的 input 事件读取到残留或干扰浏览器状态
        setTimeout(() => { imeTrap.value = ''; }, 0);
    });

    imeTrap.addEventListener('input', (e) => {
        console.log("IME: input event", { isComposing, val: imeTrap.value, inputType: e.inputType });
        // 如果正在拼写拼音，绝对不能清空 value，否则打断输入法！
        if (isComposing) return;

        // 处理非输入法状态下的纯粘贴
        if (e.inputType === 'insertFromPaste') {
            sendInput({ type: 'insertText', text: imeTrap.value });
        }

        // 正常按键已经在 keydown 里发过了，这里直接清空垃圾即可
        setTimeout(() => { imeTrap.value = ''; }, 0);
    });

    imeTrap.addEventListener('keydown', (e) => {
        const ae = document.activeElement;
        if (ae !== imeTrap && isToolbarElement(ae)) return;
        console.log("IME: keydown", { key: e.key, code: e.code, isComposing, nativeComposing: e.isComposing });

        // Skip if actively composing (use both native and manual flags)
        if (e.isComposing || isComposing) return;
        // Skip unknown keys from IME (but NOT keyCode 229 — unreliable on fcitx/ibus)
        if (e.key === 'Process' || e.key === 'Unidentified') return;

        // Functional keys (length > 1): send immediately
        if (e.key.length > 1) {
            sendInput({ type: 'keyboard', eventType: 'keyDown', key: e.key, code: e.code });
            if (e.key === 'Backspace' || e.key === 'Tab') {
                e.preventDefault();
            }
            return;
        }

        // Printable characters: defer 1 frame to let compositionstart fire first
        // (macOS fires keydown BEFORE compositionstart)
        const key = e.key, code = e.code;
        requestAnimationFrame(() => {
            if (!isComposing) {
                sendInput({ type: 'keyboard', eventType: 'keyDown', key, code });
            }
        });
    });

    imeTrap.addEventListener('keyup', (e) => {
        if (e.isComposing || isComposing) return;
        if (e.key === 'Process' || e.key === 'Unidentified') return;

        if (e.key.length > 1) {
            sendInput({ type: 'keyboard', eventType: 'keyUp', key: e.key, code: e.code });
            return;
        }

        // Defer printable keyup to match deferred keydown
        const key = e.key, code = e.code;
        requestAnimationFrame(() => {
            if (!isComposing) {
                sendInput({ type: 'keyboard', eventType: 'keyUp', key, code });
            }
        });
    });

    // Ensure the trap stays focused (but not when toolbar is active)
    document.body.addEventListener('click', (e) => {
        if (!isToolbarElement(e.target)) {
            imeTrap.focus({ preventScroll: true });
        }
    });

    // ─── Session lifecycle ───
    function startSession() {
        if (ws) { ws.onclose = null; ws.close(); }
        if (pc) { pc.close(); pc = null; }
        sfuSubId = null;
        setStatus('Connecting…', 'connecting');
        showNavOverlay();
        connectWS();
        video.addEventListener('playing', startFrameWatch, { once: true });
    }

    // Frame watch: only hides overlay when video frames arrive
    function startFrameWatch() {
        if (!('requestVideoFrameCallback' in HTMLVideoElement.prototype)) return;
        function onFrame() {
            hideNavOverlay();
            setStatus('Connected', 'connected');
            video.requestVideoFrameCallback(onFrame);
        }
        video.requestVideoFrameCallback(onFrame);
    }
    video.addEventListener('playing', startFrameWatch, { once: true });

    function waitForVideoFrame(timeoutMs = 1200) {
        return new Promise((resolve) => {
            if (!('requestVideoFrameCallback' in HTMLVideoElement.prototype)) {
                setTimeout(() => resolve(false), Math.min(timeoutMs, 300));
                return;
            }
            let done = false;
            const timer = setTimeout(() => {
                if (!done) {
                    done = true;
                    resolve(false);
                }
            }, timeoutMs);
            video.requestVideoFrameCallback(() => {
                if (!done) {
                    done = true;
                    clearTimeout(timer);
                    resolve(true);
                }
            });
        });
    }

    async function readDecodedVideoStats() {
        if (!pc) return null;
        try {
            const stats = await pc.getStats();
            let best = null;
            stats.forEach(report => {
                if (report.type === 'inbound-rtp' && report.kind === 'video') {
                    best = {
                        framesDecoded: report.framesDecoded || 0,
                        bytesReceived: report.bytesReceived || 0,
                    };
                }
            });
            return best;
        } catch (e) {
            return null;
        }
    }

    async function waitForDecodedVideoProgress(timeoutMs = 1800) {
        const baseline = await readDecodedVideoStats();
        if (!baseline) return await waitForVideoFrame(timeoutMs);

        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
            await sleep(120);
            const current = await readDecodedVideoStats();
            if (!current) continue;
            if ((current.framesDecoded || 0) > (baseline.framesDecoded || 0)) {
                return true;
            }
        }
        return false;
    }

    // ─── WebRTC Stats Polling ───
    const statEls = {
        fps: document.getElementById('statFps'),
        bitrate: document.getElementById('statBitrate'),
        codec: document.getElementById('statCodec'),
        loss: document.getElementById('statLoss'),
        jitter: document.getElementById('statJitter'),
    };
    let prevStats = { bytes: 0, frames: 0, ts: 0, packetsLost: 0, packetsRecv: 0 };

    function classifyFps(fps) { return fps >= 24 ? '' : fps >= 15 ? 'warn' : 'bad'; }
    function classifyLoss(pct) { return pct <= 1 ? '' : pct <= 5 ? 'warn' : 'bad'; }
    function classifyJitter(ms) { return ms <= 30 ? '' : ms <= 80 ? 'warn' : 'bad'; }

    async function pollStats() {
        if (!pc || pc.connectionState !== 'connected') return;
        try {
            const stats = await pc.getStats();
            let codecName = '';
            const codecMap = {};
            stats.forEach(report => {
                if (report.type === 'codec') {
                    codecMap[report.id] = report.mimeType?.split('/')[1] || report.mimeType;
                }
            });
            stats.forEach(report => {
                if (report.type === 'inbound-rtp' && report.kind === 'video') {
                    const now = report.timestamp;
                    const dt = (now - prevStats.ts) / 1000; // seconds
                    if (dt > 0 && prevStats.ts > 0) {
                        // FPS
                        const dFrames = (report.framesDecoded || 0) - prevStats.frames;
                        const fps = Math.round(dFrames / dt);
                        statEls.fps.textContent = fps;
                        statEls.fps.className = 'stat-value ' + classifyFps(fps);

                        // Bitrate
                        const dBytes = (report.bytesReceived || 0) - prevStats.bytes;
                        const kbps = Math.round((dBytes * 8) / (dt * 1000));
                        statEls.bitrate.textContent = kbps > 1000 ? (kbps / 1000).toFixed(1) + ' Mbps' : kbps + ' kbps';

                        // Packet loss
                        const dLost = (report.packetsLost || 0) - prevStats.packetsLost;
                        const dRecv = (report.packetsReceived || 0) - prevStats.packetsRecv;
                        const lossPct = dRecv > 0 ? ((dLost / (dLost + dRecv)) * 100) : 0;
                        statEls.loss.textContent = lossPct.toFixed(1) + '%';
                        statEls.loss.className = 'stat-value ' + classifyLoss(lossPct);

                        // Jitter
                        const jitterMs = Math.round((report.jitter || 0) * 1000);
                        statEls.jitter.textContent = jitterMs + ' ms';
                        statEls.jitter.className = 'stat-value ' + classifyJitter(jitterMs);
                    }
                    prevStats = {
                        bytes: report.bytesReceived || 0,
                        frames: report.framesDecoded || 0,
                        ts: now,
                        packetsLost: report.packetsLost || 0,
                        packetsRecv: report.packetsReceived || 0,
                    };
                    // Codec
                    if (report.codecId && codecMap[report.codecId]) {
                        codecName = codecMap[report.codecId];
                    }
                }
            });
            if (codecName) statEls.codec.textContent = codecName;
        } catch (e) {
            console.warn('stats poll error:', e);
        }
    }
    setInterval(pollStats, 1000);

    // ─── Encoder H/S Badge (from publisher side) ───
    const encBadge = document.getElementById('encBadge');
    async function pollEncoderType() {
        if (!activeRoom) return;
        try {
            const resp = await fetch(`/api/encoder-stats`);
            const data = await resp.json();
            const tab = data.tabs?.find(t => t.room === activeRoom);
            if (!tab) {
                encBadge.textContent = 'IDLE';
                encBadge.className = 'enc-badge enc-idle';
            } else if (tab.error) {
                encBadge.textContent = 'IDLE';
                encBadge.className = 'enc-badge enc-idle';
            } else if (tab.isHardware) {
                encBadge.textContent = 'HW';
                encBadge.className = 'enc-badge enc-hw';
            } else {
                encBadge.textContent = 'SW';
                encBadge.className = 'enc-badge enc-sw';
            }
        } catch (e) { /* ignore */ }
    }
    setInterval(pollEncoderType, 3000);
    setTimeout(pollEncoderType, 1500);

    // ─── Tab Bar ───
    const TAB_STORE_KEY = initialBrowserId === null ? '' : `viewer_tabs_${initialBrowserId}`;

    function tabRefKey(ref) {
        return `${ref.browser_id}:${ref.tab_id}`;
    }

    function normalizeSavedTab(value) {
        if (typeof value === 'number') {
            return { browser_id: initialBrowserId, tab_id: value };
        }
        if (value && Number.isInteger(value.browser_id) && Number.isInteger(value.tab_id)) {
            return { browser_id: value.browser_id, tab_id: value.tab_id };
        }
        return null;
    }

    function loadViewerTabs() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(TAB_STORE_KEY));
            if (Array.isArray(saved) && saved.length) {
                myTabs = saved.map(normalizeSavedTab).filter(Boolean);
            }
        } catch { }
        if (!myTabs.length) {
            myTabs = [{ browser_id: initialBrowserId, tab_id: initialTabId }];
        }
        if (!myTabs.some(t => t.browser_id === initialBrowserId && t.tab_id === initialTabId)) {
            myTabs.push({ browser_id: initialBrowserId, tab_id: initialTabId });
        }
    }

    async function loadWorkbenchTabs() {
        if (!Number.isInteger(initialWorkbenchId)) throw new Error('Invalid workbench ID');

        const res = await fetch(`/api/admin/workbenches/${initialWorkbenchId}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Failed to open workbench');

        myTabs = (data.active_tabs || []).map(t => ({
            browser_id: t.browser_id,
            tab_id: t.tab_id,
            url: t.url,
            allowed_hostname: t.allowed_hostname,
            workbench_id: t.workbench_id,
            workbench_name: t.workbench_name,
        }));
        if (!myTabs.length) throw new Error('Workbench has no valid tabs');

        const activeParam = new URLSearchParams(location.search).get('active');
        const active = activeParam ? myTabs.find(t => tabRefKey(t) === activeParam) : null;
        const first = active || myTabs[0];
        setActiveTab(first.browser_id, first.tab_id, false);
    }

    function saveTabs() {
        if (!isWorkbench && TAB_STORE_KEY) {
            sessionStorage.setItem(TAB_STORE_KEY, JSON.stringify(myTabs));
        }
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    function canReuseStream() {
        return !!(
            pc &&
            sfuSubId &&
            (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed')
        );
    }

    async function prepareActiveStream(timeoutMs = 8000) {
        if (activeBrowserId === null || activeTabId === null) return false;
        try {
            const res = await fetch(
                `/api/admin/browsers/${activeBrowserId}/tabs/${activeTabId}/prepare-stream?timeout_ms=${timeoutMs}`,
                { method: 'POST' }
            );
            const data = await res.json().catch(() => ({}));
            if (res.ok && data.ready !== false) return true;
            console.warn('[viewer] stream prepare failed:', res.status, data);
        } catch (e) {
            console.warn('[viewer] stream prepare error:', e);
        }
        return false;
    }

    function switchControlTab(browserId, tabId) {
        if (!isWorkbench) return Promise.resolve(true);
        if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.resolve(false);

        if (pendingControlSwitch) {
            pendingControlSwitch.reject(new Error('Superseded by another switch'));
            pendingControlSwitch = null;
        }

        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                if (pendingControlSwitch) {
                    pendingControlSwitch = null;
                    reject(new Error('Control switch timed out'));
                }
            }, 3000);

            pendingControlSwitch = {
                resolve: (msg) => {
                    clearTimeout(timer);
                    resolve(msg);
                },
                reject: (err) => {
                    clearTimeout(timer);
                    reject(err);
                },
            };

            ws.send(JSON.stringify({
                type: 'switch_tab',
                browser_id: browserId,
                tab_id: tabId,
            }));
        });
    }

    async function switchStreamFast() {
        if (!canReuseStream()) return false;

        const subId = sfuSubId;
        const room = activeRoom;
        setStatus('Switching…', 'connecting');
        showNavOverlay();

        if (isWorkbench) {
            if (!await prepareActiveStream(8000)) return false;
        } else {
            if (ws) {
                ws.onclose = null;
                try { ws.close(); } catch (e) { }
            }

            const wsReady = await connectWS({ connectStream: false });
            if (!wsReady) return false;
            await prepareActiveStream(8000);
        }

        for (let attempt = 0; attempt < 8; attempt++) {
            try {
                const res = await fetch(
                    `/api/sfu/switch?sub_id=${encodeURIComponent(subId)}&room=${encodeURIComponent(room)}&timeout_ms=2500`,
                    { method: 'POST' }
                );
                if (res.ok) {
                    console.log('[viewer] fast switch to', room);
                    const decoded = await waitForDecodedVideoProgress(3200);
                    if (!decoded) {
                        console.warn('[viewer] fast switch produced no decoded frame, falling back to reconnect');
                        return false;
                    }
                    if (isWorkbench) {
                        try {
                            if (!await switchControlTab(activeBrowserId, activeTabId)) return false;
                        } catch (e) {
                            console.warn('[viewer] control switch failed:', e);
                            return false;
                        }
                    }
                    displayedRoom = room;
                    hideNavOverlay();
                    setStatus('Connected', 'connected');
                    if (typeof startFrameWatch === 'function') startFrameWatch();
                    return true;
                }
                console.warn('[viewer] switch attempt failed:', res.status, await res.text());
            } catch (e) {
                console.warn('[viewer] switch attempt error:', e);
            }
            await sleep(250);
        }

        return false;
    }

    function setActiveTab(browserId, tabId, updateUrl = true) {
        activeBrowserId = browserId;
        activeTabId = tabId;
        activeRoom = `${activeBrowserId}_${activeTabId}`;
        updateActiveLabels();
        if (updateUrl) updateHistoryForActiveTab();
        const meta = activeTabMeta();
        if (meta?.url) urlInput.value = meta.url;
    }

    function updateActiveLabels() {
        const label = activeBrowserId === null ? 'No tab' : `${activeBrowserId}/${activeTabId}`;
        tabLabel.textContent = label;
        document.title = isWorkbench ? `Workbench - ${label}` : `TabShare - ${label}`;
    }

    function updateHistoryForActiveTab() {
        if (isWorkbench) {
            const params = new URLSearchParams(location.search);
            params.set('active', `${activeBrowserId}:${activeTabId}`);
            history.replaceState(null, '', `/workbench/${initialWorkbenchId}?${params.toString()}`);
        } else {
            history.replaceState(null, '', `/viewer/${activeBrowserId}/${activeTabId}`);
        }
    }

    function renderTabBar() {
        if (!myTabs.length) {
            tabBar.innerHTML = '';
            return;
        }
        const items = myTabs.map(ref => {
            const isActive = ref.browser_id === activeBrowserId && ref.tab_id === activeTabId;
            const domain = ref.allowed_hostname ? `<span class="tab-domain">${ref.allowed_hostname}</span>` : '';
            const close = isWorkbench ? '' : `<span class="tab-close" onclick="event.stopPropagation(); closeTab(${ref.browser_id}, ${ref.tab_id})">×</span>`;
            return `<div class="tab-item${isActive ? ' active' : ''}" data-key="${tabRefKey(ref)}" onclick="switchTab(${ref.browser_id}, ${ref.tab_id})">
            <span class="tab-title">${ref.browser_id}/${ref.tab_id}</span>
            ${domain}
            ${close}
        </div>`;
        }).join('');
        tabBar.innerHTML = items + (isWorkbench ? '' : '<div class="tab-new" onclick="createNewTab()" title="New Tab">+</div>');
    }

    // 初始化：获取当前 tab 的 URL
    async function fetchCurrentTabUrl() {
        if (activeBrowserId === null || activeTabId === null) return;
        try {
            const data = await fetch(`/api/admin/browsers/${activeBrowserId}/tabs`).then(r => r.json());
            const cur = (data.tabs || []).find(t => t.tab_id === activeTabId);
            if (cur && cur.url) {
                urlInput.value = cur.url;
                const meta = activeTabMeta();
                if (meta) {
                    meta.url = cur.url;
                    meta.allowed_hostname = cur.allowed_hostname || meta.allowed_hostname;
                }
            }
        } catch (e) { }
    }

    async function switchTab(browserId, tabId) {
        if (switchInProgress) {
            console.log('[viewer] switch ignored while another switch is in progress');
            return;
        }
        if (tabId === undefined) {
            tabId = browserId;
            browserId = activeBrowserId;
        }
        const targetRoom = `${browserId}_${tabId}`;
        console.log('[viewer] switch tab click:', targetRoom, 'active=', activeRoom, 'displayed=', displayedRoom);
        if (browserId === activeBrowserId && tabId === activeTabId && displayedRoom === targetRoom) return;

        const exists = myTabs.some(t => t.browser_id === browserId && t.tab_id === tabId);
        if (!exists) {
            if (isWorkbench) {
                showNotice('工作台只能切换固定 tab');
                return;
            }
            myTabs.push({ browser_id: browserId, tab_id: tabId });
        }

        switchInProgress = true;
        try {
            setActiveTab(browserId, tabId);
            saveTabs();
            renderTabBar();

            if (await switchStreamFast()) {
                return;
            }

            // Slow path: full reconnect
            startSession();
        } finally {
            switchInProgress = false;
        }
    }

    async function closeTab(browserId, tabId) {
        if (tabId === undefined) {
            tabId = browserId;
            browserId = activeBrowserId;
        }
        if (isWorkbench) {
            showNotice('工作台不支持关闭固定 tab');
            return;
        }
        if (!confirm(`Close tab ${browserId}/${tabId}?`)) return;
        try {
            await fetch(`/api/admin/browsers/${browserId}/tabs/${tabId}`, { method: 'DELETE' });
            myTabs = myTabs.filter(t => !(t.browser_id === browserId && t.tab_id === tabId));
            saveTabs();
            if (browserId === activeBrowserId && tabId === activeTabId) {
                if (myTabs.length > 0) {
                    switchTab(myTabs[0].browser_id, myTabs[0].tab_id);
                } else {
                    renderTabBar();
                    setStatus('No tabs', 'error');
                }
            } else {
                renderTabBar();
            }
        } catch (e) {
            console.error('closeTab error:', e);
        }
    }

    async function createNewTab() {
        if (isWorkbench) {
            showNotice('工作台不支持新增 tab');
            return;
        }
        try {
            const res = await fetch(`/api/admin/browsers/${activeBrowserId}/tabs`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: 'https://www.bing.com',
                    viewport_width: currentRes.w,
                    viewport_height: currentRes.h,
                }),
            });
            const tab = await res.json();
            if (tab.tab_id >= 0) {
                myTabs.push({ browser_id: activeBrowserId, tab_id: tab.tab_id });
                saveTabs();
                switchTab(activeBrowserId, tab.tab_id);
            }
        } catch (e) {
            console.error('createNewTab error:', e);
        }
    }

    // ─── File Transfer ───
    function showDownloadToast(msg) {
        const sizeStr = msg.size > 1048576
            ? (msg.size / 1048576).toFixed(1) + ' MB'
            : (msg.size / 1024).toFixed(1) + ' KB';
        const toast = document.createElement('div');
        toast.className = 'dl-toast';
        toast.innerHTML = `
        📥 <span><strong>${msg.filename}</strong> (${sizeStr})</span>
        <a href="${msg.download_url}" download="${msg.filename}">Download</a>
        <span class="dl-close" onclick="this.parentElement.remove()">×</span>
    `;
        document.body.appendChild(toast);
        setTimeout(() => { if (toast.parentElement) toast.remove(); }, 15000);
    }

    async function uploadFile(input) {
        const file = input.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await fetch(`/api/admin/browsers/${activeBrowserId}/tabs/${activeTabId}/upload`, {
                method: 'POST',
                body: formData,
            });
            const result = await res.json();
            console.log('Upload result:', result);
            // Show confirmation
            const toast = document.createElement('div');
            toast.className = 'dl-toast';
            const sizeStr = result.size > 1048576
                ? (result.size / 1048576).toFixed(1) + ' MB'
                : (result.size / 1024).toFixed(1) + ' KB';
            toast.innerHTML = `
            📤 <span>Uploaded <strong>${result.filename}</strong> (${sizeStr})</span>
            <span class="dl-close" onclick="this.parentElement.remove()">×</span>
        `;
            document.body.appendChild(toast);
            setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);
        } catch (e) {
            console.error('Upload error:', e);
            alert('Upload failed: ' + e.message);
        }
        input.value = ''; // Reset for re-upload
    }

    // Expose nav functions for toolbar onclick
    window.navGo = navGo;
    let _translated = false;
    function translatePage() {
        if (ws && ws.readyState === WebSocket.OPEN) {
            _translated = !_translated;
            ws.send(JSON.stringify({ type: 'translate', restore: !_translated }));
            const btn = document.getElementById('translateBtn');
            btn.textContent = _translated ? '🌐 Original' : '🌐 Translate';
        }
    }

    async function init() {
        try {
            if (isWorkbench) {
                await loadWorkbenchTabs();
            } else {
                loadViewerTabs();
                setActiveTab(initialBrowserId, initialTabId, false);
            }
            renderTabBar();
            fetchCurrentTabUrl();
            startSession();
        } catch (e) {
            console.error('init error:', e);
            showNotice(e.message || 'Failed to initialize');
            setStatus('Init failed', 'error');
            hideNavOverlay();
        }
    }

    window.navBack = navBack;
    window.navForward = navForward;
    window.navReload = navReload;
    window.setScale = setScale;
    window.toggleFullscreen = toggleFullscreen;
    window.switchTab = switchTab;
    window.closeTab = closeTab;
    window.createNewTab = createNewTab;
    window.uploadFile = uploadFile;
    window.translatePage = translatePage;
    init();
})();
