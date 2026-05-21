"""SQLite FTS5 full-text index over the session archive.

Turns ``archive/YYYY/MM/DD/…/extracted.txt`` from cold storage into a
queryable paper memory — "find the receipt from the bakery".

Design notes:

* One file, ``archive/index.sqlite`` (gitignored with the rest of
  ``archive/``). No external service, no migration ceremony.
* ``extracted.txt`` on disk stays the canonical source of truth. The
  index can be deleted at any time and is rebuilt from disk on the next
  :meth:`ArchiveIndex.reconcile` — which runs once at webapp boot.
* A single ``sqlite3.Connection`` is held lazily, opened with
  ``check_same_thread=False`` and guarded by a ``threading.Lock`` so the
  sync archive writers and the async ``/api/search`` handler can share
  it. FTS5 writes are sub-millisecond, so one coarse lock is plenty.

FTS5 has no row uniqueness, so an "upsert" is a delete-then-insert keyed
on ``session_id`` inside a transaction.
"""

from __future__ import annotations

# Standard library imports
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

INDEX_FILENAME = "index.sqlite"

# 0-based column index of the indexed ``text`` column — needed by the
# FTS5 ``snippet()`` built-in.
_TEXT_COLUMN_INDEX = 4

_CREATE_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS sessions USING fts5(
    session_id UNINDEXED,
    created_at UNINDEXED,
    model UNINDEXED,
    kind UNINDEXED,
    text,
    tokenize = 'porter unicode61 remove_diacritics 1'
)
"""


def _phrase_query(q: str) -> str:
    """Wrap raw user input as a single FTS5 phrase.

    FTS5 ``MATCH`` treats ``"``, ``*``, ``(`` etc. as query syntax — an
    unbalanced quote raises ``sqlite3.OperationalError``. Doubling the
    embedded quotes and wrapping the whole thing in quotes turns any
    input into a safe literal phrase search.
    """
    escaped = q.replace('"', '""')
    return f'"{escaped}"'


class ArchiveIndex:
    """FTS5 index over one archive root's ``extracted.txt`` files."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------ connection

    def _connect(self) -> sqlite3.Connection:
        """Return the live connection, opening + provisioning it lazily.

        On a corrupt index file the database is deleted and recreated —
        free to do, since :meth:`reconcile` rebuilds it from disk.
        """
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()
        except sqlite3.DatabaseError as exc:
            logger.warning(
                f"⚠️  Search index {self.db_path} unusable ({exc}); "
                "rebuilding from disk"
            )
            # Close the handle to the corrupt file first — on Windows an
            # open handle blocks the unlink, leaving the bad file in place.
            try:
                conn.close()
            except sqlite3.Error:
                pass
            try:
                self.db_path.unlink()
            except OSError:
                pass
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()
        self._conn = conn
        return conn

    def _conn_if_exists(self) -> Optional[sqlite3.Connection]:
        """Connection for read/delete paths — never *creates* the file.

        Returns ``None`` when no index has been built yet, so a search
        or a delete on a fresh install is a cheap no-op instead of
        materialising an empty ``index.sqlite``.
        """
        if self._conn is not None:
            return self._conn
        if not self.db_path.exists():
            return None
        return self._connect()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ------------------------------------------------------ writes

    def index_session(
        self,
        session_id: str,
        created_at: str,
        text: str,
        model: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> None:
        """Upsert one session row, keyed on ``session_id``.

        Empty ``text`` is skipped — a failed extract leaves an empty
        ``extracted.txt`` and there is nothing to search.
        """
        if not session_id or not (text or "").strip():
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM sessions WHERE session_id = ?", (session_id,)
                )
                conn.execute(
                    "INSERT INTO sessions"
                    " (session_id, created_at, model, kind, text)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (session_id, created_at or "", model or "", kind or "", text),
                )
                conn.commit()
            except sqlite3.DatabaseError as exc:
                logger.warning(f"⚠️  Could not index session {session_id}: {exc}")

    # ------------------------------------------------------ reads

    def search(self, q: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Ranked full-text search. Empty query returns an empty list."""
        if not (q or "").strip():
            return []
        limit = max(1, min(int(limit), 100))
        with self._lock:
            conn = self._conn_if_exists()
            if conn is None:
                return []
            try:
                rows = conn.execute(
                    "SELECT session_id, created_at, model,"
                    f" snippet(sessions, {_TEXT_COLUMN_INDEX},"
                    " '[', ']', '…', 12) AS snippet"
                    " FROM sessions WHERE sessions MATCH ?"
                    " ORDER BY rank LIMIT ?",
                    (_phrase_query(q), limit),
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                logger.warning(f"⚠️  Search failed for {q!r}: {exc}")
                return []
        return [
            {
                "session_id": r["session_id"],
                "created_at": r["created_at"],
                "model": r["model"] or None,
                "snippet": r["snippet"] or "",
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> None:
        """Drop one session's row — call when the session leaves disk."""
        if not session_id:
            return
        with self._lock:
            conn = self._conn_if_exists()
            if conn is None:
                return
            try:
                conn.execute(
                    "DELETE FROM sessions WHERE session_id = ?", (session_id,)
                )
                conn.commit()
            except sqlite3.DatabaseError as exc:
                logger.warning(
                    f"⚠️  Could not drop session {session_id} from index: {exc}"
                )

    def session_ids(self) -> set[str]:
        """Every ``session_id`` currently in the index."""
        with self._lock:
            conn = self._conn_if_exists()
            if conn is None:
                return set()
            try:
                rows = conn.execute("SELECT session_id FROM sessions").fetchall()
            except sqlite3.DatabaseError as exc:
                logger.warning(f"⚠️  Could not read index ids: {exc}")
                return set()
        return {r["session_id"] for r in rows}

    # ------------------------------------------------------ reconcile

    def reconcile(self, sessions: List[Dict[str, Any]]) -> int:
        """Make the index match what is on disk.

        ``sessions`` is a list of ``{session_id, created_at, model,
        text}`` dicts gathered by the caller from ``extracted.txt`` +
        ``meta.json``. Sessions missing from the index are inserted;
        index rows whose session no longer exists on disk are pruned.
        Idempotent. Returns the number of rows inserted.
        """
        present = self.session_ids()
        disk_ids = {
            str(s.get("session_id"))
            for s in sessions
            if s.get("session_id")
        }
        for stale in present - disk_ids:
            self.delete_session(stale)
        inserted = 0
        for s in sessions:
            sid = str(s.get("session_id") or "")
            text = str(s.get("text") or "")
            if not sid or sid in present or not text.strip():
                continue
            self.index_session(
                session_id=sid,
                created_at=str(s.get("created_at") or ""),
                text=text,
                model=s.get("model"),
                kind=s.get("kind"),
            )
            inserted += 1
        if inserted:
            logger.info(f"🔎 Search index reconciled — {inserted} session(s) added")
        return inserted
