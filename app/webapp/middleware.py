"""Bearer-token / loopback auth middleware for the photo-ocr webapp."""

from __future__ import annotations

# Standard library imports
import hmac
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
