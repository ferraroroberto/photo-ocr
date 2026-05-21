"""Dated session archive — every OCR take's full lineage on disk.

Layout:

    archive/
      YYYY/
        MM/
          DD/
            HH-MM-SS-<id>/
              01.jpg, 02.jpg, …      persisted photos in upload order
              extracted.txt          final OCR output (empty if extract failed)
              ocr_request.json       prompt + image-pointer payload sent to hub
              ocr_response.json      raw hub response
              meta.json              session metadata

The whole `archive/` folder is gitignored. Sessions older than the
retention window (default 30 days) are deleted on app start, and on
demand from the UI's Clean button.
"""

from __future__ import annotations

# Standard library imports
import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Local imports
from src.archive_index import INDEX_FILENAME, ArchiveIndex

logger = logging.getLogger(__name__)

DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"
META_FILENAME = "meta.json"
EXTRACTED_FILENAME = "extracted.txt"
OCR_REQUEST_FILENAME = "ocr_request.json"
OCR_RESPONSE_FILENAME = "ocr_response.json"


@dataclass
class PhotoMeta:
    sequence_index: int
    path: str  # relative filename within the session folder (e.g. "01.jpg")
    width: int
    height: int
    bytes_on_disk: int


@dataclass
class SessionMeta:
    """Metadata recorded alongside each session."""

    session_id: str
    created_at: str  # ISO 8601 in UTC
    photos: List[PhotoMeta] = field(default_factory=list)
    model: Optional[str] = None
    prompt_id: Optional[str] = None
    extract_succeeded: Optional[bool] = None
    extract_duration_s: Optional[float] = None
    extracted_chars: int = 0
    error: Optional[str] = None
    incognito: bool = False  # filtered out of list_sessions when True
    extra: dict = field(default_factory=dict)


