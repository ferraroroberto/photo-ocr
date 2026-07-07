"""Unauthenticated entry points: page boot, liveness probe, and the
build-identity endpoint."""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

# Local imports
from app.webapp.routers._helpers import BUILD_INFO, STATIC_DIR

router = APIRouter()


@router.get("/")
async def index() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html missing")
    # Stamp the asset URLs with the build fleet hash and force the entry
    # document to revalidate, so a tray restart after an edit is always
    # picked up — no stale iOS PWA cache.
    html = BUILD_INFO.stamp_html(index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "service": "photo-ocr-webapp"}


@router.get("/api/version")
async def version() -> Dict[str, str]:
    """Build identity so the phone (and tests) can confirm which build
    is loaded — see issue #5."""
    return BUILD_INFO.as_dict()
