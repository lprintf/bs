/**
 * stealth.js — 反检测注入脚本
 * 通过 Playwright context.add_init_script() 注入
 * {{FINGERPRINT_JSON}} 在 Python 端替换为实际指纹数据
 */
(() => {
  'use strict';

  const FP = {{FINGERPRINT_JSON}};

  // ========== Helper: seeded PRNG (mulberry32) ==========
  function mulberry32(a) {
    return function () {
      a |= 0;
      a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ========== 1. navigator.webdriver ==========
  // 删除自动化标志
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
  });
  // 同时处理原型链
  if (Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver')) {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  }

  // ========== 2. 硬件信息 ==========
  Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => FP.hardware_concurrency,
    configurable: true,
  });
  Object.defineProperty(navigator, 'deviceMemory', {
    get: () => FP.device_memory,
    configurable: true,
  });

  // ========== 3. 语言 ==========
  const primaryLang = FP.locale;
  const baseLang = FP.locale.split('-')[0];
  Object.defineProperty(navigator, 'language', {
    get: () => primaryLang,
    configurable: true,
  });
  Object.defineProperty(navigator, 'languages', {
    get: () => Object.freeze([primaryLang, baseLang]),
    configurable: true,
  });

  // ========== 4. Screen / Display ==========
  Object.defineProperty(screen, 'width', {
    get: () => FP.screen_width,
    configurable: true,
  });
  Object.defineProperty(screen, 'height', {
    get: () => FP.screen_height,
    configurable: true,
  });
  Object.defineProperty(screen, 'availWidth', {
    get: () => FP.screen_width,
    configurable: true,
  });
  Object.defineProperty(screen, 'availHeight', {
    get: () => FP.screen_height - 25, // macOS menu bar
    configurable: true,
  });
  Object.defineProperty(screen, 'colorDepth', {
    get: () => FP.color_depth,
    configurable: true,
  });
  Object.defineProperty(screen, 'pixelDepth', {
    get: () => FP.color_depth,
    configurable: true,
  });
  Object.defineProperty(window, 'devicePixelRatio', {
    get: () => FP.pixel_ratio,
    configurable: true,
  });

  // ========== 5. Canvas 指纹噪音 ==========
  const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const _origToBlob = HTMLCanvasElement.prototype.toBlob;
  const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

  function addCanvasNoise(canvas) {
    try {
      const ctx = canvas.getContext('2d');
      if (!ctx || canvas.width === 0 || canvas.height === 0) return;
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const rng = mulberry32(FP.canvas_noise_seed);
      const pixels = imageData.data;
      // 修改少量像素以产生稳定的噪音
      const count = Math.min(20, Math.floor(pixels.length / 4));
      for (let i = 0; i < count; i++) {
        const idx = Math.floor(rng() * pixels.length) & ~3; // 4字节对齐
        pixels[idx] = (pixels[idx] + Math.floor(rng() * 3) - 1) & 0xff;
        pixels[idx + 1] =
          (pixels[idx + 1] + Math.floor(rng() * 3) - 1) & 0xff;
      }
      ctx.putImageData(imageData, 0, 0);
    } catch (e) {
      // 跨域 canvas 等情况，静默忽略
    }
  }

  HTMLCanvasElement.prototype.toDataURL = function () {
    addCanvasNoise(this);
    return _origToDataURL.apply(this, arguments);
  };

  HTMLCanvasElement.prototype.toBlob = function () {
    addCanvasNoise(this);
    return _origToBlob.apply(this, arguments);
  };

  CanvasRenderingContext2D.prototype.getImageData = function () {
    const result = _origGetImageData.apply(this, arguments);
    const rng = mulberry32(FP.canvas_noise_seed + 1);
    const count = Math.min(10, Math.floor(result.data.length / 4));
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(rng() * result.data.length) & ~3;
      result.data[idx] = (result.data[idx] + Math.floor(rng() * 3) - 1) & 0xff;
    }
    return result;
  };

  // ========== 6. WebGL 指纹 ==========
  const UNMASKED_VENDOR_WEBGL = 0x9245;
  const UNMASKED_RENDERER_WEBGL = 0x9246;

  function patchWebGL(proto) {
    const origGetParam = proto.getParameter;
    proto.getParameter = function (param) {
      if (param === UNMASKED_VENDOR_WEBGL) return FP.webgl_vendor;
      if (param === UNMASKED_RENDERER_WEBGL) return FP.webgl_renderer;
      return origGetParam.call(this, param);
    };

    // readPixels 噪音
    const origReadPixels = proto.readPixels;
    proto.readPixels = function () {
      origReadPixels.apply(this, arguments);
      // arguments[6] 是 pixels ArrayBufferView
      const pixels = arguments[6];
      if (pixels && pixels.length) {
        const rng = mulberry32(FP.webgl_noise_seed);
        const count = Math.min(10, pixels.length);
        for (let i = 0; i < count; i++) {
          const idx = Math.floor(rng() * pixels.length);
          pixels[idx] = (pixels[idx] + Math.floor(rng() * 3) - 1) & 0xff;
        }
      }
    };
  }

  if (typeof WebGLRenderingContext !== 'undefined') {
    patchWebGL(WebGLRenderingContext.prototype);
  }
  if (typeof WebGL2RenderingContext !== 'undefined') {
    patchWebGL(WebGL2RenderingContext.prototype);
  }

  // ========== 7. AudioContext 噪音 ==========
  if (typeof AnalyserNode !== 'undefined') {
    const origGetFloat = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = function (array) {
      origGetFloat.call(this, array);
      if (array && array.length) {
        const rng = mulberry32(FP.audio_noise_seed);
        for (let i = 0; i < array.length; i++) {
          array[i] += (rng() - 0.5) * 0.001;
        }
      }
    };

    const origGetByte = AnalyserNode.prototype.getByteFrequencyData;
    AnalyserNode.prototype.getByteFrequencyData = function (array) {
      origGetByte.call(this, array);
      if (array && array.length) {
        const rng = mulberry32(FP.audio_noise_seed + 1);
        for (let i = 0; i < Math.min(5, array.length); i++) {
          const idx = Math.floor(rng() * array.length);
          array[idx] = Math.max(
            0,
            Math.min(255, array[idx] + Math.floor(rng() * 3) - 1)
          );
        }
      }
    };
  }

  // ========== 8. Battery API ==========
  if (navigator.getBattery) {
    const fakeBattery = {
      charging: true,
      chargingTime: Infinity,
      dischargingTime: Infinity,
      level: 0.95 + mulberry32(FP.canvas_noise_seed + 100)() * 0.05,
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => true,
    };
    navigator.getBattery = () => Promise.resolve(fakeBattery);
  }

  // ========== 9. chrome.runtime (防止 headless 检测) ==========
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      connect: function () {},
      sendMessage: function () {},
      id: undefined,
    };
  }
  // chrome.app
  if (!window.chrome.app) {
    window.chrome.app = {
      isInstalled: false,
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    };
  }

  // ========== 10. Plugins & MimeTypes ==========
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const plugins = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1 },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 1 },
      ];
      plugins.length = 3;
      Object.setPrototypeOf(plugins, PluginArray.prototype);
      return plugins;
    },
    configurable: true,
  });

  // ========== 11. MediaDevices 噪音 ==========
  if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
    const origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
    navigator.mediaDevices.enumerateDevices = async function () {
      const devices = await origEnum();
      const rng = mulberry32(FP.canvas_noise_seed + 200);
      return devices.map((d) => ({
        deviceId: Array.from({ length: 64 }, () =>
          '0123456789abcdef'[Math.floor(rng() * 16)]
        ).join(''),
        groupId: d.groupId,
        kind: d.kind,
        label: d.label,
      }));
    };
  }

  // ========== 12. DoNotTrack ==========
  if (FP.do_not_track === '1') {
    Object.defineProperty(navigator, 'doNotTrack', { get: () => '1', configurable: true });
  } else if (FP.do_not_track === '0') {
    Object.defineProperty(navigator, 'doNotTrack', { get: () => '0', configurable: true });
  }
  // "unset" = 保持默认

  // ========== 13. 端口扫描保护 ==========
  // 拦截对常见本地端口的探测（白名单内部 SFU 端口）
  const _origFetch = window.fetch;
  const _allowedLocalPorts = ['8088']; // SFU internal
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : input?.url;
    if (url) {
      try {
        const u = new URL(url, location.href);
        if (
          (u.hostname === 'localhost' || u.hostname === '127.0.0.1') &&
          u.origin !== location.origin &&
          !_allowedLocalPorts.includes(u.port)
        ) {
          return Promise.reject(new TypeError('Failed to fetch'));
        }
      } catch (e) {}
    }
    return _origFetch.apply(this, arguments);
  };

  // ========== 14. Permissions API 一致性 ==========
  const origQuery = Permissions.prototype.query;
  Permissions.prototype.query = function (desc) {
    if (desc.name === 'notifications') {
      return Promise.resolve({ state: 'prompt', onchange: null });
    }
    return origQuery.call(this, desc);
  };

  // ========== 15. WebGPU 基本隔离 ==========
  // 如果 WebGPU 可用，覆盖 adapter info 使其与 WebGL 一致
  if (navigator.gpu && navigator.gpu.requestAdapter) {
    const origRequestAdapter = navigator.gpu.requestAdapter.bind(navigator.gpu);
    navigator.gpu.requestAdapter = async function () {
      const adapter = await origRequestAdapter.apply(this, arguments);
      if (adapter) {
        // 覆盖 requestAdapterInfo
        const origInfo = adapter.requestAdapterInfo?.bind(adapter);
        if (origInfo) {
          adapter.requestAdapterInfo = async function () {
            const info = await origInfo();
            return {
              vendor: FP.webgl_vendor,
              architecture: info?.architecture || '',
              device: info?.device || '',
              description: FP.webgl_renderer,
            };
          };
        }
      }
      return adapter;
    };
  }

  // console.debug('[stealth] Fingerprint applied:', FP.fingerprint_id);
})();
