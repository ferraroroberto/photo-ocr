"""Shared paths, build identity, and tiny request helpers used by more
than one router module."""

from __future__ import annotations

# Standard library imports
from pathlib import Path
from typing import Any, Dict

# Third-party imports
from fastapi import Request

# Local imports
from src.static_versioning import BuildInfo

# app/webapp/routers/_helpers.py → parents[3] is the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

# Build identity, computed once at import — the tray restarts on every
# code edit, so a fresh process always reflects the deployed code.
BUILD_INFO = BuildInfo(STATIC_DIR, PROJECT_ROOT)


async def maybe_json(request: Request) -> Dict[str, Any]:
    """Best-effort JSON body — an empty dict for non-JSON or malformed
    requests, so optional-body endpoints stay tolerant."""
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}
