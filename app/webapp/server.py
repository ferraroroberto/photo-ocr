"""FastAPI webapp — mobile-first photo OCR.

Routes:

    GET    /                                  → static/index.html
    GET    /static/{file}                     → CSS / JS / icons / manifest
    GET    /healthz                           → liveness probe
    GET    /install-ca                        → iOS .mobileconfig

    GET    /api/config                        → current config + prompts + models
    POST   /api/config                        → patch + persist (whitelist)
    POST   /api/login                         → swap password for bearer token
    GET    /api/status                        → llm_hub reachability

    POST   /api/sessions                      → create new session
    POST   /api/sessions/{id}/photos          → multipart upload, 1..N files
    DELETE /api/sessions/{id}/photos/{seq}    → drop a photo before extract
    POST   /api/sessions/{id}/extract         → run OCR on all photos
    POST   /api/sessions/{id}/redo            → re-run OCR (saved photos)
    GET    /api/sessions/{id}/photo/{seq}     → serve a stored photo
    GET    /api/sessions/{id}/text            → full extracted text
    GET    /api/sessions                      → list (newest first, paginated)
    DELETE /api/sessions                      → cleanup all
    DELETE /api/sessions/{id}                 → delete one
    DELETE /api/sessions/older-than/{days}    → cleanup old

The lifespan hook prunes sessions older than the configured retention
window on every boot.
"""

from __future__ import annotations

