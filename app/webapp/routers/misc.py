"""Unauthenticated entry points: page boot, liveness probe, and the
build-identity endpoint."""

from __future__ import annotations

# Standard library imports
import hashlib
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

# Local imports
from app.webapp.routers._helpers import BUILD_INFO, STATIC_DIR

router = APIRouter()

_INDEX_CACHE_CONTROL = "no-cache, must-revalidate"


def _entry_etag(html: str) -> str:
    """Weak validator for the stamped entry document.

    Weak because the bytes on the wire vary with ``Content-Encoding``.
    Derived from the stamped body *and* the build fingerprint, so any edit
    or new build changes it.
    """
    digest = hashlib.sha256(BUILD_INFO.fingerprint().encode("utf-8"))
    digest.update(html.encode("utf-8"))
    return f'W/"{digest.hexdigest()[:20]}"'


def _if_none_match_hits(header: str, etag: str) -> bool:
    """RFC 9110 weak comparison of ``If-None-Match`` against ``etag``."""
    header = header.strip()
    if not header:
        return False
    if header == "*":
        return True
    ours = etag.removeprefix("W/")
    return any(
        candidate.strip().removeprefix("W/") == ours
        for candidate in header.split(",")
    )


@router.get("/")
async def index(request: Request) -> Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html missing")
    # Stamp the asset URLs with the build fleet hash and force the entry
    # document to revalidate, so a tray restart after an edit is always
    # picked up — no stale iOS PWA cache. The ETag lets that revalidation
    # answer 304 with no body when nothing changed.
    html = BUILD_INFO.stamp_html(index_path.read_text(encoding="utf-8"))
    headers = {
        "Cache-Control": _INDEX_CACHE_CONTROL,
        "ETag": _entry_etag(html),
    }
    if _if_none_match_hits(request.headers.get("if-none-match", ""), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return HTMLResponse(html, headers=headers)


@router.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "service": "photo-ocr-webapp"}


@router.get("/api/version")
async def version() -> Dict[str, str]:
    """Build identity so the phone (and tests) can confirm which build
    is loaded — see issue #5."""
    return BUILD_INFO.as_dict()
