"""Bearer-token / loopback auth middleware for the photo-ocr webapp."""

from __future__ import annotations

# Standard library imports
import hmac
import ipaddress
from typing import Callable

# Third-party imports
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Loopback addresses bypass the bearer-token gate so local probes keep
# working without carrying the token. Tunnel traffic arrives with a
# non-loopback client IP and must present the token.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Endpoints that must remain reachable without the token: liveness
# probes, the page boot (so the JS can pick up the token from ?token=
# and attach it to subsequent calls), /api/login so a device with no
# token can swap a password for the bearer token, and /api/version so
# the build line renders before the user has authenticated.
_AUTH_EXEMPT_PREFIXES = ("/static/", "/healthz")
_AUTH_EXEMPT_EXACT = frozenset(
    {"/", "/healthz", "/api/login", "/api/version"}
)


# cloudflared connects to this app from loopback and names the real caller in
# this header, so it is the only client address a tunnelled request has.
_TUNNEL_CLIENT_HEADER = "cf-connecting-ip"
# Shared bucket for a tunnelled request whose header is not an IP address.
_UNPARSEABLE_TUNNEL_CLIENT = "tunnel-unknown"


def _socket_host(request: Request) -> str:
    return request.client.host if request.client else ""


def _tunnel_client(request: Request) -> str | None:
    """The real caller behind the tunnel, or None when this is not tunnel traffic.

    The header is believed only on a loopback socket: from any other peer it is
    just a header the caller chose.
    """
    if _socket_host(request) not in _LOOPBACK_HOSTS:
        return None
    raw = request.headers.get(_TUNNEL_CLIENT_HEADER)
    if raw is None:
        return None
    try:
        return ipaddress.ip_address(raw.strip()).compressed
    except ValueError:
        return _UNPARSEABLE_TUNNEL_CLIENT


def client_address(request: Request) -> str:
    """The address login attempts are keyed and logged on: the tunnel's
    client when there is one, else the socket peer."""
    return _tunnel_client(request) or _socket_host(request) or "?"


def is_pc_itself(request: Request) -> bool:
    """True for a caller on this machine, not one the tunnel forwarded.

    A loopback socket alone proves nothing: every public request through the
    tunnel arrives that way, so it also must not carry the tunnel's client header.
    """
    return _socket_host(request) in _LOOPBACK_HOSTS and (
        request.headers.get(_TUNNEL_CLIENT_HEADER) is None
    )


class BearerTokenMiddleware:
    """Require Authorization: Bearer <token> on API endpoints.

    Behaviour matches voice-transcriber's gate:

    - Empty configured token → short-circuit, no gate.
    - Loopback callers bypass.
    - `/`, `/static/*`, `/healthz`, `/api/login`, `/api/version` exempt.
    - Otherwise accept token from `Authorization: Bearer …` header or
      `?token=…` query string.

    A plain ASGI middleware, not ``BaseHTTPMiddleware``: that base class
    re-streams every body in chunks, which hides each response's real size
    from the outer compression layer and defeats its minimum-size rule.
    """

    def __init__(self, app: ASGIApp, get_token: Callable[[], str]) -> None:
        self.app = app
        self._get_token = get_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if self._is_allowed(request):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="photo-ocr"'},
        )
        await response(scope, receive, send)

    def _is_allowed(self, request: Request) -> bool:
        token = (self._get_token() or "").strip()
        if not token:
            return True

        client_host = request.client.host if request.client else ""
        if client_host in _LOOPBACK_HOSTS:
            return True

        path = request.url.path
        if path in _AUTH_EXEMPT_EXACT or any(
            path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES
        ):
            return True

        presented = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
        if not presented:
            presented = request.query_params.get("token", "").strip()

        return bool(presented) and hmac.compare_digest(presented, token)
