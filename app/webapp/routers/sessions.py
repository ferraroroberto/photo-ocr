"""Session lifecycle: create, photo upload/delete/retrieval, OCR
extract/redo, text retrieval, listing, and the delete family."""

from __future__ import annotations

# Standard library imports
import asyncio
import logging
from typing import Any, Dict, List, Optional

# Third-party imports
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

# Local imports
from app.webapp.routers._helpers import maybe_json
from src.archive import PhotoMeta, Session, SessionArchive
from src.extract_job import (
    execute_extract_job,
    extract_status_payload,
    progress_meta,
    set_extract_progress,
)
from src.image_utils import ImageValidationError, validate_and_persist
from src.ocr_client import chunk_count
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
        "source": s.meta.source,
        "prompt_id": s.meta.prompt_id,
        "extract_succeeded": s.meta.extract_succeeded,
        "extract_duration_s": s.meta.extract_duration_s,
        "extracted_chars": s.meta.extracted_chars,
        "extracted_preview": _preview(s.read_extracted(), 200),
        "error": s.meta.error,
        "extract_progress": extract_status_payload(s, include_extracted=False),
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


def _resolve_source(source: Any, default: str) -> str:
    """Normalise a caller-supplied ``source`` label.

    Trims whitespace, caps length so a consumer can't write an essay into
    History meta, and falls back to ``default`` when unset/blank.
    """
    if isinstance(source, str) and source.strip():
        return source.strip()[:64]
    return default


