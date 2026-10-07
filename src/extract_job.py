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
import hashlib
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
_NON_TERMINAL_PHASES = frozenset({"queued", "running", "merging"})


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
        "missing_photos": list(progress.get("missing_photos") or []),
        # Why the last "retry missing" read nothing new; the text is unchanged.
        "retry_error": progress.get("retry_error"),
    }
    if phase == "succeeded" and include_extracted:
        payload["extracted"] = session.read_extracted() or ""
    return payload


def set_extract_progress(session: Session, **fields: Any) -> None:
    progress = progress_meta(session)
    progress.update(fields)
    session.meta.extra[_PROGRESS_KEY] = progress
    session.write_meta()


def photos_signature(session: Session) -> str:
    """Fingerprint of the take's photo list (files, sizes, order)."""
    digest = hashlib.sha1()
    for p in session.meta.photos:
        digest.update(f"{p.path}:{p.bytes_on_disk}:{p.width}x{p.height};".encode())
    return digest.hexdigest()


def extract_is_current(session: Session) -> bool:
    """Whether the stored text still reflects the take's photos.

    A photo added, removed or reordered after the read makes it stale. A take
    read before the fingerprint was recorded counts as current.
    """
    recorded = progress_meta(session).get("photos_sig")
    return recorded is None or recorded == photos_signature(session)


def settle_interrupted(session: Session, reason: str) -> None:
    """Settle a take whose job died before reaching a terminal phase, so it
    can't read as queued/running forever and block ``/extract``, ``/redo`` and
    ``/retry-missing``. A take with text on disk keeps it (a redo or retry
    died, the last good read is intact); one without is marked failed."""
    if session.meta.extract_succeeded:
        set_extract_progress(session, phase="succeeded", retry_error=reason)
        return
    model = progress_meta(session).get("model") or session.meta.model or ""
    session.mark_extract_failed(model, reason, prompt_id=session.meta.prompt_id)
    set_extract_progress(session, phase="failed", error=reason)


def settle_interrupted_extracts(archive: SessionArchive) -> int:
    """Boot-time sweep: no job survives a restart, so any take still in a
    non-terminal phase was interrupted. Returns how many were settled."""
    settled = 0
    for session in archive.iter_sessions():
        if progress_meta(session).get("phase") in _NON_TERMINAL_PHASES:
            settle_interrupted(session, "interrupted by a webapp restart")
            logger.info(f"ℹ️  Settled interrupted extract for {session.session_id}")
            settled += 1
    return settled


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
            missing_photos=[],
            retry_error=None,
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
                policy=cfg.extract_policy(),
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
            missing_photos=result.missing_photos,
            photos_sig=photos_signature(current),
        )
        _index_session_best_effort(cfg, archive, current)


def execute_retry_missing_job(app: Any, session_id: str) -> None:
    """Re-read only the photos a finished take left unread, then re-collate.

    Runs under the same lock as a full extract. The take stays a success
    whatever happens: when nothing new could be read the text on disk is left
    untouched and ``retry_error`` says why.
    """
    archive: SessionArchive = app.state.archive
    cfg: WebappConfig = app.state.webapp_config
    ocr_client: OcrClient = app.state.ocr_client

    with app.state.extract_lock:
        session = archive.get(session_id)
        if session is None:
            logger.warning(f"⚠️  Retry job lost unknown session {session_id}")
            return
        before = list(progress_meta(session).get("missing_photos") or [])

        def _settle(**fields: Any) -> None:
            current = archive.get(session_id) or session
            set_extract_progress(current, **fields)

        payloads = session.read_ocr_payloads()
        if payloads is None:
            _settle(
                phase="succeeded", retry_error="this take's OCR archive is "
                "missing; use Redo",
            )
            return
        request_payload, response_payload = payloads
        total_units = sum(
            1
            for entry in response_payload.get("chunks") or []
            if isinstance(entry, dict) and "error" in entry
        )
        _settle(
            phase="running", chunks_total=total_units, chunks_done=0,
            error=None, retry_error=None,
        )

        def _on_unit(done: int, total: int) -> None:
            _settle(phase="running", chunks_total=total, chunks_done=done)

        t0 = time.monotonic()
        try:
            result = ocr_client.retry_missing(
                image_paths=session.photo_paths(),
                request_payload=request_payload,
                response_payload=response_payload,
                system=str(request_payload.get("system") or ""),
                progress_callback=_on_unit,
                policy=cfg.extract_policy(),
            )
        except OcrError as exc:
            logger.info(f"ℹ️  Retry of missing photos read nothing new: {exc}")
            _settle(phase="succeeded", retry_error=str(exc))
            return

        current = archive.get(session_id) or session
        _settle(phase="merging", chunks_done=total_units)
        current.write_extracted(
            result.extracted_text,
            model=result.model,
            request_payload=result.request_payload,
            response_payload=result.response_payload,
            prompt_id=current.meta.prompt_id,
            duration_s=(current.meta.extract_duration_s or 0.0)
            + (time.monotonic() - t0),
        )
        _settle(
            phase="succeeded",
            chunks_done=total_units,
            model=result.model,
            error=None,
            retry_error=None,
            missing_photos=result.missing_photos,
        )
        logger.info(
            f"✅ Retry recovered {len(before) - len(result.missing_photos)} of "
            f"{len(before)} missing photo(s) for {session_id}"
        )
        _index_session_best_effort(cfg, archive, current)
