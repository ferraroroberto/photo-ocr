"""FastAPI webapp — mobile-first photo OCR.

Routes (split across ``app/webapp/routers/``):

    GET    /                                  → static/index.html       (misc)
    GET    /static/{file}                     → CSS / JS / icons         (static mount)
    GET    /healthz                           → liveness probe           (misc)
    GET    /install-ca                        → iOS .mobileconfig        (misc)
    GET    /api/version                       → build identity           (misc)

    GET    /api/config                        → config + prompts + models(config)
    POST   /api/config                        → patch + persist          (config)
    GET    /api/status                        → llm_hub reachability     (config)
    POST   /api/login                         → password → bearer token  (auth)

    POST   /api/sessions                      → create new session       (sessions)
    POST   /api/sessions/{id}/photos          → multipart upload, 1..N    (sessions)
    DELETE /api/sessions/{id}/photos/{seq}    → drop a photo              (sessions)
    POST   /api/sessions/{id}/extract         → run OCR on all photos     (sessions)
    POST   /api/sessions/{id}/redo            → re-run OCR                (sessions)
    GET    /api/sessions/{id}/photo/{seq}     → serve a stored photo      (sessions)
    GET    /api/sessions/{id}/text            → full extracted text       (sessions)
    GET    /api/sessions                      → list (newest first)       (sessions)
    DELETE /api/sessions                      → cleanup all               (sessions)
    DELETE /api/sessions/{id}                 → delete one                (sessions)
    DELETE /api/sessions/older-than/{days}    → cleanup old               (sessions)

The lifespan hook prunes sessions older than the configured retention
window on every boot.
"""

from __future__ import annotations

# Standard library imports
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

# Third-party imports
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

# Local imports
from app.webapp.middleware import BearerTokenMiddleware
from app.webapp.routers import auth, config, misc, sessions
from app.webapp.routers._helpers import BUILD_INFO, STATIC_DIR
from src.app_config import load_app_config
from src.archive import SessionArchive
from src.ocr_client import OcrClient
from src.webapp_config import WebappConfig, load_webapp_config

logger = logging.getLogger(__name__)

# Hash-stamped assets get a one-year immutable cache: the fleet hash in
# the query string makes the URL change on every edit, so a stale copy
# can never be served. Icons + manifest revalidate daily — they almost
# never change but we don't want a year of staleness either.
_LONG_CACHE = "public, max-age=31536000, immutable"
_DAY_CACHE = "public, max-age=86400"
_IMMUTABLE_SUFFIXES = frozenset({".js", ".css"})
_DAILY_SUFFIXES = frozenset({".webmanifest", ".png", ".ico"})


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` with per-file ``Cache-Control`` + JS-import stamping.

    Starlette's mount serves every file with only ``ETag`` /
    ``Last-Modified``, leaving iOS Safari free to heuristic-cache. This
    subclass stamps an explicit policy keyed on the suffix, and rewrites
    each served ``.js`` module's relative ``import`` URLs to carry the
    build fleet hash — so an edit to any module busts the whole graph.
    """

    def file_response(
        self,
        full_path: "os.PathLike[str]",
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        path = Path(full_path)
        suffix = path.suffix.lower()

        if suffix == ".js":
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                return super().file_response(
                    full_path, stat_result, scope, status_code
                )
            media_type, _ = mimetypes.guess_type(str(path))
            return Response(
                content=BUILD_INFO.stamp_js(body),
                status_code=status_code,
                media_type=media_type or "text/javascript",
                headers={"Cache-Control": _LONG_CACHE},
            )

        response = super().file_response(
            full_path, stat_result, scope, status_code
        )
        if suffix in _IMMUTABLE_SUFFIXES:
            response.headers["Cache-Control"] = _LONG_CACHE
        elif suffix in _DAILY_SUFFIXES:
            response.headers["Cache-Control"] = _DAY_CACHE
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: prune archive older than retention window. Shutdown: close clients."""
    cfg: WebappConfig = app.state.webapp_config
    archive: SessionArchive = app.state.archive
    try:
        removed = archive.cleanup_older_than(cfg.history_retention_days)
        if removed:
            logger.info(f"🧹 Pruned {removed} old sessions on boot")
    except Exception as exc:  # noqa: BLE001 — never block startup
        logger.warning(f"⚠️  Archive prune failed: {exc}")

    yield

    try:
        app.state.ocr_client.close()
    except Exception:
        pass


def create_app() -> FastAPI:
    """Build the FastAPI app — wired with all dependencies."""
    app_config = load_app_config()
    webapp_cfg = load_webapp_config()
    archive = SessionArchive()
    ocr_client = OcrClient(webapp_cfg.llm_hub_url)

    auth.ensure_auth_log_handler()

    app = FastAPI(
        title="Photo OCR",
        version="0.1.0",
        lifespan=_lifespan,
    )

    # Read the token from app.state on every request so a /api/config
    # patch that rotates it takes effect without a restart.
    app.add_middleware(
        BearerTokenMiddleware,
        get_token=lambda: getattr(app.state.webapp_config, "auth_token", ""),
    )

    app.state.app_config = app_config
    app.state.webapp_config = webapp_cfg
    app.state.archive = archive
    app.state.ocr_client = ocr_client

    if STATIC_DIR.exists():
        app.mount(
            "/static",
            CachingStaticFiles(directory=str(STATIC_DIR)),
            name="static",
        )

    logger.info(
        f"ℹ️  webapp build {BUILD_INFO.git_sha} "
        f"(fleet {BUILD_INFO.fleet_hash}) built {BUILD_INFO.built_at}"
    )

    app.include_router(misc.router)
    app.include_router(config.router)
    app.include_router(auth.router)
    app.include_router(sessions.router)

    return app


# Module-level app for `uvicorn app.webapp.server:app`.
app = create_app()