def _store_extract_task(app: Any, session_id: str, task: asyncio.Task) -> None:
    tasks = app.state.extract_tasks
    tasks[session_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        tasks.pop(session_id, None)
        try:
            done_task.result()
        except Exception as exc:  # noqa: BLE001 — background crash visibility
            logger.exception(f"❌ Extract job crashed for {session_id}: {exc}")

    task.add_done_callback(_cleanup)


async def _start_extract(
    request: Request, session_id: str, allow_when_done: bool
) -> Dict[str, Any]:
    body = await maybe_json(request)
    cfg: WebappConfig = request.app.state.webapp_config
    archive: SessionArchive = request.app.state.archive

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
        set_extract_progress(
            session,
            phase="succeeded",
            chunks_total=chunk_count(len(session.meta.photos), cfg.extract_chunk_size),
            chunks_done=chunk_count(len(session.meta.photos), cfg.extract_chunk_size),
            model=session.meta.model,
            prompt_id=session.meta.prompt_id,
            error=None,
            reused=True,
        )
        return extract_status_payload(session)

    progress = progress_meta(session)
    if progress.get("phase") in {"queued", "running", "merging"}:
        return extract_status_payload(session)

    model = _resolve_model(body.get("model"), cfg)
    prompt = _resolve_prompt(body.get("prompt_id"), cfg)
    total_chunks = chunk_count(len(session.meta.photos), cfg.extract_chunk_size)
    set_extract_progress(
        session,
        phase="queued",
        chunks_total=total_chunks,
        chunks_done=0,
        model=model,
        prompt_id=prompt.id,
        error=None,
        reused=False,
    )

    task = asyncio.create_task(
        asyncio.to_thread(
            execute_extract_job,
            request.app,
            session_id,
            model,
            prompt.system,
            prompt.id,
            cfg.extract_chunk_size,
        )
    )
    _store_extract_task(request.app, session_id, task)
    return extract_status_payload(session)


# ---------------------------------------------------------------- routes


@router.post("/api/sessions")
async def create_session(request: Request) -> Dict[str, Any]:
    body = await maybe_json(request)
    archive: SessionArchive = request.app.state.archive
    incognito = bool(body.get("incognito", False))
    # The PWA is the dominant caller of this endpoint, so default to
    # "webapp"; async-flow consumers can self-identify with their own label.
    source = _resolve_source(body.get("source"), default="webapp")
    session = archive.new_session(incognito=incognito, source=source)
    return {
        "session_id": session.session_id,
        "folder": str(session.folder),
        "created_at": session.meta.created_at,
        "incognito": incognito,
        "source": source,
    }


async def _persist_uploads(
    session: Session, files: List[UploadFile], cfg: WebappConfig
) -> List[Dict[str, Any]]:
    """Validate, EXIF-rotate, downscale, and persist each uploaded image
    into the session folder, recording its ``PhotoMeta``.

    Returns the list of added-photo dicts. Raises ``HTTPException(400)`` on
    the first invalid file after rolling back files and metadata added by
    this batch. The caller owns any photo-count cap check and the final
    ``session.write_meta()`` on success. Shared by
    ``/api/sessions/{id}/photos`` and the single-shot ``/api/extract`` so
    both ingest images identically.
    """
    persisted: List[Dict[str, Any]] = []
    original_photos = list(session.meta.photos)
    added_paths: List[str] = []
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
            for rel_path in added_paths:
                try:
                    (session.folder / rel_path).unlink(missing_ok=True)
                except OSError as unlink_exc:
                    logger.warning(
                        f"⚠️  Could not roll back uploaded photo "
                        f"{session.folder / rel_path}: {unlink_exc}"
                    )
            session.meta.photos = original_photos
            session.write_meta()
            raise HTTPException(status_code=400, detail=str(exc))
        pm = PhotoMeta(
            sequence_index=p.sequence_index,
            path=p.path.name,
            width=p.width,
            height=p.height,
            bytes_on_disk=p.bytes_on_disk,
        )
        session.record_photo(pm)
        added_paths.append(pm.path)
        persisted.append(_photo_dict(pm))
    return persisted


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

    persisted = await _persist_uploads(session, files, cfg)
    session.write_meta()
    return {
        "session_id": session.session_id,
        "photos": [_photo_dict(pm) for pm in session.meta.photos],
        "added": persisted,
    }


@router.post("/api/sessions/{session_id}/photos/reorder")
async def reorder_photos(session_id: str, request: Request) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    session = archive.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    body = await maybe_json(request)
    order = body.get("order")
    if not isinstance(order, list) or any(
        not isinstance(i, int) or isinstance(i, bool) for i in order
    ):
        raise HTTPException(
            status_code=400,
            detail="order must be a list of photo sequence_index integers",
        )
    if session.meta.extract_succeeded is not None:
        logger.info(f"ℹ️  reordering already-extracted session {session_id}")
    if not session.reorder_photos(order):
        current = [p.sequence_index for p in session.meta.photos]
        raise HTTPException(
            status_code=400,
            detail=f"order must match current photo sequence indexes {current}",
        )
    session.write_meta()
    return {
        "session_id": session.session_id,
        "photos": [_photo_dict(pm) for pm in session.meta.photos],
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
    return await _start_extract(request, session_id, allow_when_done=False)


@router.post("/api/sessions/{session_id}/redo")
async def redo_session(session_id: str, request: Request) -> Dict[str, Any]:
    return await _start_extract(request, session_id, allow_when_done=True)


@router.get("/api/sessions/{session_id}/extract/status")
async def get_extract_status(session_id: str, request: Request) -> Dict[str, Any]:
    archive: SessionArchive = request.app.state.archive
    session = archive.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    return extract_status_payload(session)


@router.post("/api/extract")
async def extract_single_shot(
    request: Request,
    files: List[UploadFile] = File(...),
) -> Dict[str, Any]:
    """Synchronous single-shot OCR for downstream fleet consumers.

    Create a session, ingest 1..N images, run extraction to completion
    server-side, and return the clean text in **one call** — the
    consumable counterpart to the async ``create → photos → extract →
    poll`` flow. This is the surface app-launcher's "paste screenshot"
    button (and any future fleet app) calls over loopback; see
    ``docs/consuming-the-session-api.md``.

    The take is kept in History (recoverable on disk) exactly like one
    made in the PWA, unless ``incognito=true``, and is attributed to its
    ``source`` so History stays a cross-fleet audit trail. Query params,
    all optional: ``model``, ``prompt_id``, ``incognito``, ``source``
    (defaults to ``"api"``; a consumer should pass e.g. ``"app-launcher"``).

    Errors: ``400`` empty upload, ``413`` more than ``single_shot_max_photos``
    images (use the async flow for big takes), ``400`` unknown model,
    ``502`` on a hub/extraction failure.
    """
    cfg: WebappConfig = request.app.state.webapp_config
    archive: SessionArchive = request.app.state.archive
    if not files:
        raise HTTPException(status_code=400, detail="no files in upload")
    if len(files) > cfg.single_shot_max_photos:
        raise HTTPException(
            status_code=413,
            detail=(
                f"single-shot /api/extract accepts at most "
                f"{cfg.single_shot_max_photos} photos; got {len(files)}. "
                f"Use the async session flow (POST /api/sessions → "
                f"/photos → /extract → poll status) for larger takes."
            ),
        )

    params = request.query_params
    model = _resolve_model(params.get("model"), cfg)
    prompt = _resolve_prompt(params.get("prompt_id"), cfg)
    incognito = params.get("incognito") in ("1", "true", "True")
    # Externally-triggered takes default to "api"; a consumer should pass
    # its own label (e.g. "app-launcher") so History stays attributable.
    source = _resolve_source(params.get("source"), default="api")

    session = archive.new_session(incognito=incognito, source=source)
    try:
        await _persist_uploads(session, files, cfg)
    except HTTPException:
        archive.delete_session(session.session_id)
        raise
    session.write_meta()

    # Reuse the exact extraction engine the async path uses (chunking,
    # overlap merge, dedup, archival, search index) — run it to completion
    # off the event loop. No duplicate OCR logic.
    await asyncio.to_thread(
        execute_extract_job,
        request.app,
        session.session_id,
        model,
        prompt.system,
        prompt.id,
        cfg.extract_chunk_size,
    )

    final = archive.get(session.session_id)
    if final is None:
        raise HTTPException(
            status_code=500, detail="session disappeared mid-extract"
        )
    if final.meta.extract_succeeded is not True:
        raise HTTPException(
            status_code=502, detail=final.meta.error or "extraction failed"
        )
    return {
        "session_id": final.session_id,
        "text": final.read_extracted() or "",
        "model": final.meta.model,
        "prompt_id": final.meta.prompt_id,
        "chars": final.meta.extracted_chars,
        "duration_s": final.meta.extract_duration_s,
        "incognito": incognito,
        "source": source,
    }


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
