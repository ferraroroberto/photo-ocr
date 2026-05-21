"""Session lifecycle: create, photo upload/delete/retrieval, OCR
extract/redo, text retrieval, listing, and the delete family."""

from __future__ import annotations

# Standard library imports
import logging
import time
from typing import Any, Dict, List, Optional

# Third-party imports
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

# Local imports
from app.webapp.routers._helpers import maybe_json
from src.archive import PhotoMeta, Session, SessionArchive
from src.image_utils import ImageValidationError, validate_and_persist
from src.ocr_client import OcrClient, OcrError
from src.ocr_prompts import OcrPrompt, get_prompt
from src.webapp_config import WebappConfig

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------- helpers


def _photo_dict(pm: PhotoMeta) -> Dict[str, Any]:
    return {
        "sequence_index": pm.sequence_index,
        "path": pm.path,
        "width": pm.width,
        "height": pm.height,
        "bytes_on_disk": pm.bytes_on_disk,
    }


def _preview(text: Optional[str], n: int) -> Optional[str]:
    if not text:
        return None
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


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


async def _run_extract(
    request: Request, session_id: str, allow_when_done: bool
) -> Dict[str, Any]:
    body = await maybe_json(request)
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

    # Index the fresh text for full-text search. Best-effort — a search
    # hiccup must never fail an extract that already succeeded and is
    # persisted on disk (the canonical source).
    if cfg.search_enabled:
        try:
            archive.index_session(session)
        except Exception as exc:  # noqa: BLE001 — search is non-critical
            logger.warning(f"⚠️  Could not index session {session_id}: {exc}")

    return {
        "session_id": session.session_id,
        "extracted": result.extracted_text,
        "model": result.model,
        "prompt_id": prompt.id,
        "duration_s": round(duration, 2),
        "reused": False,
    }


# ---------------------------------------------------------------- routes


@router.post("/api/sessions")
async def create_session(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    archive: SessionArchive = request.app.state.archive
    incognito = bool(body.get("incognito", False))
    session = archive.new_session(incognito=incognito)
    return {
        "session_id": session.session_id,
        "folder": str(session.folder),
        "created_at": session.meta.created_at,
        "incognito": incognito,
    }


@router.post("/api/sessions/{session_id}/photos")
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


@router.delete("/api/sessions/{session_id}/photos/{sequence_index}")
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


@router.post("/api/sessions/{session_id}/extract")
async def extract_session(session_id: str, request: Request) -> Dict[str, Any]:
    return await _run_extract(request, session_id, allow_when_done=False)


@router.post("/api/sessions/{session_id}/redo")
async def redo_session(session_id: str, request: Request) -> Dict[str, Any]:
    return await _run_extract(request, session_id, allow_when_done=True)


@router.get("/api/sessions/{session_id}/photo/{sequence_index}")
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


@router.get("/api/sessions/{session_id}/text")
async def get_session_text(session_id: str, request: Request) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    session = archive.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    return {
        "session_id": session.session_id,
        "extracted": session.read_extracted() or "",
    }


@router.get("/api/sessions")
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


@router.delete("/api/sessions")
async def delete_all_sessions(request: Request) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    removed = archive.cleanup_all()
    return {"removed": removed}


@router.delete("/api/sessions/{session_id}")
async def delete_one_session(session_id: str, request: Request) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    if not archive.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    return {"removed": session_id}


@router.delete("/api/sessions/older-than/{days}")
async def delete_old_sessions(days: int, request: Request) -> Dict[str, Any]:
    if days < 1:
        raise HTTPException(status_code=400, detail="days must be >= 1")
    archive: SessionArchive = request.app.state.archive
    removed = archive.cleanup_older_than(days)
    return {"removed": removed}
