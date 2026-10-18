"""SIM Bridge Render-compatible WebSocket relay (integration prototype).

Unlike the original Google Cloud TCP splicer, this service terminates each WebSocket
session at the Render HTTPS edge. Payloads MUST be application-layer encrypted by
clients before production use. The existing Redmi ReverseTunnel TCP client and
Safari/Swift clients are NOT wire-compatible with this prototype.
"""
import asyncio
import hmac
import json
import logging
import os
import time
from collections import defaultdict, deque

from aiohttp import WSMsgType, web

LOG = logging.getLogger("simbridge-relay")
LOGGING_FORMAT = "%(asctime)s %(levelname)s %(message)s"
MAX_FRAME_BYTES = 128 * 1024
AUTH_TIMEOUT_SECONDS = 8
SEND_TIMEOUT_SECONDS = 2
PER_IP_WINDOW = 60
PER_IP_MAX = 24


def token_ok(provided, expected):
    return isinstance(provided, str) and hmac.compare_digest(provided.encode(), expected.encode())


class Relay:
    def __init__(self, gateway_key: str, iphone_key: str, allowed_origins=""):
        if any(len(s) < 32 for s in (gateway_key, iphone_key)):
            raise ValueError("BRIDGE_GATEWAY_KEY and BRIDGE_IPHONE_KEY must each have at least 32 characters")
        if gateway_key == iphone_key:
            raise ValueError("Gateway and iPhone must have different authentication keys")
        self.keys = {"gateway": gateway_key, "iphone": iphone_key}
        self.allowed_origins = set(filter(None, (s.strip() for s in allowed_origins.split(","))))
        self.peers = {"gateway": None, "iphone": None}
        self.join_lock = asyncio.Lock()
        self.ip_attempts = defaultdict(deque)
        self.start_time = time.monotonic()

    async def page(self, request):
        from pathlib import Path
        html = Path(__file__).with_name('index.html').read_text(encoding='utf-8')
        return web.Response(text=html, content_type='text/html', headers={'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer', 'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; media-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

    async def health(self, request):
        # Avoid publishing live call status or any connection identifiers.
        return web.json_response({"service": "simbridge-relay", "status": "ok", "protocol": "ws-v1-e2ee"})

    async def socket(self, request):
        role = request.match_info.get("role")
        if role not in self.keys:
            return web.Response(status=404)
        origin = request.headers.get("Origin", "")
        if origin and self.allowed_origins and origin not in self.allowed_origins:
            return web.Response(status=403, text="Origin not allowed")
        ip = request.remote or "unknown"
        times = self.ip_attempts[ip]
        now = time.monotonic()
        while times and now - times[0] > PER_IP_WINDOW:
            times.popleft()
        if len(times) >= PER_IP_MAX:
            return web.Response(status=429, text="Too many connection attempts")
        times.append(now)

        # The WebSocket handshake is HTTP-based; Render terminates outer TLS.
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME_BYTES)
        await ws.prepare(request)
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=AUTH_TIMEOUT_SECONDS)
            if first.type != WSMsgType.TEXT:
                await ws.close(code=1008, message=b"Authentication required")
                return ws
            try:
                auth = json.loads(first.data)
            except (ValueError, TypeError):
                auth = None
            if not isinstance(auth, dict) or auth.get("type") != "auth" or not token_ok(auth.get("token"), self.keys[role]):
                await ws.close(code=1008, message=b"Invalid credentials")
                LOG.warning("authentication rejected for role=%s", role)
                return ws
            async with self.join_lock:
                if self.peers[role] is not None and not self.peers[role].closed:
                    await ws.close(code=1008, message=b"Role already connected")
                    return ws
                self.peers[role] = ws
            LOG.info("connected role=%s", role)
            await ws.send_json({"type": "ready", "role": role, "protocol": "ws-v1-e2ee"})
            other_role = "iphone" if role == "gateway" else "gateway"
            other = self.peers[other_role]
            await ws.send_json({"type": "peer", "online": other is not None and not other.closed})
            if other is not None and not other.closed:
                await other.send_json({"type": "peer", "online": True})
            async for msg in ws:
                if msg.type not in (WSMsgType.TEXT, WSMsgType.BINARY):
                    continue
                peer = self.peers[other_role]
                if peer is None or peer.closed:
                    # Never buffer stale audio when the other party is offline.
                    continue
                try:
                    # Backpressure timeouts prevent minutes of stale audio accumulating.
                    if msg.type == WSMsgType.TEXT:
                        await asyncio.wait_for(peer.send_str(msg.data), timeout=SEND_TIMEOUT_SECONDS)
                    else:
                        await asyncio.wait_for(peer.send_bytes(msg.data), timeout=SEND_TIMEOUT_SECONDS)
                except (asyncio.TimeoutError, ConnectionError, RuntimeError):
                    await peer.close(code=1011, message=b"Relay send stalled")
                    LOG.warning("closed stalled peer role=%s", other_role)
        except asyncio.TimeoutError:
            await ws.close(code=1008, message=b"Auth timeout")
        except (ConnectionError, RuntimeError):
            LOG.info("connection closed role=%s", role)
        finally:
            async with self.join_lock:
                if self.peers[role] is ws:
                    self.peers[role] = None
                    other_role = "iphone" if role == "gateway" else "gateway"
                    peer = self.peers[other_role]
                else:
                    peer = None
            if peer is not None and not peer.closed:
                try:
                    await peer.send_json({"type": "peer", "online": False})
                except (ConnectionError, RuntimeError):
                    pass
            await ws.close()
            LOG.info("disconnected role=%s", role)
        return ws


def make_app(gateway_key=None, iphone_key=None, allowed_origins=None):
    gateway_key = gateway_key or os.environ.get("BRIDGE_GATEWAY_KEY", "")
    iphone_key = iphone_key or os.environ.get("BRIDGE_IPHONE_KEY", "")
    allowed_origins = allowed_origins if allowed_origins is not None else os.environ.get("ALLOWED_ORIGINS", "")
    relay = Relay(gateway_key, iphone_key, allowed_origins)
    app = web.Application(client_max_size=MAX_FRAME_BYTES)
    app.router.add_get("/", relay.page)
    app.router.add_get("/healthz", relay.health)
    app.router.add_get("/ws/{role}", relay.socket)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)
    app = make_app()
    port = int(os.getenv("PORT", "10000"))
    LOG.info("starting SIM Bridge Render relay on %s", port)
    web.run_app(app, host="0.0.0.0", port=port, access_log=None)
