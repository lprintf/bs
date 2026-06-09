import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from urllib.parse import parse_qs, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)


ISSUER = os.getenv("AUTH_ISSUER", "tabshare-auth")
AUDIENCE = os.getenv("AUTH_AUDIENCE", "tabshare-api")
KEY_ID = os.getenv("JWT_KEY_ID", "demo-ed25519-1")
KEYS_DIR = Path(os.getenv("KEYS_DIR", "/app/keys"))
JWKS_PATH = Path(os.getenv("JWKS_PATH", "/app/keys/jwks.json"))
TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "600"))
DEMO_USER_EMAIL = os.getenv("DEMO_USER_EMAIL", "demo@example.com")
DEMO_USER_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "demo-password")
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "user_1")
LOGIN_BRAND_NAME = os.getenv("LOGIN_BRAND_NAME", "Demo Identity")
LOGIN_PRIMARY_COLOR = os.getenv("LOGIN_PRIMARY_COLOR", "#1f6feb")
LOGIN_ALLOWED_REDIRECT_HOSTS = {
    host.strip().lower()
    for host in os.getenv(
        "LOGIN_ALLOWED_REDIRECT_HOSTS",
        "127.0.0.1,localhost,bs-pilot.lprintf.com,gate-pilot.lprintf.com,auth-pilot.lprintf.com",
    ).split(",")
    if host.strip()
}
LOGIN_ALLOWED_REDIRECT_SUFFIXES = tuple(
    suffix.strip().lower()
    for suffix in os.getenv("LOGIN_ALLOWED_REDIRECT_SUFFIXES", "-pilot.lprintf.com").split(",")
    if suffix.strip()
)
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN", "").strip()
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "auto").strip().lower()
AUTH_COOKIE_AUTO_DOMAIN_SUFFIXES = tuple(
    suffix.strip().lstrip(".").lower()
    for suffix in os.getenv("AUTH_COOKIE_AUTO_DOMAIN_SUFFIXES", "lprintf.com").split(",")
    if suffix.strip()
)

PASSWORD_SALT = b"demo-static-salt"
PASSWORD_HASH = hashlib.pbkdf2_hmac(
    "sha256", DEMO_USER_PASSWORD.encode("utf-8"), PASSWORD_SALT, 120_000
)
ALL_JWKS = json.loads(JWKS_PATH.read_text(encoding="utf-8"))
ALL_JWKS_BY_KID = {key["kid"]: key for key in ALL_JWKS.get("keys", [])}
ACTIVE_KEY_ID = KEY_ID
PUBLISHED_KEY_IDS = set(
    kid.strip()
    for kid in os.getenv("JWKS_PUBLISHED_KEY_IDS", KEY_ID).split(",")
    if kid.strip()
)
ACTIVE_SESSIONS = {}
PRIVATE_KEYS = {}
PUBLIC_KEYS = {}


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def json_response(handler: BaseHTTPRequestHandler, status: int, body: dict, headers=None) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(payload)