@dataclass
class Session:
    """Handle to a single archive folder."""

    session_id: str
    folder: Path
    meta: SessionMeta

    # ---------------------------------------------------------------- photos

    def next_sequence_index(self) -> int:
        """Returns 1-based index for the next photo to be added."""
        return len(self.meta.photos) + 1

    def record_photo(self, photo: PhotoMeta) -> None:
        self.meta.photos.append(photo)

    def remove_photo(self, sequence_index: int) -> bool:
        """Remove a not-yet-extracted photo by sequence index.

        Renumbers remaining photos so the next OCR call still sees a
        contiguous 01.jpg, 02.jpg, … sequence. Returns True on success.
        """
        idx = next(
            (i for i, p in enumerate(self.meta.photos) if p.sequence_index == sequence_index),
            -1,
        )
        if idx < 0:
            return False
        # Delete the file, then drop from meta, then renumber+rename.
        target = self.folder / self.meta.photos[idx].path
        if target.exists():
            try:
                target.unlink()
            except OSError as exc:
                logger.warning(f"⚠️  Could not unlink {target}: {exc}")
        del self.meta.photos[idx]
        self._renumber_photos()
        return True

    def _renumber_photos(self) -> None:
        """Re-stamp 01.jpg, 02.jpg, … after a delete or reorder.

        Two-phase rename via .tmp suffixes so we don't clobber an
        intermediate file when the new index collides with a still-
        existing old name.
        """
        # Phase 1: rename everything to a unique temp name.
        temps: List[tuple[Path, int]] = []
        for new_idx, p in enumerate(self.meta.photos, start=1):
            current = self.folder / p.path
            if not current.exists():
                continue
            tmp = self.folder / f"{p.path}.tmp-{new_idx}"
            try:
                current.rename(tmp)
            except OSError as exc:
                logger.warning(f"⚠️  Could not rename {current}: {exc}")
                continue
            temps.append((tmp, new_idx))

        # Phase 2: temp → final, and update meta.
        new_photos: List[PhotoMeta] = []
        for (tmp, new_idx), p in zip(temps, self.meta.photos):
            final_name = f"{new_idx:02d}.jpg"
            final_path = self.folder / final_name
            try:
                tmp.rename(final_path)
            except OSError as exc:
                logger.warning(f"⚠️  Could not rename {tmp}: {exc}")
                # Best effort — restore old name + index so meta stays consistent.
                new_photos.append(p)
                continue
            new_photos.append(
                PhotoMeta(
                    sequence_index=new_idx,
                    path=final_name,
                    width=p.width,
                    height=p.height,
                    bytes_on_disk=p.bytes_on_disk,
                )
            )
        self.meta.photos = new_photos

    def photo_paths(self) -> List[Path]:
        """Absolute paths of stored photos in upload order."""
        return [self.folder / p.path for p in self.meta.photos]

    # ---------------------------------------------------------------- writers

    def write_extracted(
        self,
        extracted_text: str,
        model: str,
        request_payload: dict,
        response_payload: dict,
        prompt_id: Optional[str],
        duration_s: Optional[float] = None,
    ) -> Path:
        (self.folder / EXTRACTED_FILENAME).write_text(
            extracted_text, encoding="utf-8"
        )
        (self.folder / OCR_REQUEST_FILENAME).write_text(
            json.dumps(request_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.folder / OCR_RESPONSE_FILENAME).write_text(
            json.dumps(response_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.meta.model = model
        self.meta.prompt_id = prompt_id
        self.meta.extract_succeeded = True
        self.meta.extracted_chars = len(extracted_text)
        if duration_s is not None:
            self.meta.extract_duration_s = float(duration_s)
        self.meta.error = None
        return self.folder / EXTRACTED_FILENAME

    def mark_extract_failed(
        self,
        model: str,
        error: str,
        prompt_id: Optional[str] = None,
    ) -> None:
        self.meta.model = model
        self.meta.prompt_id = prompt_id
        self.meta.extract_succeeded = False
        self.meta.error = error

    def write_meta(self) -> Path:
        path = self.folder / META_FILENAME
        path.write_text(
            json.dumps(asdict(self.meta), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    # ---------------------------------------------------------------- readers

    def read_extracted(self) -> Optional[str]:
        path = self.folder / EXTRACTED_FILENAME
        return path.read_text(encoding="utf-8") if path.exists() else None


class SessionArchive:
    """Top-level archive — creates sessions, lists them, prunes old ones."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ARCHIVE_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self._index: Optional[ArchiveIndex] = None

    # ------------------------------------------------------ search index

    @property
    def index(self) -> ArchiveIndex:
        """Lazily-created FTS5 search index over this archive.

        Constructing the wrapper is cheap and touches no disk — the
        ``index.sqlite`` file is only materialised on the first write.
        """
        if self._index is None:
            self._index = ArchiveIndex(self.root / INDEX_FILENAME)
        return self._index

    def index_session(self, session: Session) -> None:
        """Upsert one session's extracted text into the search index.

        Incognito sessions are skipped — they never surface in History
        and must not surface in search either.
        """
        if session.meta.incognito:
            return
        self.index.index_session(
            session_id=session.session_id,
            created_at=session.meta.created_at,
            text=session.read_extracted() or "",
            model=session.meta.model,
        )

    def search(self, q: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Ranked full-text search over indexed sessions."""
        return self.index.search(q, limit)

    def reconcile_index(self) -> int:
        """Rebuild any missing index rows from ``extracted.txt`` on disk.

        Run once at webapp boot. Keeps ``extracted.txt`` canonical: the
        index can be deleted at any time and is restored from here.
        """
        sessions: List[Dict[str, Any]] = []
        for s in (self._hydrate(f) for f in self._iter_session_folders()):
            if s.meta.incognito:
                continue
            text = s.read_extracted() or ""
            if not text.strip():
                continue
            sessions.append(
                {
                    "session_id": s.session_id,
                    "created_at": s.meta.created_at,
                    "model": s.meta.model,
                    "text": text,
                }
            )
        return self.index.reconcile(sessions)

    # ------------------------------------------------------ create / lookup

    def new_session(
        self,
        now: Optional[datetime] = None,
        incognito: bool = False,
    ) -> Session:
        ts = now or datetime.now()
        session_id = ts.strftime("%H-%M-%S-") + uuid.uuid4().hex[:8]
        folder = (
            self.root
            / ts.strftime("%Y")
            / ts.strftime("%m")
            / ts.strftime("%d")
            / session_id
        )
        folder.mkdir(parents=True, exist_ok=True)

        meta = SessionMeta(
            session_id=session_id,
            created_at=ts.isoformat(timespec="seconds"),
            incognito=incognito,
        )
        session = Session(session_id=session_id, folder=folder, meta=meta)
        session.write_meta()
        logger.info(f"📁 New session {session_id} → {folder}")
        return session

    def get(self, session_id: str) -> Optional[Session]:
        for folder in self._iter_session_folders():
            if folder.name == session_id:
                return self._hydrate(folder)
        return None

    def list_sessions(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Session]:
        """Newest-first listing, optionally paginated.

        Incognito sessions are excluded — they exist on disk while the
        capture flow needs them but never surface in History.
        """
        sessions = sorted(
            (self._hydrate(f) for f in self._iter_session_folders()),
            key=lambda s: s.meta.created_at,
            reverse=True,
        )
        sessions = [s for s in sessions if not s.meta.incognito]
        if offset:
            sessions = sessions[offset:]
        if limit is not None:
            sessions = sessions[:limit]
        return sessions

    def count_sessions(self) -> int:
        return sum(
            1
            for s in (self._hydrate(f) for f in self._iter_session_folders())
            if not s.meta.incognito
        )

    def delete_session(self, session_id: str) -> bool:
        for folder in self._iter_session_folders():
            if folder.name == session_id:
                shutil.rmtree(folder, ignore_errors=True)
                self._prune_empty_date_folders()
                self.index.delete_session(session_id)
                return True
        return False

    # ------------------------------------------------------ housekeeping

    def cleanup_older_than(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        removed_ids: List[str] = []
        for folder in list(self._iter_session_folders()):
            try:
                mtime = folder.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed_ids.append(folder.name)
        if removed_ids:
            logger.info(
                f"🧹 Pruned {len(removed_ids)} sessions older than {days} days"
            )
        self._prune_empty_date_folders()
        for sid in removed_ids:
            self.index.delete_session(sid)
        return len(removed_ids)

    def cleanup_all(self) -> int:
        removed_ids: List[str] = []
        for folder in list(self._iter_session_folders()):
            removed_ids.append(folder.name)
            shutil.rmtree(folder, ignore_errors=True)
        self._prune_empty_date_folders()
        for sid in removed_ids:
            self.index.delete_session(sid)
        if removed_ids:
            logger.info(f"🧹 Cleared {len(removed_ids)} sessions")
        return len(removed_ids)

    # ---------------------------------------------------------------- helpers

    def _iter_session_folders(self) -> Iterator[Path]:
        if not self.root.exists():
            return
        for year in self.root.iterdir():
            if not year.is_dir():
                continue
            for month in year.iterdir():
                if not month.is_dir():
                    continue
                for day in month.iterdir():
                    if not day.is_dir():
                        continue
                    for session in day.iterdir():
                        if session.is_dir():
                            yield session

    def _prune_empty_date_folders(self) -> None:
        if not self.root.exists():
            return
        for year in list(self.root.iterdir()):
            if not year.is_dir():
                continue
            for month in list(year.iterdir()):
                if not month.is_dir():
                    continue
                for day in list(month.iterdir()):
                    if day.is_dir() and not any(day.iterdir()):
                        try:
                            day.rmdir()
                        except OSError:
                            pass
                if month.is_dir() and not any(month.iterdir()):
                    try:
                        month.rmdir()
                    except OSError:
                        pass
            if year.is_dir() and not any(year.iterdir()):
                try:
                    year.rmdir()
                except OSError:
                    pass

    def _hydrate(self, folder: Path) -> Session:
        meta_path = folder / META_FILENAME
        meta = SessionMeta(
            session_id=folder.name,
            created_at=datetime.fromtimestamp(folder.stat().st_mtime).isoformat(
                timespec="seconds"
            ),
        )
        if meta_path.exists():
            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                photos = [
                    PhotoMeta(
                        sequence_index=int(p.get("sequence_index", i + 1)),
                        path=str(p.get("path", f"{i + 1:02d}.jpg")),
                        width=int(p.get("width", 0)),
                        height=int(p.get("height", 0)),
                        bytes_on_disk=int(p.get("bytes_on_disk", 0)),
                    )
                    for i, p in enumerate(raw.get("photos") or [])
                    if isinstance(p, dict)
                ]
                meta = SessionMeta(
                    session_id=str(raw.get("session_id", folder.name)),
                    created_at=str(raw.get("created_at", meta.created_at)),
                    photos=photos,
                    model=raw.get("model"),
                    prompt_id=raw.get("prompt_id"),
                    extract_succeeded=raw.get("extract_succeeded"),
                    extract_duration_s=raw.get("extract_duration_s"),
                    extracted_chars=int(raw.get("extracted_chars", 0)),
                    error=raw.get("error"),
                    incognito=bool(raw.get("incognito", False)),
                    extra=dict(raw.get("extra") or {}),
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(f"⚠️  Stale meta for {folder.name}: {exc}")

        return Session(session_id=folder.name, folder=folder, meta=meta)
