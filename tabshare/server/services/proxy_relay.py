"""
本地代理转发器 - 解决 Playwright 407 重试与某些代理不兼容的问题。

Playwright 的代理认证方式：先发不带认证的 CONNECT → 收到 407 → 再重试带认证。
某些代理服务器会因此标记连接为"可疑"并拒绝服务。

方案：在本地启动一个透明 TCP 代理，预注入 Proxy-Authorization 头，
让 Chrome 连接本地代理（无需认证），由本地代理转发到上游代理（带认证）。
"""

import asyncio
import base64
from urllib.parse import urlparse

_relays: dict[str, "ProxyRelay"] = {}
_next_port = 13100  # 本地代理起始端口


class ProxyRelay:
    """为单个上游代理创建一个本地透明转发器"""

    def __init__(self, upstream_host: str, upstream_port: int, username: str, password: str):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.auth_header = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.server: asyncio.AbstractServer | None = None
        self.local_port: int = 0

    async def start(self, port: int):
        self.local_port = port
        self.server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", port
        )
        print(f"[ProxyRelay] 127.0.0.1:{port} → {self.upstream_host}:{self.upstream_port}")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        up_reader = up_writer = None
        try:
            # 读取请求头（CONNECT xxx:443 HTTP/1.1\r\n...）
            first_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not first_line:
                writer.close()
                return

            headers = [first_line]
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if line in (b"\r\n", b"\n", b""):
                    break
                # 过滤掉客户端可能发的 Proxy-Authorization
                if not line.lower().startswith(b"proxy-authorization:"):
                    headers.append(line)

            # 连接上游代理
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self.upstream_host, self.upstream_port),
                timeout=10,
            )

            # 发送请求头 + 预注入认证
            for h in headers:
                up_writer.write(h)
            up_writer.write(f"Proxy-Authorization: Basic {self.auth_header}\r\n".encode())
            up_writer.write(b"\r\n")
            await up_writer.drain()

            # 双向转发
            async def pipe(r, w):
                try:
                    while True:
                        data = await r.read(65536)
                        if not data:
                            break
                        w.write(data)
                        await w.drain()
                except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                    pass

            await asyncio.gather(pipe(up_reader, writer), pipe(reader, up_writer))

        except Exception:
            pass
        finally:
            for w in (writer, up_writer):
                if w:
                    try:
                        w.close()
                    except Exception:
                        pass

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            print(f"[ProxyRelay] Stopped 127.0.0.1:{self.local_port}")


async def get_or_create_relay(proxy_url: str) -> str:
    """
    解析带认证的代理 URL，返回本地无认证代理地址。
    如果代理不需要认证，直接返回原 URL。

    Args:
        proxy_url: 如 "http://user:pass@host:port"
    Returns:
        本地代理 URL: "http://127.0.0.1:{port}"
    """
    global _next_port

    parsed = urlparse(proxy_url)
    if not parsed.username:
        # 无认证，不需要 relay
        return proxy_url

    # 同一个上游代理复用同一个 relay
    relay_key = f"{parsed.hostname}:{parsed.port}:{parsed.username}"
    if relay_key in _relays:
        relay = _relays[relay_key]
        return f"http://127.0.0.1:{relay.local_port}"

    relay = ProxyRelay(
        upstream_host=parsed.hostname,
        upstream_port=parsed.port or 80,
        username=parsed.username,
        password=parsed.password or "",
    )

    port = _next_port
    _next_port += 1
    await relay.start(port)
    _relays[relay_key] = relay

    return f"http://127.0.0.1:{port}"


async def stop_all():
    """关闭所有 relay"""
    for relay in _relays.values():
        await relay.stop()
    _relays.clear()