def html_response(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    payload = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def redirect_response(handler: BaseHTTPRequestHandler, location: str, headers=None) -> None:
    handler.send_response(HTTPStatus.FOUND)
    handler.send_header("Location", location)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def is_allowed_redirect_uri(redirect_uri: str) -> bool:
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in LOGIN_ALLOWED_REDIRECT_HOSTS:
        return True
    return any(hostname.endswith(suffix) for suffix in LOGIN_ALLOWED_REDIRECT_SUFFIXES)


def cookie_value(handler: BaseHTTPRequestHandler, name: str) -> str:
    cookies = handler.headers.get("Cookie", "")
    for item in cookies.split(";"):
        key, _, value = item.strip().partition("=")
        if key == name:
            return value
    return ""


def request_hostname(handler: BaseHTTPRequestHandler) -> str:
    host = handler.headers.get("Host", "")
    return host.split(":", 1)[0].lower()


def cookie_domain_for_host(hostname: str) -> str:
    if AUTH_COOKIE_DOMAIN:
        return AUTH_COOKIE_DOMAIN
    for suffix in AUTH_COOKIE_AUTO_DOMAIN_SUFFIXES:
        if hostname == suffix or hostname.endswith("." + suffix):
            return "." + suffix
    return ""


def cookie_secure_for_host(hostname: str) -> bool:
    if AUTH_COOKIE_SECURE in {"1", "true", "yes", "on"}:
        return True
    if AUTH_COOKIE_SECURE in {"0", "false", "no", "off"}:
        return False
    return bool(cookie_domain_for_host(hostname))


def session_cookie(handler: BaseHTTPRequestHandler, value: str, max_age: int) -> str:
    hostname = request_hostname(handler)
    parts = [
        f"demo_auth_sid={value}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    domain = cookie_domain_for_host(hostname)
    if domain:
        parts.append(f"Domain={domain}")
    if cookie_secure_for_host(hostname):
        parts.append("Secure")
    return "; ".join(parts)


def build_redirect_with_token(redirect_uri: str, token: str) -> str:
    separator = "&" if "#" in redirect_uri else "#"
    return f"{redirect_uri}{separator}access_token={token}&token_type=Bearer&expires_in={TTL_SECONDS}"


def build_redirect_with_logout(redirect_uri: str) -> str:
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}logged_out=1"


def private_key_path(kid: str) -> Path:
    return KEYS_DIR / f"private-{kid}.pem"


def public_key_path(kid: str) -> Path:
    return KEYS_DIR / f"public-{kid}.pem"


def get_private_key(kid: str):
    key = PRIVATE_KEYS.get(kid)
    if key is None:
        key = load_pem_private_key(private_key_path(kid).read_bytes(), password=None)
        PRIVATE_KEYS[kid] = key
    return key


def get_public_key(kid: str):
    key = PUBLIC_KEYS.get(kid)
    if key is None:
        key = load_pem_public_key(public_key_path(kid).read_bytes())
        PUBLIC_KEYS[kid] = key
    return key


def sign_jwt(claims: dict) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": ACTIVE_KEY_ID}
    encoded_header = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = get_private_key(ACTIVE_KEY_ID).sign(signing_input)
    return f"{encoded_header}.{encoded_payload}.{b64url_encode(signature)}"


def issue_access_token(user_id: str, sid: str) -> str:
    now = int(time.time())
    ACTIVE_SESSIONS[sid] = {
        "user_id": user_id,
        "created_at": ACTIVE_SESSIONS.get(sid, {}).get("created_at", now),
        "last_login_at": now,
    }
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": user_id,
        "sid": sid,
        "iat": now,
        "exp": now + TTL_SECONDS,
    }
    return sign_jwt(claims)


def verify_jwt(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed_token")

    header = json.loads(b64url_decode(parts[0]))
    if header.get("alg") != "EdDSA":
        raise ValueError("bad_alg")
    kid = header.get("kid")
    if kid not in ALL_JWKS_BY_KID:
        raise ValueError("bad_kid")

    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    signature = b64url_decode(parts[2])
    try:
        get_public_key(kid).verify(signature, signing_input)
    except InvalidSignature:
        raise ValueError("bad_signature")

    claims = json.loads(b64url_decode(parts[1]))
    now = int(time.time())
    if claims.get("iss") != ISSUER:
        raise ValueError("bad_issuer")
    if claims.get("aud") != AUDIENCE:
        raise ValueError("bad_audience")
    if int(claims.get("exp", 0)) <= now:
        raise ValueError("expired")
    if not claims.get("sub"):
        raise ValueError("missing_subject")
    return claims


def read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length))