# Standard library imports
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Third-party imports
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from src.app_config import load_app_config
from src.archive import PhotoMeta, Session, SessionArchive
from src.image_utils import ImageValidationError, validate_and_persist
from src.ocr_client import OcrClient, OcrError
from src.ocr_prompts import (
    OcrPrompt,
    get_prompt,
    load_ocr_prompts,
)
from src.webapp_config import (
    WebappConfig,
    load_webapp_config,
    update_webapp_config,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Loopback addresses bypass the bearer-token gate so local probes keep
# working without carrying the token. Tunnel traffic arrives with a
# non-loopback client IP and must present the token.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Endpoints that must remain reachable without the token: liveness
# probes, the iOS profile install, the page boot (so the JS can pick
# up the token from ?token= and attach it to subsequent calls), and
# /api/login so a device with no token can swap a password for the
# bearer token.
_AUTH_EXEMPT_PREFIXES = ("/static/", "/healthz", "/install-ca")
_AUTH_EXEMPT_EXACT = frozenset({"/", "/healthz", "/install-ca", "/api/login"})


# Dedicated logger for password attempts — written to webapp/auth.log
# in addition to the normal stderr stream so failed attempts are easy
# to find without scrolling through full server logs.
auth_logger = logging.getLogger("photo_ocr.auth")
_AUTH_LOG_PATH = PROJECT_ROOT / "webapp" / "auth.log"


def _ensure_auth_log_handler() -> None:
    if any(
        isinstance(h, logging.FileHandler)
        and Path(h.baseFilename).resolve() == _AUTH_LOG_PATH.resolve()
        for h in auth_logger.handlers
    ):
        return
    try:
        _AUTH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_AUTH_LOG_PATH, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        auth_logger.addHandler(fh)
        auth_logger.setLevel(logging.INFO)
    except OSError as exc:
        logger.warning(f"⚠️  Could not open {_AUTH_LOG_PATH}: {exc}")


_ensure_auth_log_handler()


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require Authorization: Bearer <token> on API endpoints.

    Behaviour matches voice-transcriber's gate:

    - Empty configured token → short-circuit, no gate.
    - Loopback callers bypass.
    - `/`, `/static/*`, `/healthz`, `/install-ca`, `/api/login` exempt.
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

    # ----------------------------------------------------- static routes

    if STATIC_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIR)),
            name="static",
        )

    @app.get("/")
    async def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=500, detail="index.html missing")
        return FileResponse(str(index_path))

    @app.get("/healthz")
    async def healthz() -> Dict[str, Any]:
        return {"ok": True, "service": "photo-ocr-webapp"}

    @app.get("/install-ca")
    async def install_ca() -> FileResponse:
        profile = STATIC_DIR / "photo-ocr-ca.mobileconfig"
        if not profile.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    "CA profile not generated yet. Run "
                    "`scripts/gen_ssl_cert.py` from the project root."
                ),
            )
        return FileResponse(
            str(profile),
            media_type="application/x-apple-aspen-config",
            filename="photo-ocr-ca.mobileconfig",
        )

    # ------------------------------------------------------ config API

    @app.get("/api/config")
    async def get_config(request: Request) -> Dict[str, Any]:
        cfg: WebappConfig = request.app.state.webapp_config
        prompts = load_ocr_prompts()
        return {
            "ocr_model_default": cfg.ocr_model_default,
            "ocr_models_available": cfg.ocr_models_available,
            "ocr_prompt_default": cfg.ocr_prompt_default,
            "ocr_prompts": [
                {
                    "id": p.id,
                    "label": p.label,
                    "description": p.description,
                    "system": p.system,
                }
                for p in prompts
            ],
            "history_retention_days": cfg.history_retention_days,
            "max_photos_per_session": cfg.max_photos_per_session,
            "max_photo_dimension_px": cfg.max_photo_dimension_px,
            "auth_password_set": bool(cfg.auth_password),
        }

    @app.post("/api/config")
    async def patch_config(request: Request) -> Dict[str, Any]:
        body = await request.json()
        allowed = {
            "ocr_model_default",
            "ocr_prompt_default",
            "history_retention_days",
            "max_photos_per_session",
        }
        patch = {k: v for k, v in body.items() if k in allowed}
        try:
            new_cfg = update_webapp_config(**patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        request.app.state.webapp_config = new_cfg
        return {"ok": True, "config": _config_dict(new_cfg)}

    @app.post("/api/login")
    async def login(request: Request) -> Dict[str, Any]:
        cfg: WebappConfig = request.app.state.webapp_config
        client_host = request.client.host if request.client else "?"
        if not cfg.auth_password:
            auth_logger.info(
                f"⚠️  Login attempt from {client_host} but no auth_password "
                "configured — password auth disabled"
            )
            raise HTTPException(
                status_code=503,
                detail="password auth not configured",
            )
        if not cfg.auth_token:
            auth_logger.info(
                f"⚠️  Login attempt from {client_host} but no auth_token "
                "configured — nothing to hand back"
            )
            raise HTTPException(
                status_code=503,
                detail="bearer token not configured",
            )
        body = await _maybe_json(request)
        presented = str(body.get("password") or "")
        if not presented or not hmac.compare_digest(presented, cfg.auth_password):
            auth_logger.warning(
                f"🚨 Failed password attempt from {client_host} "
                f"(presented: {len(presented)} chars)"
            )
            raise HTTPException(status_code=401, detail="bad password")
        auth_logger.info(f"🔓 Password login from {client_host}")
        return {"token": cfg.auth_token}

    @app.get("/api/status")
    async def status(request: Request) -> Dict[str, Any]:
        ocr: OcrClient = request.app.state.ocr_client
        return {
            "llm_hub": {
                "reachable": ocr.is_reachable(),
                "base_url": ocr.base_url,
            },
        }

    # ------------------------------------------------------ session API

    @app.post("/api/sessions")
    async def create_session(request: Request) -> Dict[str, Any]:
        body = await _maybe_json(request)
        archive: SessionArchive = request.app.state.archive
        incognito = bool(body.get("incognito", False))
        session = archive.new_session(incognito=incognito)
        return {
            "session_id": session.session_id,
            "folder": str(session.folder),
            "created_at": session.meta.created_at,
            "incognito": incognito,
        }

    @app.post("/api/sessions/{session_id}/photos")
    async def upload_photos(
        session_id: str,
        request: Request,
        files: List[UploadFile] = File(...),
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        cfg: WebappConfig = request.app.state.webapp_config
        session = archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        if not files:
            raise HTTPException(status_code=400, detail="no files in upload")

        if len(session.meta.photos) + len(files) > cfg.max_photos_per_session:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"session would exceed max_photos_per_session "
                    f"({cfg.max_photos_per_session}); has {len(session.meta.photos)}, "
                    f"upload adds {len(files)}"
                ),
            )

        persisted: List[Dict[str, Any]] = []
        for upload in files:
            raw = await upload.read()
            seq = session.next_sequence_index()
            try:
                p = validate_and_persist(
                    raw=raw,
                    content_type=upload.content_type or "",
                    dest_folder=session.folder,
                    sequence_index=seq,
                    max_dim_px=cfg.max_photo_dimension_px,
                )
            except ImageValidationError as exc:
                # Keep already-persisted photos in this batch — fail
                # this single file with a useful message.
                raise HTTPException(status_code=400, detail=str(exc))
            session.record_photo(
                PhotoMeta(
                    sequence_index=p.sequence_index,
                    path=p.path.name,
                    width=p.width,
                    height=p.height,
                    bytes_on_disk=p.bytes_on_disk,
                )
            )
            persisted.append(
                {
                    "sequence_index": p.sequence_index,
                    "path": p.path.name,
                    "width": p.width,
                    "height": p.height,
                    "bytes_on_disk": p.bytes_on_disk,
                }
            )
        session.write_meta()
        return {
            "session_id": session.session_id,
            "photos": [_photo_dict(pm) for pm in session.meta.photos],
            "added": persisted,
        }

    @app.delete("/api/sessions/{session_id}/photos/{sequence_index}")
    async def remove_photo(
        session_id: str, sequence_index: int, request: Request
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        session = archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        if session.meta.extract_succeeded is not None:
            # Allow but warn — the photos list won't match the
            # extracted.txt produced before deletion. The UI should
            # avoid this path, but it's harmless on the server.
            logger.info(
                f"ℹ️  removing photo from already-extracted session {session_id}"
            )
        if not session.remove_photo(sequence_index):
            raise HTTPException(
                status_code=404,
                detail=f"no photo with sequence_index={sequence_index} in session",
            )
        session.write_meta()
        return {
            "session_id": session.session_id,
            "photos": [_photo_dict(pm) for pm in session.meta.photos],
        }

    @app.post("/api/sessions/{session_id}/extract")
    async def extract_session(
        session_id: str, request: Request
    ) -> Dict[str, Any]:
        return await _run_extract(request, session_id, allow_when_done=False)

    @app.post("/api/sessions/{session_id}/redo")
    async def redo_session(
        session_id: str, request: Request
    ) -> Dict[str, Any]:
        return await _run_extract(request, session_id, allow_when_done=True)

    @app.get("/api/sessions/{session_id}/photo/{sequence_index}")
    async def get_session_photo(
        session_id: str, sequence_index: int, request: Request
    ) -> FileResponse:
        archive: SessionArchive = request.app.state.archive
        session = archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        match = next(
            (p for p in session.meta.photos if p.sequence_index == sequence_index),
            None,
        )
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=f"no photo with sequence_index={sequence_index}",
            )
        path = session.folder / match.path
        if not path.exists():
            raise HTTPException(status_code=404, detail="photo file missing on disk")
        return FileResponse(str(path), media_type="image/jpeg")

    @app.get("/api/sessions/{session_id}/text")
    async def get_session_text(
        session_id: str, request: Request
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        session = archive.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        return {
            "session_id": session.session_id,
            "extracted": session.read_extracted() or "",
        }

    @app.get("/api/sessions")
    async def list_sessions(
        request: Request, limit: int = 10, offset: int = 0,
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        if limit < 1:
            limit = 10
        if offset < 0:
            offset = 0
        sessions = archive.list_sessions(limit=limit, offset=offset)
        total = archive.count_sessions()
        return {
            "sessions": [_session_summary(s) for s in sessions],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @app.delete("/api/sessions")
    async def delete_all_sessions(request: Request) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        removed = archive.cleanup_all()
        return {"removed": removed}

    @app.delete("/api/sessions/{session_id}")
    async def delete_one_session(
        session_id: str, request: Request
    ) -> Dict[str, Any]:
        archive: SessionArchive = request.app.state.archive
        if not archive.delete_session(session_id):
            raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
        return {"removed": session_id}

    @app.delete("/api/sessions/older-than/{days}")
    async def delete_old_sessions(
        days: int, request: Request
    ) -> Dict[str, Any]:
        if days < 1:
            raise HTTPException(status_code=400, detail="days must be >= 1")
        archive: SessionArchive = request.app.state.archive
        removed = archive.cleanup_older_than(days)
        return {"removed": removed}

    return app


# --------------------------------------------------------------- helpers


async def _run_extract(
    request: Request, session_id: str, allow_when_done: bool
) -> Dict[str, Any]:
    body = await _maybe_json(request)
    cfg: WebappConfig = request.app.state.webapp_config
    archive: SessionArchive = request.app.state.archive
    ocr_client: OcrClient = request.app.state.ocr_client

    session = archive.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    if not session.meta.photos:
        raise HTTPException(status_code=400, detail="session has no photos to extract")
    if session.meta.extract_succeeded and not allow_when_done:
        # The /extract endpoint should be idempotent on the client
        # path — the UI uses /redo when the user wants to re-run with
        # a different model. Surface the existing text instead of
        # silently re-billing the hub.
        return {
            "session_id": session.session_id,
            "extracted": session.read_extracted() or "",
            "model": session.meta.model,
            "prompt_id": session.meta.prompt_id,
            "duration_s": session.meta.extract_duration_s,
            "reused": True,
        }

    model = _resolve_model(body.get("model"), cfg)
    prompt = _resolve_prompt(body.get("prompt_id"), cfg)
    photo_paths = session.photo_paths()

    t0 = time.monotonic()
    try:
        result = ocr_client.extract(
            image_paths=photo_paths,
            model=model,
            system=prompt.system,
        )
    except OcrError as exc:
        session.mark_extract_failed(model, str(exc), prompt_id=prompt.id)
        session.write_meta()
        # 424 (Failed Dependency) so Cloudflare passes the JSON body
        # through to the browser. Cloudflare rewrites 5xx into its own
        # HTML error page, clobbering the rich upstream message.
        raise HTTPException(status_code=424, detail=str(exc))

    duration = time.monotonic() - t0
    session.write_extracted(
        result.extracted_text,
        model=result.model,
        request_payload=result.request_payload,
        response_payload=result.response_payload,
        prompt_id=prompt.id,
        duration_s=duration,
    )
    session.write_meta()
    return {
        "session_id": session.session_id,
        "extracted": result.extracted_text,
        "model": result.model,
        "prompt_id": prompt.id,
        "duration_s": round(duration, 2),
        "reused": False,
    }


async def _maybe_json(request: Request) -> Dict[str, Any]:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _photo_dict(pm: PhotoMeta) -> Dict[str, Any]:
    return {
        "sequence_index": pm.sequence_index,
        "path": pm.path,
        "width": pm.width,
        "height": pm.height,
        "bytes_on_disk": pm.bytes_on_disk,
    }


def _session_summary(s: Session) -> Dict[str, Any]:
    return {
        "session_id": s.session_id,
        "created_at": s.meta.created_at,
        "photo_count": len(s.meta.photos),
        "model": s.meta.model,
        "prompt_id": s.meta.prompt_id,
        "extract_succeeded": s.meta.extract_succeeded,
        "extract_duration_s": s.meta.extract_duration_s,
        "extracted_chars": s.meta.extracted_chars,
        "extracted_preview": _preview(s.read_extracted(), 200),
        "error": s.meta.error,
    }


def _preview(text: Optional[str], n: int) -> Optional[str]:
    if not text:
        return None
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _config_dict(cfg: WebappConfig) -> Dict[str, Any]:
    return {
        "ocr_model_default": cfg.ocr_model_default,
        "ocr_models_available": cfg.ocr_models_available,
        "ocr_prompt_default": cfg.ocr_prompt_default,
        "history_retention_days": cfg.history_retention_days,
        "max_photos_per_session": cfg.max_photos_per_session,
        "max_photo_dimension_px": cfg.max_photo_dimension_px,
    }


def _resolve_prompt(prompt_id: Optional[str], cfg: WebappConfig) -> OcrPrompt:
    pid = prompt_id if isinstance(prompt_id, str) and prompt_id else None
    if not pid:
        pid = cfg.ocr_prompt_default
    return get_prompt(pid)


def _resolve_model(model: Any, cfg: WebappConfig) -> str:
    """Reject unknown models with HTTP 400 so a typo can't waste a
    180-second hub timeout."""
    candidate = (
        model if isinstance(model, str) and model.strip() else cfg.ocr_model_default
    )
    if cfg.ocr_models_available and candidate not in cfg.ocr_models_available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown ocr model {candidate!r}; "
                f"allowed: {cfg.ocr_models_available}"
            ),
        )
    return candidate


# Module-level app for `uvicorn app.webapp.server:app`.
app = create_app()
