"""Chunked OCR extraction job engine — locking, progress state machine,
chunked hub calls, archive writes, and search indexing.

Framework-free business logic (no FastAPI import) so any UI surface —
the webapp's async session flow, the single-shot `/api/extract` route,
or a future CLI path — can drive the same archive-integrated
extraction without going through `Request`/`app.state` plumbing. See
`src/__init__.py` for the `src/` <-> `app/` split convention.
"""

from __future__ import annotations

# Standard library imports
import logging
import time
from typing import Any, Dict

# Local imports
from src.archive import Session, SessionArchive
from src.app_config import AppConfig
from src.ocr_client import OcrClient, OcrError, chunk_count
from src.ocr_prompts import apply_language_hint
from src.webapp_config import WebappConfig

logger = logging.getLogger(__name__)

_PROGRESS_KEY = "extract_progress"


def progress_meta(session: Session) -> Dict[str, Any]:
    raw = session.meta.extra.get(_PROGRESS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def extract_status_payload(
    session: Session, include_extracted: bool = True
) -> Dict[str, Any]:
    progress = progress_meta(session)
    phase = progress.get("phase")
    if not phase:
        if session.meta.extract_succeeded is True:
            phase = "succeeded"
        elif session.meta.extract_succeeded is False:
            phase = "failed"
        else:
            phase = "idle"

    payload = {
        "session_id": session.session_id,
        "phase": phase,
        "chunks_total": int(progress.get("chunks_total") or 0),
        "chunks_done": int(progress.get("chunks_done") or 0),
        "model": progress.get("model") or session.meta.model,
        "prompt_id": progress.get("prompt_id") or session.meta.prompt_id,
        "duration_s": session.meta.extract_duration_s,
        "extract_succeeded": session.meta.extract_succeeded,
        "extracted_chars": session.meta.extracted_chars,
        "error": session.meta.error,
        "reused": bool(progress.get("reused", False)),
    }
    if phase == "succeeded" and include_extracted:
        payload["extracted"] = session.read_extracted() or ""
    return payload


def set_extract_progress(session: Session, **fields: Any) -> None:
    progress = progress_meta(session)
    progress.update(fields)
    session.meta.extra[_PROGRESS_KEY] = progress
    session.write_meta()


def _index_session_best_effort(
    cfg: WebappConfig, archive: SessionArchive, session: Session
) -> None:
    if not cfg.search_enabled:
        return
    try:
        archive.index_session(session)
    except Exception as exc:  # noqa: BLE001 — search is non-critical
        logger.warning(f"⚠️  Could not index session {session.session_id}: {exc}")


def execute_extract_job(
    app: Any,
    session_id: str,
    model: str,
    prompt_system: str,
    prompt_id: str,
    chunk_size: int,
) -> None:
    archive: SessionArchive = app.state.archive
    cfg: WebappConfig = app.state.webapp_config
    app_cfg: AppConfig = app.state.app_config
    ocr_client: OcrClient = app.state.ocr_client
    lock = app.state.extract_lock

    with lock:
        session = archive.get(session_id)
        if session is None:
            logger.warning(f"⚠️  Extract job lost unknown session {session_id}")
            return

        total_chunks = chunk_count(len(session.meta.photos), chunk_size)
        set_extract_progress(
            session,
            phase="running",
            chunks_total=total_chunks,
            chunks_done=0,
            model=model,
            prompt_id=prompt_id,
            error=None,
            reused=False,
        )
        photo_paths = session.photo_paths()
        t0 = time.monotonic()

        def _on_chunk(done: int, total: int) -> None:
            current = archive.get(session_id)
            if current is None:
                return
            set_extract_progress(
                current,
                phase="running",
                chunks_total=total,
                chunks_done=done,
                model=model,
                prompt_id=prompt_id,
            )

        try:
            result = ocr_client.extract(
                image_paths=photo_paths,
                model=model,
                system=apply_language_hint(prompt_system, app_cfg.default_language_hint),
                chunk_size=chunk_size,
                progress_callback=_on_chunk,
            )
        except OcrError as exc:
            current = archive.get(session_id) or session
            current.mark_extract_failed(model, str(exc), prompt_id=prompt_id)
            set_extract_progress(
                current,
                phase="failed",
                chunks_total=total_chunks,
                chunks_done=int(progress_meta(current).get("chunks_done") or 0),
                model=model,
                prompt_id=prompt_id,
                error=str(exc),
                reused=False,
            )
            return

        duration = time.monotonic() - t0
        current = archive.get(session_id) or session
        set_extract_progress(
            current,
            phase="merging",
            chunks_total=total_chunks,
            chunks_done=total_chunks,
            model=model,
            prompt_id=prompt_id,
            error=None,
            reused=False,
        )
        current.write_extracted(
            result.extracted_text,
            model=result.model,
            request_payload=result.request_payload,
            response_payload=result.response_payload,
            prompt_id=prompt_id,
            duration_s=duration,
        )
        set_extract_progress(
            current,
            phase="succeeded",
            chunks_total=total_chunks,
            chunks_done=total_chunks,
            model=result.model,
            prompt_id=prompt_id,
            error=None,
            reused=False,
        )
        _index_session_best_effort(cfg, archive, current)