def extract_bearer_token(handler: BaseHTTPRequestHandler) -> str | None:
    authorization = handler.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "tabshare-auth-service-poc"

    def log_message(self, fmt: str, *args) -> None:
        print(json.dumps({
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "http_access",
            "remote": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "message": fmt % args,
        }))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            redirect_response(self, "/login/page")
            return
        if path == "/login/page":
            self.handle_login_page()
            return
        if path == "/logout":
            self.handle_logout()
            return
        if path == "/healthz":
            json_response(self, HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/.well-known/jwks.json":
            self.handle_jwks()
            return
        if path == "/internal/verify":
            self.handle_verify()
            return
        if path == "/internal/status":
            self.handle_status()
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/login":
            self.handle_login()
            return
        if path == "/logout":
            self.handle_logout()
            return
        if path == "/internal/verify":
            self.handle_verify()
            return
        if path == "/internal/rotate":
            self.handle_rotate()
            return
        if path == "/internal/reset-rotation":
            self.handle_reset_rotation()
            return
        if path == "/internal/session/introspect":
            self.handle_session_introspect()
            return
        if path == "/internal/session/resolve":
            self.handle_session_resolve()
            return
        if path == "/internal/session/revoke":
            self.handle_session_revoke()
            return
        if path == "/internal/session/reset":
            self.handle_session_reset()
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def handle_login_page(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        redirect_uri = query.get("redirect_uri", [""])[0]
        redirect_allowed = bool(redirect_uri) and is_allowed_redirect_uri(redirect_uri)
        existing_sid = cookie_value(self, "demo_auth_sid")
        existing_session = ACTIVE_SESSIONS.get(existing_sid)
        if redirect_allowed and existing_session:
            token = issue_access_token(existing_session["user_id"], existing_sid)
            redirect_response(self, build_redirect_with_token(redirect_uri, token))
            return

        config = {
            "brandName": LOGIN_BRAND_NAME,
            "primaryColor": LOGIN_PRIMARY_COLOR,
            "redirectUri": redirect_uri if redirect_allowed else "",
            "defaultEmail": DEMO_USER_EMAIL,
        }
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(LOGIN_BRAND_NAME)} Login</title>
  <style>
    :root {{ color-scheme: light; --primary: {escape(LOGIN_PRIMARY_COLOR)}; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f8fa; color: #1f2328; display: grid; place-items: center; }}
    main {{ width: min(420px, calc(100vw - 32px)); background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 28px; box-shadow: 0 16px 48px rgba(31, 35, 40, 0.08); }}
    h1 {{ margin: 0 0 6px; font-size: 24px; line-height: 1.2; }}
    p {{ margin: 0 0 22px; color: #57606a; line-height: 1.5; }}
    label {{ display: block; font-size: 13px; font-weight: 600; margin: 14px 0 6px; }}
    input {{ width: 100%; height: 40px; border: 1px solid #d0d7de; border-radius: 6px; padding: 8px 10px; font: inherit; }}
    button {{ width: 100%; height: 42px; margin-top: 18px; border: 0; border-radius: 6px; background: var(--primary); color: #fff; font-weight: 700; cursor: pointer; }}
    button:disabled {{ opacity: .6; cursor: wait; }}
    .error {{ min-height: 20px; margin-top: 12px; color: #cf222e; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(LOGIN_BRAND_NAME)}</h1>
    <p>统一登录入口</p>
    <form id="login-form">
      <label for="email">邮箱</label>
      <input id="email" name="email" autocomplete="username" value="{escape(DEMO_USER_EMAIL)}">
      <label for="password">密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" value="demo-password">
      <button id="submit" type="submit">登录</button>
      <div id="error" class="error"></div>
    </form>
  </main>
  <script>
    const config = {json.dumps(config, ensure_ascii=False)};
    const form = document.querySelector("#login-form");
    const button = document.querySelector("#submit");
    const error = document.querySelector("#error");
    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      error.textContent = "";
      if (!config.redirectUri) {{
        error.textContent = "redirect_uri 不在允许列表中";
        return;
      }}
      button.disabled = true;
      try {{
        const response = await fetch("/login", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            email: form.email.value,
            password: form.password.value
          }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.error || "login_failed");
        }}
        const separator = config.redirectUri.includes("#") ? "&" : "#";
        location.href = config.redirectUri + separator
          + "access_token=" + encodeURIComponent(payload.access_token)
          + "&token_type=Bearer&expires_in=" + encodeURIComponent(payload.expires_in);
      }} catch (err) {{
        error.textContent = "登录失败";
      }} finally {{
        button.disabled = false;
      }}
    }});
  </script>
</body>
</html>"""
        html_response(self, HTTPStatus.OK, body)

    def handle_logout(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        redirect_uri = query.get("redirect_uri", [""])[0]
        redirect_allowed = bool(redirect_uri) and is_allowed_redirect_uri(redirect_uri)
        sid = cookie_value(self, "demo_auth_sid")
        if sid:
            ACTIVE_SESSIONS.pop(sid, None)
            print(json.dumps({"event": "auth.logout.succeeded", "sid": sid}))

        headers = {
            "Set-Cookie": session_cookie(self, "", 0),
        }
        if redirect_allowed:
            redirect_response(self, build_redirect_with_logout(redirect_uri), headers=headers)
            return

        json_response(self, HTTPStatus.OK, {"logged_out": True}, headers=headers)

    def handle_login(self) -> None:
        try:
            body = read_json(self)
        except Exception:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), PASSWORD_SALT, 120_000)
        if email != DEMO_USER_EMAIL or not hmac.compare_digest(candidate, PASSWORD_HASH):
            print(json.dumps({"event": "auth.login.failed", "email": email}))
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "invalid_credentials"})
            return

        sid = f"demo-session-{DEMO_USER_ID}"
        token = issue_access_token(DEMO_USER_ID, sid)
        print(json.dumps({"event": "auth.login.succeeded", "user_id": DEMO_USER_ID}))
        json_response(self, HTTPStatus.OK, {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": TTL_SECONDS,
        }, headers={
            "Set-Cookie": session_cookie(self, sid, TTL_SECONDS),
        })

    def handle_session_resolve(self) -> None:
        sid = self.headers.get("X-Internal-Auth-Session", "")
        session = ACTIVE_SESSIONS.get(sid)
        if not session:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"active": False})
            return

        json_response(self, HTTPStatus.OK, {
            "active": True,
            "user_id": session["user_id"],
            "sid": sid,
        })

    def handle_verify(self) -> None:
        token = extract_bearer_token(self)
        if not token:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "missing_bearer_token"})
            return

        try:
            claims = verify_jwt(token)
        except Exception as exc:
            print(json.dumps({"event": "auth.token.rejected", "reason": str(exc)}))
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "invalid_token"})
            return

        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("X-Verified-User-Id", claims["sub"])
        self.end_headers()

    def handle_jwks(self) -> None:
        keys = [
            ALL_JWKS_BY_KID[kid]
            for kid in sorted(PUBLISHED_KEY_IDS)
            if kid in ALL_JWKS_BY_KID
        ]
        json_response(self, HTTPStatus.OK, {"keys": keys})

    def handle_status(self) -> None:
        json_response(self, HTTPStatus.OK, {
            "active_kid": ACTIVE_KEY_ID,
            "published_kids": sorted(PUBLISHED_KEY_IDS),
        })

    def handle_rotate(self) -> None:
        global ACTIVE_KEY_ID

        try:
            body = read_json(self)
        except Exception:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        kid = str(body.get("kid", "")).strip()
        if kid not in ALL_JWKS_BY_KID:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "unknown_kid"})
            return

        ACTIVE_KEY_ID = kid
        PUBLISHED_KEY_IDS.add(kid)
        print(json.dumps({"event": "auth.key.rotated", "active_kid": ACTIVE_KEY_ID}))
        self.handle_status()

    def handle_reset_rotation(self) -> None:
        global ACTIVE_KEY_ID

        ACTIVE_KEY_ID = KEY_ID
        PUBLISHED_KEY_IDS.clear()
        PUBLISHED_KEY_IDS.add(KEY_ID)
        print(json.dumps({"event": "auth.key_rotation.reset", "active_kid": ACTIVE_KEY_ID}))
        self.handle_status()

    def handle_session_introspect(self) -> None:
        subject = str(self.headers.get("X-Internal-Auth-Subject", "")).strip()
        sid = str(self.headers.get("X-Internal-Auth-Session", "")).strip()
        if not subject or not sid:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "missing_internal_identity"})
            return

        session = ACTIVE_SESSIONS.get(sid)
        if not session:
            json_response(self, HTTPStatus.UNAUTHORIZED, {
                "error": "inactive_session",
                "sid": sid,
            })
            return
        if session.get("user_id") != subject:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "session_subject_mismatch"})
            return

        json_response(self, HTTPStatus.OK, {
            "active": True,
            "sid": sid,
            "sub": subject,
        })

    def handle_session_revoke(self) -> None:
        try:
            body = read_json(self)
        except Exception:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        sid = str(body.get("sid", f"demo-session-{DEMO_USER_ID}")).strip()
        ACTIVE_SESSIONS.pop(sid, None)
        print(json.dumps({"event": "auth.session.revoked", "sid": sid}))
        json_response(self, HTTPStatus.OK, {"revoked": True, "sid": sid})

    def handle_session_reset(self) -> None:
        now = int(time.time())
        sid = f"demo-session-{DEMO_USER_ID}"
        ACTIVE_SESSIONS.clear()
        ACTIVE_SESSIONS[sid] = {
            "user_id": DEMO_USER_ID,
            "created_at": now,
            "last_login_at": now,
        }
        print(json.dumps({"event": "auth.session.reset", "sid": sid}))
        json_response(self, HTTPStatus.OK, {"active": True, "sid": sid})


class DemoHTTPServer(ThreadingHTTPServer):
    request_queue_size = 1024
    daemon_threads = True


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    DemoHTTPServer(("0.0.0.0", port), Handler).serve_forever()
