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

  // Canvas / WebGL / Audio: 有头模式下使用 Chrome 原生渲染输出，不注入噪音
  // 篡改这些 API 本身就是检测向量

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
