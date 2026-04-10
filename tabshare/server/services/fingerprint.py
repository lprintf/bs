"""
Fingerprint — 生成/加载/应用浏览器指纹配置
每个浏览器实例对应一份独立的 fingerprint.json，首次创建时自动生成，
后续启动复用同一份以保证指纹一致性。
"""
import json
import hashlib
import random
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Fingerprint:
    """每个浏览器实例的独立指纹"""
    # 全局唯一 ID
    fingerprint_id: str = ""
    # 伪装的操作系统
    os: str = "macOS"
    user_agent: str = ""
    # 硬件
    hardware_concurrency: int = 4
    device_memory: int = 8  # GB
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    pixel_ratio: float = 2.0
    # WebGL
    webgl_vendor: str = "Apple Inc."
    webgl_renderer: str = "Apple GPU"
    # 噪音种子（确保同一浏览器每次噪音一致，不同浏览器噪音不同）
    canvas_noise_seed: int = 0
    webgl_noise_seed: int = 0
    audio_noise_seed: int = 0
    # 网络
    do_not_track: str = "unset"  # "1", "0", "unset"
    # WebRTC
    webrtc_policy: str = "disable_non_proxied_udp"
    # locale/timezone（geoip 自动填充，也可手动指定）
    locale: str = "en-SG"
    timezone: str = "Asia/Singapore"
    latitude: float = 1.3521
    longitude: float = 103.8198


# --- macOS Chrome UA 模板 ---
_UA_TEMPLATES = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{version} Safari/537.36",
]

_CHROME_VERSIONS = [
    "131.0.6778.205",
    "132.0.6834.110",
    "133.0.6917.92",
    "134.0.6998.45",
    "142.0.7444.176",
]

# --- macOS 常见 WebGL 配置 ---
_WEBGL_CONFIGS = [
    ("Apple Inc.", "Apple GPU"),
    ("Apple Inc.", "Apple M1"),
    ("Apple Inc.", "Apple M2"),
    ("Apple Inc.", "Apple M3"),
    ("Apple Inc.", "AMD Radeon Pro 5500M"),
]


def generate_fingerprint(browser_id: str) -> Fingerprint:
    """为新浏览器生成一份稳定的指纹（基于 browser_id 派生，确保可重现）"""
    seed = int(hashlib.md5(browser_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    fp = Fingerprint()
    fp.fingerprint_id = hashlib.sha256(
        f"fp_{browser_id}_{seed}".encode()
    ).hexdigest()[:20]

    # UA
    chrome_ver = rng.choice(_CHROME_VERSIONS)
    fp.user_agent = rng.choice(_UA_TEMPLATES).format(version=chrome_ver)

    # Hardware
    fp.hardware_concurrency = rng.choice([4, 8, 10, 12])
    fp.device_memory = rng.choice([8, 16])
    fp.screen_width = rng.choice([1440, 1680, 1920, 2560])
    fp.screen_height = rng.choice([900, 1050, 1080, 1440])
    fp.pixel_ratio = rng.choice([1.0, 2.0])

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
