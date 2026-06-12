"""Tests for the SQLite FTS5 search index over the session archive.

Covers the index unit (`ArchiveIndex`) directly and the archive-level
wiring (`SessionArchive.index_session` / `search` / `reconcile_index`).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.archive import SessionArchive
from src.archive_index import ArchiveIndex


# --------------------------------------------------------------- fixtures


@pytest.fixture
def index(tmp_path: Path) -> ArchiveIndex:
    return ArchiveIndex(tmp_path / "index.sqlite")


def _archive_with_extract(
    root: Path, text: str, incognito: bool = False
) -> tuple[SessionArchive, str]:
    """Create an archive holding one session whose extract is ``text``."""
    archive = SessionArchive(root)
    session = archive.new_session(incognito=incognito)
    session.write_extracted(
        text,
        model="gemini_flash",
        request_payload={},
        response_payload={},
        prompt_id="verbatim-merge",
    )
    session.write_meta()
    return archive, session.session_id


# --------------------------------------------------------- ArchiveIndex


def test_index_and_search_roundtrip(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "bakery receipt total 12.50")
    results = index.search("bakery")
    assert len(results) == 1
    assert results[0]["session_id"] == "s1"
    assert results[0]["model"] is None or isinstance(results[0]["model"], str)
    assert "bakery" in results[0]["snippet"].lower()


def test_upsert_replaces_row_by_session_id(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "old text mentions apple")
    index.index_session("s1", "2026-05-01T10:00:00", "new text mentions banana")
    assert index.search("apple") == []
    banana = index.search("banana")
    assert len(banana) == 1 and banana[0]["session_id"] == "s1"
    assert index.session_ids() == {"s1"}


def test_empty_text_is_not_indexed(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "   \n  ")
    assert index.session_ids() == set()


def test_empty_query_returns_no_results(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "hello world")
    assert index.search("") == []
    assert index.search("   ") == []


def test_fts5_special_chars_never_raise(index: ArchiveIndex) -> None:
    """Raw user input is wrapped as a phrase — FTS5 syntax in the query
    must not surface as a 500."""
    index.index_session("s1", "2026-05-01T10:00:00", 'a "quoted" phrase with * star')
    for query in ['"', '*', 'foo"bar', 'a OR b', '(', 'NEAR', '^', 'col:val']:
        assert isinstance(index.search(query), list)


def test_phrase_search_matches_embedded_quotes(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", 'he said "hello there" today')
    assert len(index.search('"hello there"')) == 1


def test_ranking_prefers_higher_term_frequency(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "the meeting notes")
    index.index_session("s2", "2026-05-02T10:00:00", "meeting meeting meeting agenda")
    results = index.search("meeting")
    assert [r["session_id"] for r in results][0] == "s2"


def test_limit_caps_result_count(index: ArchiveIndex) -> None:
    for i in range(5):
        index.index_session(f"s{i}", "2026-05-01T10:00:00", f"common word doc {i}")
    assert len(index.search("common", limit=2)) == 2


def test_delete_session_removes_row(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "findme please")
    index.delete_session("s1")
    assert index.search("findme") == []


def test_source_is_returned_in_results(index: ArchiveIndex) -> None:
    index.index_session(
        "s1", "2026-05-01T10:00:00", "bakery receipt", source="app-launcher"
    )
    results = index.search("bakery")
    assert len(results) == 1
    assert results[0]["source"] == "app-launcher"


def test_missing_source_returns_none(index: ArchiveIndex) -> None:
    index.index_session("s1", "2026-05-01T10:00:00", "no source here")
    assert index.search("source")[0]["source"] is None


def test_source_is_searchable(index: ArchiveIndex) -> None:
    """The indexed source column lets 'find the app-launcher OCRs' work."""
    index.index_session(
        "s1", "2026-05-01T10:00:00", "first take", source="app-launcher"
    )
    index.index_session("s2", "2026-05-01T10:01:00", "second take", source="webapp")
    results = index.search("app-launcher")
    assert [r["session_id"] for r in results] == ["s1"]


def test_outdated_schema_is_migrated(tmp_path: Path) -> None:
    """An index.sqlite built before the source column is rebuilt, not
    left broken — reconcile restores rows from disk."""
    import sqlite3

    db = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE VIRTUAL TABLE sessions USING fts5("
        " session_id UNINDEXED, created_at UNINDEXED, model UNINDEXED, text)"
    )
    conn.execute(
        "INSERT INTO sessions (session_id, created_at, model, text)"
        " VALUES ('old', '2026-01-01T00:00:00', 'm', 'legacy row')"
    )
    conn.commit()
    conn.close()

    idx = ArchiveIndex(db)
    # First write provisions the connection → detects the missing column
    # → drops + recreates with the new schema.
    idx.index_session(
        "s1", "2026-05-01T10:00:00", "fresh row", source="app-launcher"
    )
    results = idx.search("fresh")
    assert len(results) == 1 and results[0]["source"] == "app-launcher"


def test_corrupt_index_file_is_rebuilt(tmp_path: Path) -> None:
    db = tmp_path / "index.sqlite"
    db.write_bytes(b"this is not a sqlite database at all")
    idx = ArchiveIndex(db)
    idx.index_session("s1", "2026-05-01T10:00:00", "recovered after corruption")
    assert len(idx.search("recovered")) == 1


def test_reads_on_fresh_install_create_no_file(tmp_path: Path) -> None:
    """A search or delete before anything is indexed must not
    materialise an empty index.sqlite."""
    db = tmp_path / "index.sqlite"
    idx = ArchiveIndex(db)
    assert idx.search("anything") == []
    assert idx.session_ids() == set()
    idx.delete_session("nope")
    assert not db.exists()


# ----------------------------------------------- SessionArchive wiring


def test_reconcile_builds_index_from_disk(tmp_path: Path) -> None:
    archive, sid = _archive_with_extract(tmp_path / "archive", "invoice from acme corp")
    assert archive.search("acme") == []  # not indexed live
    assert archive.reconcile_index() == 1
    results = archive.search("acme")
    assert len(results) == 1 and results[0]["session_id"] == sid


def test_reconcile_is_idempotent(tmp_path: Path) -> None:
    archive, _ = _archive_with_extract(tmp_path / "archive", "hello reconcile")
    assert archive.reconcile_index() == 1
    assert archive.reconcile_index() == 0


def test_reconcile_skips_incognito_sessions(tmp_path: Path) -> None:
    archive, _ = _archive_with_extract(
        tmp_path / "archive", "secret incognito body", incognito=True
    )
    assert archive.reconcile_index() == 0
    assert archive.search("incognito") == []


def test_reconcile_skips_empty_extract(tmp_path: Path) -> None:
    archive, _ = _archive_with_extract(tmp_path / "archive", "")
    assert archive.reconcile_index() == 0


def test_reconcile_prunes_session_gone_from_disk(tmp_path: Path) -> None:
    archive = SessionArchive(tmp_path / "archive")
    session = archive.new_session()
    session.write_extracted(
        "findme please",
        model="m",
        request_payload={},
        response_payload={},
        prompt_id="p",
    )
    session.write_meta()
    archive.reconcile_index()
    assert len(archive.search("findme")) == 1
    # Simulate an external rm — bypass delete_session entirely.
    shutil.rmtree(session.folder)
    archive.reconcile_index()
    assert archive.search("findme") == []


def test_reconcile_carries_source(tmp_path: Path) -> None:
    archive = SessionArchive(tmp_path / "archive")
    session = archive.new_session(source="app-launcher")
    session.write_extracted(
        "invoice from acme corp",
        model="gemini_flash",
        request_payload={},
        response_payload={},
        prompt_id="verbatim-merge",
    )
    session.write_meta()
    assert archive.reconcile_index() == 1
    results = archive.search("acme")
    assert len(results) == 1 and results[0]["source"] == "app-launcher"


def test_archive_index_session_skips_incognito(tmp_path: Path) -> None:
    archive, sid = _archive_with_extract(
        tmp_path / "archive", "incognito body text", incognito=True
    )
    session = archive.get(sid)
    assert session is not None
    archive.index_session(session)
    assert archive.search("incognito") == []


def test_archive_delete_session_drops_index_row(tmp_path: Path) -> None:
    archive = SessionArchive(tmp_path / "archive")
    session = archive.new_session()
    session.write_extracted(
        "delete me text",
        model="m",
        request_payload={},
        response_payload={},
        prompt_id="p",
    )
    session.write_meta()
    archive.index_session(session)
    assert len(archive.search("delete")) == 1
    archive.delete_session(session.session_id)
    assert archive.search("delete") == []
