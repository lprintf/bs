"""
Fingerprint — 生成/加载/应用浏览器指纹配置
每个浏览器实例对应一份独立的 fingerprint.json，首次创建时自动生成，
后续启动复用同一份以保证指纹一致性。
"""
import json
import hashlib
import random
import subprocess
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Fingerprint:
    """每个浏览器实例的独立指纹"""
    # 全局唯一 ID
    fingerprint_id: str = ""
    # 伪装的操作系统
    os: str = "linux"
    user_agent: str = ""
    # 硬件
    hardware_concurrency: int = 4
    device_memory: int = 8  # GB
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    pixel_ratio: float = 1.0
    # WebGL — 必须和 SwiftShader GL 能力匹配
    webgl_vendor: str = "Google Inc. (Intel)"
    webgl_renderer: str = "ANGLE (Intel, Intel UHD Graphics 630, OpenGL 4.5)"
    # 噪音种子（确保同一浏览器每次噪音一致，不同浏览器噪音不同）
    canvas_noise_seed: int = 0
    webgl_noise_seed: int = 0
    audio_noise_seed: int = 0
    # 网络
    do_not_track: str = "unset"  # "1", "0", "unset"
    # WebRTC
    webrtc_policy: str = "disable_non_proxied_udp"
    # locale/timezone（geoip 自动填充，也可手动指定）
    locale: str = "en-US"
    timezone: str = "Asia/Singapore"
    latitude: float = 1.3521
    longitude: float = 103.8198


# --- Linux Chrome UA 模板 ---
_UA_TEMPLATES = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{version} Safari/537.36",
]

_CHROME_VERSIONS = [
    "147.0.7727.50",
]


def _detect_chrome_version() -> str:
    """从 CHROME_PATH 二进制获取真实版本号，确保 UA 与 TLS 指纹一致"""
    chrome_path = os.environ.get("CHROME_PATH", "")
    if chrome_path and Path(chrome_path).exists():
        try:
            out = subprocess.check_output(
                [chrome_path, "--version"], timeout=5, text=True
            ).strip()
            # "Google Chrome for Testing 147.0.7727.50" -> "147.0.7727.50"
            parts = out.split()
            for p in reversed(parts):
                if p[0].isdigit() and "." in p:
                    return p
        except Exception:
            pass
    return _CHROME_VERSIONS[0]

# --- WebGL 配置 ---
# 必须和 SwiftShader 实际 GL 能力匹配（MAX_TEXTURE_SIZE=8192）
# Intel 集成显卡和 AMD APU 的能力最接近，避免 NVIDIA 独显（16384+）
_WEBGL_CONFIGS = [
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel UHD Graphics 630, OpenGL 4.5)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel UHD Graphics 770, OpenGL 4.5)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel Iris Xe Graphics, OpenGL 4.5)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon Vega 8 Graphics, OpenGL 4.5)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX Vega 11, OpenGL 4.5)"),
]


def generate_fingerprint(browser_id: str) -> Fingerprint:
    """为新浏览器生成一份稳定的指纹（基于 browser_id 派生，确保可重现）"""
    seed = int(hashlib.md5(browser_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    fp = Fingerprint()
    fp.fingerprint_id = hashlib.sha256(
        f"fp_{browser_id}_{seed}".encode()
    ).hexdigest()[:20]

    # UA — 版本号必须匹配真实 Chrome 二进制，否则 TLS/UA 不一致
    chrome_ver = _detect_chrome_version()
    fp.user_agent = rng.choice(_UA_TEMPLATES).format(version=chrome_ver)

    # Hardware
    # deviceMemory API 合法值: 0.25, 0.5, 1, 2, 4, 8（最大 8）
    fp.hardware_concurrency = rng.choice([4, 8, 16])
    fp.device_memory = rng.choice([4, 8])
    fp.screen_width = rng.choice([1920, 2560, 3440])
    fp.screen_height = rng.choice([1080, 1440])
    fp.pixel_ratio = 1.0  # Linux 桌面一般是 1.0

    # WebGL
    fp.webgl_vendor, fp.webgl_renderer = rng.choice(_WEBGL_CONFIGS)

    # 噪音种子
    fp.canvas_noise_seed = rng.randint(1, 2**31)
    fp.webgl_noise_seed = rng.randint(1, 2**31)
    fp.audio_noise_seed = rng.randint(1, 2**31)

    return fp


def load_or_create_fingerprint(browser_dir: Path, browser_id: str) -> Fingerprint:
    """加载已有指纹或生成新的，并持久化到 fingerprint.json"""
    fp_path = browser_dir / "fingerprint.json"
    if fp_path.exists():
        try:
            data = json.loads(fp_path.read_text())
            fp = Fingerprint(**data)
            print(f"[Fingerprint] Loaded existing: {fp.fingerprint_id} ({browser_id})")
            return fp
        except Exception as e:
            print(f"[Fingerprint] Failed to load {fp_path}, regenerating: {e}")

    fp = generate_fingerprint(browser_id)
    browser_dir.mkdir(parents=True, exist_ok=True)
    fp_path.write_text(json.dumps(asdict(fp), indent=2, ensure_ascii=False))
    print(f"[Fingerprint] Generated new: {fp.fingerprint_id} ({browser_id})")
    return fp


def save_fingerprint(browser_dir: Path, fp: Fingerprint):
    """保存指纹配置（geoip 更新后调用）"""
    fp_path = browser_dir / "fingerprint.json"
    browser_dir.mkdir(parents=True, exist_ok=True)
    fp_path.write_text(json.dumps(asdict(fp), indent=2, ensure_ascii=False))
