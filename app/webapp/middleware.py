"""Bearer-token / loopback auth middleware for the photo-ocr webapp."""

from __future__ import annotations

# Standard library imports
import hmac

# Third-party imports
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Loopback addresses bypass the bearer-token gate so local probes keep
# working without carrying the token. Tunnel traffic arrives with a
# non-loopback client IP and must present the token.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Endpoints that must remain reachable without the token: liveness
# probes, the iOS profile install, the page boot (so the JS can pick
# up the token from ?token= and attach it to subsequent calls),
# /api/login so a device with no token can swap a password for the
# bearer token, and /api/version so the build line renders before the
# user has authenticated.
_AUTH_EXEMPT_PREFIXES = ("/static/", "/healthz", "/install-ca")
_AUTH_EXEMPT_EXACT = frozenset(
    {"/", "/healthz", "/install-ca", "/api/login", "/api/version"}
)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <token> on API endpoints.

    Behaviour matches voice-transcriber's gate:

    - Empty configured token → short-circuit, no gate.
    - Loopback callers bypass.
    - `/`, `/static/*`, `/healthz`, `/install-ca`, `/api/login`,
      `/api/version` exempt.
    - Otherwise accept token from `Authorization: Bearer …` header or
      `?token=…` query string.
    """

    def __init__(self, app, get_token):
        super().__init__(app)
        self._get_token = get_token

    async def dispatch(self, request: Request, call_next):
        token = (self._get_token() or "").strip()
        if not token:
            return await call_next(request)

        client_host = request.client.host if request.client else ""
        if client_host in _LOOPBACK_HOSTS:
            return await call_next(request)

        path = request.url.path
        if path in _AUTH_EXEMPT_EXACT or any(
            path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES
        ):
            return await call_next(request)

        presented = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
        if not presented:
            presented = request.query_params.get("token", "").strip()

        if presented and hmac.compare_digest(presented, token):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="photo-ocr"'},
        )
