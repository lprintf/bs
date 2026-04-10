"""
GeoIP 工具 — 根据代理 IP 自动匹配 timezone/locale/geolocation
"""
from typing import Optional
from urllib.parse import urlparse


# IP → country code → locale/timezone 映射表
# 可根据业务需要扩展
COUNTRY_LOCALE_MAP = {
    "SG": {
        "locale": "en-SG",
        "timezone": "Asia/Singapore",
        "lat": 1.3521,
        "lon": 103.8198,
    },
    "US": {
        "locale": "en-US",
        "timezone": "America/Los_Angeles",
        "lat": 34.0522,
        "lon": -118.2437,
    },
    "GB": {
        "locale": "en-GB",
        "timezone": "Europe/London",
        "lat": 51.5074,
        "lon": -0.1278,
    },
    "JP": {
        "locale": "ja-JP",
        "timezone": "Asia/Tokyo",
        "lat": 35.6762,
        "lon": 139.6503,
    },
    "KR": {
        "locale": "ko-KR",
        "timezone": "Asia/Seoul",
        "lat": 37.5665,
        "lon": 126.9780,
    },
    "DE": {
        "locale": "de-DE",
        "timezone": "Europe/Berlin",
        "lat": 52.5200,
        "lon": 13.4050,
    },
    "FR": {
        "locale": "fr-FR",
        "timezone": "Europe/Paris",
        "lat": 48.8566,
        "lon": 2.3522,
    },
    "AU": {
        "locale": "en-AU",
        "timezone": "Australia/Sydney",
        "lat": -33.8688,
        "lon": 151.2093,
    },
    "HK": {
        "locale": "zh-HK",
        "timezone": "Asia/Hong_Kong",
        "lat": 22.3193,
        "lon": 114.1694,
    },
    "TW": {
        "locale": "zh-TW",
        "timezone": "Asia/Taipei",
        "lat": 25.0330,
        "lon": 121.5654,
    },
    "IN": {
        "locale": "en-IN",
        "timezone": "Asia/Kolkata",
        "lat": 28.6139,
        "lon": 77.2090,
    },
    "BR": {
        "locale": "pt-BR",
        "timezone": "America/Sao_Paulo",
        "lat": -23.5505,
        "lon": -46.6333,
    },
    "CA": {
        "locale": "en-CA",
        "timezone": "America/Toronto",
        "lat": 43.6532,
        "lon": -79.3832,
    },
    "PH": {
        "locale": "en-PH",
        "timezone": "Asia/Manila",
        "lat": 14.5995,
        "lon": 120.9842,
    },
    "TH": {
        "locale": "th-TH",
        "timezone": "Asia/Bangkok",
        "lat": 13.7563,
        "lon": 100.5018,
    },
    "VN": {
        "locale": "vi-VN",
        "timezone": "Asia/Ho_Chi_Minh",
        "lat": 10.8231,
        "lon": 106.6297,
    },
    "ID": {
        "locale": "id-ID",
        "timezone": "Asia/Jakarta",
        "lat": -6.2088,
        "lon": 106.8456,
    },
    "MY": {
        "locale": "ms-MY",
        "timezone": "Asia/Kuala_Lumpur",
        "lat": 3.1390,
        "lon": 101.6869,
    },
}

DEFAULT_GEO = COUNTRY_LOCALE_MAP["SG"]  # 默认新加坡

# GeoIP 数据库（延迟加载）
_geolite2 = None


def _get_geolite2():
    """延迟加载 geolite2，避免未安装时直接报错"""
    global _geolite2
    if _geolite2 is None:
        try:
            from geoip import geolite2

            _geolite2 = geolite2
        except ImportError:
            print("[GeoIP] python-geoip-geolite2 not installed, using defaults")
            _geolite2 = False  # 标记为不可用
    return _geolite2 if _geolite2 is not False else None


def lookup_geoip(ip: str) -> dict:
    """
    根据 IP 地址查询地理信息。
    返回 {"locale": ..., "timezone": ..., "lat": ..., "lon": ...}
    """
    geolite2 = _get_geolite2()
    if geolite2 is None:
        return DEFAULT_GEO.copy()

    try:
        match = geolite2.lookup(ip)
        if match and match.country:
            cc = match.country
            geo = COUNTRY_LOCALE_MAP.get(cc, DEFAULT_GEO).copy()
            # 优先使用精确的 timezone
            if match.timezone:
                geo["timezone"] = match.timezone
            # 优先使用精确坐标
            if match.location:
                geo["lat"] = match.location[0]
                geo["lon"] = match.location[1]
            print(f"[GeoIP] {ip} → {cc} ({geo['locale']}, {geo['timezone']})")
            return geo
    except Exception as e:
        print(f"[GeoIP] Lookup failed for {ip}: {e}")

    return DEFAULT_GEO.copy()


def extract_ip_from_proxy(proxy_url: str) -> Optional[str]:
    """
    从代理 URL 提取主机名/IP。
    支持格式: socks5://user:pass@1.2.3.4:1080, http://1.2.3.4:8080, etc.
    """
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url)
        return parsed.hostname
    except Exception:
        return None
