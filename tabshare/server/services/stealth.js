/**
 * stealth.js — 指纹一致性 & 噪音注入脚本（有头模式精简版）
 *
 * Xvfb 有头模式下，Chrome 原生已有正确的：
 *   - navigator.webdriver = false（通过 --disable-blink-features=AutomationControlled）
 *   - navigator.plugins（真实 PluginArray）
 *   - window.chrome 对象（完整）
 *   - Notification.permission = "default"
 *   - UA 不含 HeadlessChrome
 *
 * 本脚本只负责：
 *   1. CDP 痕迹清洗（Error.stack 中的 UtilityScript 等）
 *   2. 硬件/语言/屏幕指纹覆盖（保持跨实例一致性）
 *   3. Canvas/WebGL/Audio 噪音（指纹唯一化）
 *   4. Battery API 伪装
 *   5. 端口扫描保护
 *
 * {{FINGERPRINT_JSON}} 在 Python 端替换为实际指纹数据
 */
(() => {
  'use strict';

  // ========== 0. CDP 痕迹隐藏（必须最先执行）==========
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

  // ========== Helper: 伪装函数为 [native code] ==========
  const _nativeFns = new Set();
  const _origToString = Function.prototype.toString;
  Function.prototype.toString = function () {
    if (_nativeFns.has(this)) {
      return `function ${this.name || ''}() { [native code] }`;
    }
    return _origToString.call(this);
  };
  _nativeFns.add(Function.prototype.toString);

  function markNative(fn) {
    _nativeFns.add(fn);
    return fn;
  }

  // ========== 1. 硬件信息 ==========
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

  // ========== 2. 语言 ==========
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

  // ========== 3. Screen / Display ==========
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

  // ========== 4. Canvas 指纹噪音 ==========
  const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const _origToBlob = HTMLCanvasElement.prototype.toBlob;
  const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

  function canvasContentHash(data) {
    let h = 0x811c9dc5;
    const step = Math.max(1, Math.floor(data.length / 256));
    for (let i = 0; i < data.length; i += step) {
      h ^= data[i];
      h = Math.imul(h, 0x01000193);
    }
    return (h ^ FP.canvas_noise_seed) >>> 0;
  }

  function applyCanvasNoise(data) {
    const noised = new Uint8ClampedArray(data);
    const rng = mulberry32(canvasContentHash(data));
    const count = Math.min(10, Math.floor(data.length / 16));
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(rng() * data.length) & ~3;
      noised[idx] = (noised[idx] + (rng() > 0.5 ? 1 : -1)) & 0xff;
    }
    return noised;
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
    // 确定性噪声
    const rng = mulberry32(canvasContentHash(data));
    const count = Math.min(10, Math.floor(data.length / 16));
    for (let i = 0; i < count; i++) {
      const idx = Math.floor(rng() * data.length) & ~3;
      if (data[idx + 3] !== 0) {
        data[idx] = (data[idx] + (rng() > 0.5 ? 1 : -1)) & 0xff;
      }
    }
    return result;
  });

  // ========== 5. WebGL readPixels 噪音 ==========
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

  // ========== 6. AudioContext 噪音 ==========
  if (typeof AnalyserNode !== 'undefined') {
    const origGetFloat = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = markNative(function getFloatFrequencyData(array) {
      origGetFloat.call(this, array);
      if (array && array.length) {
        const rng = mulberry32(FP.audio_noise_seed);
        for (let i = 0; i < array.length; i++) {
          array[i] += (rng() - 0.5) * 0.001;
        }
      }
    });
  }
  if (typeof AudioBuffer !== 'undefined') {
    const origCopy = AudioBuffer.prototype.copyFromChannel;
    AudioBuffer.prototype.copyFromChannel = markNative(function copyFromChannel(dest, ch, offset) {
      origCopy.call(this, dest, ch, offset || 0);
      const rng = mulberry32(FP.audio_noise_seed + (ch || 0));
      for (let i = 0; i < dest.length; i++) {
        dest[i] += (rng() - 0.5) * 0.0001;
      }
    });
  }

  // ========== 7. Battery API ==========
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

  // ========== 8. MediaDevices ID 噪音 ==========
  if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
    const origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
    navigator.mediaDevices.enumerateDevices = markNative(async function enumerateDevices() {
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
    });
  }

  // ========== 9. DoNotTrack ==========
  if (FP.do_not_track === '1') {
    Object.defineProperty(Navigator.prototype, 'doNotTrack', { get: markNative(function doNotTrack() { return '1'; }), configurable: true, enumerable: true });
  } else if (FP.do_not_track === '0') {
    Object.defineProperty(Navigator.prototype, 'doNotTrack', { get: markNative(function doNotTrack() { return '0'; }), configurable: true, enumerable: true });
  }

  // ========== 10. 端口扫描保护 ==========
  const _origFetch = window.fetch;
  const _allowedLocalPorts = ['8088'];
  window.fetch = markNative(function fetch(input, init) {
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
  });

})();
