/**
 * stealth.js — 反检测注入脚本
 * Python 加载代码后通过 `(代码)(FP)` 的闭包执行方式传递指纹变量
 */
((FP) => {
  'use strict';

  // ========== 0. CDP 痕迹隐藏（必须最先执行）==========
  // Playwright/CDP 通过 UtilityScript.evaluate 执行 JS，
  // CF Turnstile 会检查 Error().stack 中是否包含这些关键字

  // 0a. 清洗 Error.stack 中的 CDP 痕迹
  const _origStackDesc = Object.getOwnPropertyDescriptor(Error.prototype, 'stack');
  if (_origStackDesc && _origStackDesc.get) {
    Object.defineProperty(Error.prototype, 'stack', {
      get: function () {
        const stack = _origStackDesc.get.call(this);
        if (typeof stack !== 'string') return stack;
        return stack
          .split('\n')
          .filter(line =>
            !line.includes('UtilityScript') &&
            !line.includes('__playwright') &&
            !line.includes('__patchright') &&
            !line.includes('pptr:') &&
            !line.includes('CDP')
          )
          .join('\n');
      },
      set: function (v) {
        if (_origStackDesc.set) _origStackDesc.set.call(this, v);
      },
      configurable: true,
    });
  } else {
    // Fallback: V8 可能用 value 而非 getter
    const _origErrorCtor = Error;
    const _origPrepare = Error.prepareStackTrace;
    Error.prepareStackTrace = function (error, stack) {
      const formatted = _origPrepare
        ? _origPrepare(error, stack)
        : stack.map(f => `    at ${f}`).join('\n');
      if (typeof formatted !== 'string') return formatted;
      return formatted
        .split('\n')
        .filter(line =>
          !line.includes('UtilityScript') &&
          !line.includes('__playwright') &&
          !line.includes('__patchright')
        )
        .join('\n');
    };
  }

  // 0c. 清除 Playwright 注入的全局变量
  try { delete window.__pwInitScripts; } catch (e) {}
  try { delete window.__playwright; } catch (e) {}
  try { delete window.__pw_manual; } catch (e) {}



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

  // ========== Helper: 伪装函数为 [native code] ==========
  const _nativeFns = new Set();
  const _origToString = Function.prototype.toString;
  Function.prototype.toString = function () {
    if (_nativeFns.has(this)) {
      return `function ${this.name || ''}() { [native code] }`;
    }
    return _origToString.call(this);
  };
  // 把 toString 本身也标记为 native
  _nativeFns.add(Function.prototype.toString);

  /** 注册一个函数使其 toString() 返回 [native code] */
  function markNative(fn) {
    _nativeFns.add(fn);
    return fn;
  }

  // ========== 1. navigator.webdriver ==========
  // 正常 Chrome: navigator.webdriver === false（不是 undefined）
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: markNative(function webdriver() { return false; }),
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}

  // ========== 1b. navigator.userAgentData (sec-ch-ua brands) ==========
  // Chrome 二进制版本和 UA 版本可能不同，需要在 JS 层覆盖 brands
  if (navigator.userAgentData && FP.user_agent) {
    const uaMatch = FP.user_agent.match(/Chrome\/(\d+)\.(\d+\.\d+\.\d+)/);
    if (uaMatch) {
      const majorVer = uaMatch[1];
      const fullVer = uaMatch[1] + '.' + uaMatch[2];
      const fakeBrands = Object.freeze([
        Object.freeze({ brand: 'Google Chrome', version: majorVer }),
        Object.freeze({ brand: 'Not.A/Brand', version: '8' }),
        Object.freeze({ brand: 'Chromium', version: majorVer }),
      ]);
      const fakeFullVersionList = Object.freeze([
        Object.freeze({ brand: 'Google Chrome', version: fullVer }),
        Object.freeze({ brand: 'Not.A/Brand', version: '8.0.0.0' }),
        Object.freeze({ brand: 'Chromium', version: fullVer }),
      ]);
      const origUAData = navigator.userAgentData;
      const fakeUAData = {
        brands: fakeBrands,
        mobile: origUAData.mobile,
        platform: origUAData.platform,
        getHighEntropyValues: markNative(function getHighEntropyValues(hints) {
          return origUAData.getHighEntropyValues(hints).then(function(values) {
            values.brands = fakeBrands;
            values.fullVersionList = fakeFullVersionList;
            return values;
          });
        }),
        toJSON: markNative(function toJSON() {
          return { brands: fakeBrands, mobile: origUAData.mobile, platform: origUAData.platform };
        }),
      };
      Object.setPrototypeOf(fakeUAData, NavigatorUAData.prototype);
      Object.defineProperty(Navigator.prototype, 'userAgentData', {
        get: markNative(function userAgentData() { return fakeUAData; }),
        configurable: true,
        enumerable: true,
      });
    }
  }


  // ========== 2. 硬件信息 ==========
  // 定义在 Navigator.prototype 上，使 getOwnPropertyNames(navigator) 返回空数组
  Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
    get: markNative(function hardwareConcurrency() { return FP.hardware_concurrency; }),
    configurable: true,
    enumerable: true,
  });
  Object.defineProperty(Navigator.prototype, 'deviceMemory', {
    get: markNative(function deviceMemory() { return FP.device_memory; }),
    configurable: true,
    enumerable: true,
  });

  // ========== 3. 语言 ==========
  const primaryLang = FP.locale;
  const baseLang = FP.locale.split('-')[0];
  Object.defineProperty(Navigator.prototype, 'language', {
    get: markNative(function language() { return primaryLang; }),
    configurable: true,
    enumerable: true,
  });
  Object.defineProperty(Navigator.prototype, 'languages', {
    get: markNative(function languages() { return Object.freeze([primaryLang, baseLang]); }),
    configurable: true,
    enumerable: true,
  });

  // ========== 4. Screen / Display ==========
  Object.defineProperty(screen, 'width', {
    get: markNative(function width() { return FP.screen_width; }),
    configurable: true,
  });
  Object.defineProperty(screen, 'height', {
    get: markNative(function height() { return FP.screen_height; }),
    configurable: true,
  });
  Object.defineProperty(screen, 'availWidth', {
    get: markNative(function availWidth() { return FP.screen_width; }),
    configurable: true,
  });
  Object.defineProperty(screen, 'availHeight', {
    get: markNative(function availHeight() { return FP.screen_height - 25; }),
    configurable: true,
  });
  Object.defineProperty(screen, 'colorDepth', {
    get: markNative(function colorDepth() { return FP.color_depth; }),
    configurable: true,
  });
  Object.defineProperty(screen, 'pixelDepth', {
    get: markNative(function pixelDepth() { return FP.color_depth; }),
    configurable: true,
  });
  Object.defineProperty(window, 'devicePixelRatio', {
    get: markNative(function devicePixelRatio() { return FP.pixel_ratio; }),
    configurable: true,
  });

  // ========== 5. Canvas 指纹噪音 ==========
  // 策略：不修改原始 canvas，在输出阶段注入确定性噪声
  // 同一 canvas 内容 + seed → 同一结果（避免 UNSTABLE 检测）
  const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const _origToBlob = HTMLCanvasElement.prototype.toBlob;
  const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

  // 基于 canvas 像素内容生成确定性 hash（轻量）
  function canvasContentHash(data) {
    let h = FP.canvas_noise_seed;
    // 采样少量像素作为 hash 输入（性能考虑）
    const step = Math.max(1, Math.floor(data.length / 64));
    for (let i = 0; i < data.length; i += step) {
      h = (h * 31 + data[i]) | 0;
    }
    return h;
  }

  // 对像素数据注入确定性噪声（不修改原始数据，返回新副本）
  function applyCanvasNoise(srcData) {
    const data = new Uint8ClampedArray(srcData);
    const rng = mulberry32(canvasContentHash(data));
    // 只修改极少量像素的低位（最大 ±1），不影响视觉
    const count = Math.min(10, Math.floor(data.length / 16));
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(rng() * data.length) & ~3; // 4字节对齐
      if (data[idx + 3] > 0) { // 只对非透明像素
        data[idx] = (data[idx] + (rng() > 0.5 ? 1 : -1)) & 0xff;
      }
    }
    return data;
  }

  HTMLCanvasElement.prototype.toDataURL = markNative(function toDataURL() {
    try {
      const ctx = this.getContext('2d');
      if (ctx && this.width > 0 && this.height > 0) {
        const orig = _origGetImageData.call(ctx, 0, 0, this.width, this.height);
        const noised = applyCanvasNoise(orig.data);
        const tmp = document.createElement('canvas');
        tmp.width = this.width;
        tmp.height = this.height;
        const tmpCtx = tmp.getContext('2d');
        const imgData = tmpCtx.createImageData(this.width, this.height);
        imgData.data.set(noised);
        tmpCtx.putImageData(imgData, 0, 0);
        return _origToDataURL.apply(tmp, arguments);
      }
    } catch (e) {}
    return _origToDataURL.apply(this, arguments);
  });

  HTMLCanvasElement.prototype.toBlob = markNative(function toBlob() {
    try {
      const ctx = this.getContext('2d');
      if (ctx && this.width > 0 && this.height > 0) {
        const orig = _origGetImageData.call(ctx, 0, 0, this.width, this.height);
        const noised = applyCanvasNoise(orig.data);
        const tmp = document.createElement('canvas');
        tmp.width = this.width;
        tmp.height = this.height;
        const tmpCtx = tmp.getContext('2d');
        const imgData = tmpCtx.createImageData(this.width, this.height);
        imgData.data.set(noised);
        tmpCtx.putImageData(imgData, 0, 0);
        return _origToBlob.apply(tmp, arguments);
      }
    } catch (e) {}
    return _origToBlob.apply(this, arguments);
  });

  CanvasRenderingContext2D.prototype.getImageData = markNative(function getImageData() {
    const result = _origGetImageData.apply(this, arguments);
    const data = result.data;

    // 修复 SwiftShader 透明像素 bug: alpha=0 时 RGB 应为 0
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] === 0) {
        data[i] = 0;
        data[i + 1] = 0;
        data[i + 2] = 0;
      }
    }

    // 确定性噪声（基于内容 hash，多次调用结果一致）
    const rng = mulberry32(canvasContentHash(data));
    const count = Math.min(10, Math.floor(data.length / 16));
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(rng() * data.length) & ~3;
      if (data[idx + 3] > 0) {
        data[idx] = (data[idx] + (rng() > 0.5 ? 1 : -1)) & 0xff;
      }
    }
    return result;
  });

  // ========== 6. WebGL 指纹噪音 ==========
  // 不伪装 vendor/renderer（SwiftShader 原值内部一致，伪装反而暴露不匹配）
  // 只对 readPixels 加噪音使指纹唯一
  function patchWebGL(proto) {
    const origReadPixels = proto.readPixels;
    proto.readPixels = markNative(function readPixels() {
      origReadPixels.apply(this, arguments);
      const pixels = arguments[6];
      if (pixels && pixels.length) {
        const rng = mulberry32(FP.webgl_noise_seed);
        const count = Math.min(10, pixels.length);
        for (let i = 0; i < count; i++) {
          const idx = Math.floor(rng() * pixels.length);
          pixels[idx] = (pixels[idx] + Math.floor(rng() * 3) - 1) & 0xff;
        }
      }
    });
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
      addEventListener: markNative(function addEventListener() {}),
      removeEventListener: markNative(function removeEventListener() {}),
      dispatchEvent: markNative(function dispatchEvent() { return true; }),
    };
    Object.defineProperty(Navigator.prototype, 'getBattery', {
      value: markNative(function getBattery() { return Promise.resolve(fakeBattery); }),
      configurable: true,
      writable: true,
      enumerable: true,
    });
  }

  // ========== 9. chrome 对象完整伪装 ==========
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      connect: function () {},
      sendMessage: function () {},
      id: undefined,
    };
  }
  if (!window.chrome.app) {
    window.chrome.app = {
      isInstalled: false,
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    };
  }
  // chrome.csi — bot.sannysoft 检测项
  if (!window.chrome.csi) {
    window.chrome.csi = function () {
      return {
        onloadT: Date.now(),
        startE: Date.now(),
        pageT: performance.now(),
        tran: 15,  // Navigation type: link click
      };
    };
  }
  // chrome.loadTimes — bot.sannysoft 检测项
  if (!window.chrome.loadTimes) {
    window.chrome.loadTimes = function () {
      return {
        commitLoadTime: Date.now() / 1000,
        connectionInfo: 'h2',
        finishDocumentLoadTime: Date.now() / 1000,
        finishLoadTime: Date.now() / 1000,
        firstPaintAfterLoadTime: 0,
        firstPaintTime: Date.now() / 1000,
        navigationType: 'Other',
        npnNegotiatedProtocol: 'h2',
        requestTime: Date.now() / 1000 - 0.16,
        startLoadTime: Date.now() / 1000 - 0.12,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true,
      };
    };
  }

  // ========== 10. Plugins & MimeTypes ==========
  // 不替换原生 PluginArray（会导致 instanceof 检查失败），
  // 只在 plugins 为空时才注入，且保持原生类型
  (() => {
    const origPlugins = navigator.plugins;
    // 如果已经有 plugins 且是 PluginArray，不替换
    if (origPlugins && origPlugins instanceof PluginArray && origPlugins.length > 0) return;
    // headless 下 plugins 可能为空但仍是 PluginArray 类型，不需要替换类型
    // 只需确保检测脚本看到 length > 0 即可
    // 通过 Proxy 包装原生 PluginArray 来注入 length
    try {
      Object.defineProperty(Navigator.prototype, 'plugins', {
        get: markNative(function plugins() {
          const proxy = new Proxy(origPlugins, {
            get(target, prop) {
              if (prop === 'length') return 3;
              if (prop === Symbol.toStringTag) return 'PluginArray';
              if (typeof prop === 'string' && /^\d+$/.test(prop)) {
                const fakePlugins = [
                  { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                  { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                  { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
                ];
                return fakePlugins[Number(prop)];
              }
              if (prop === 'item') return markNative(function item(i) { return proxy[i]; });
              if (prop === 'namedItem') return markNative(function namedItem(name) { return [proxy[0], proxy[1], proxy[2]].find(p => p?.name === name) || null; });
              if (prop === 'refresh') return markNative(function refresh() {});
              return Reflect.get(target, prop);
            },
          });
          Object.setPrototypeOf(proxy, PluginArray.prototype);
          return proxy;
        }),
        configurable: true,
      });
    } catch (e) {}
  })();

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
    Object.defineProperty(Navigator.prototype, 'doNotTrack', { get: markNative(function doNotTrack() { return '1'; }), configurable: true, enumerable: true });
  } else if (FP.do_not_track === '0') {
    Object.defineProperty(Navigator.prototype, 'doNotTrack', { get: markNative(function doNotTrack() { return '0'; }), configurable: true, enumerable: true });
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
  Permissions.prototype.query = function query(desc) {
    if (desc.name === 'notifications') {
      return Promise.resolve({ state: 'prompt', onchange: null });
    }
    return origQuery.call(this, desc);
  };
  markNative(Permissions.prototype.query);

  // ========== 14b. Notification.permission (headchr_permissions) ==========
  // headless 下 Notification.permission 返回 "denied"，正常浏览器返回 "default"
  try {
    Object.defineProperty(Notification, 'permission', {
      get: markNative(function permission() { return 'default'; }),
      configurable: true,
    });
  } catch (e) {}




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

  // ========== 20. 自动拒绝 WebAuthn (Security Key) 拦截 ==========
  // 当网站（如 Google）尝试唤起硬件安全密钥（USB/指纹）时，
  // Headless Chrome 会弹出一个原生 Modal（有时候不可见），这会导致整个页面被蒙层盖住无法点击。
  // 通过劫持 navigator.credentials.get 并在它请求 PublicKey 时直接抛出拒绝异常，
  // 可以模拟用户主动点击了“取消”，从而迫使 Google 释放页面焦点，露出“尝试其他方式”按钮。
  if (navigator.credentials && navigator.credentials.get) {
    const origGet = navigator.credentials.get;
    navigator.credentials.get = markNative(async function get(options) {
      if (options && options.publicKey) {
        throw new DOMException("The operation either timed out or was not allowed.", "NotAllowedError");
      }
      return origGet.apply(this, arguments);
    });
  }

})
