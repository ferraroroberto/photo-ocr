"""Full-text search over the session archive (SQLite FTS5)."""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, Request

# Local imports
from src.archive import SessionArchive
from src.webapp_config import WebappConfig

router = APIRouter()


@router.get("/api/search")
async def search_archive(
    request: Request, q: str = "", limit: int = 20,
) -> Dict[str, Any]:
    """Ranked FTS5 search over indexed sessions.

    Returns an empty result set (with ``enabled: false``) when the
    feature flag is off, so the frontend can hide the search box
    without a separate capability probe.
    """
    cfg: WebappConfig = request.app.state.webapp_config
    if not cfg.search_enabled:
        return {"query": q, "enabled": False, "results": []}
    archive: SessionArchive = request.app.state.archive
    if limit < 1:
        limit = 20
    results = archive.search(q, limit=limit)
    return {"query": q, "enabled": True, "results": results}
