"""Tests for src/archive.py."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from src.archive import PhotoMeta, SessionArchive


def test_new_session_creates_dated_folder(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    s = arc.new_session()
    assert s.folder.exists()
    assert (s.folder / "meta.json").exists()
    parts = s.folder.relative_to(tmp_path).parts
    # YYYY/MM/DD/HH-MM-SS-xxxxxxxx
    assert len(parts) == 4
    assert s.session_id == parts[3]


def test_record_and_remove_photo(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    s = arc.new_session()
    # Drop two placeholder files; record_photo just updates meta.
    (s.folder / "01.jpg").write_bytes(b"a")
    (s.folder / "02.jpg").write_bytes(b"b")
    s.record_photo(PhotoMeta(1, "01.jpg", 10, 10, 1))
    s.record_photo(PhotoMeta(2, "02.jpg", 10, 10, 1))
    assert len(s.meta.photos) == 2

    # Remove first; second should be renumbered to 01.jpg.
    assert s.remove_photo(1) is True
    assert len(s.meta.photos) == 1
    assert s.meta.photos[0].sequence_index == 1
    assert s.meta.photos[0].path == "01.jpg"
    assert (s.folder / "01.jpg").exists()
    assert not (s.folder / "02.jpg").exists()


def test_remove_unknown_returns_false(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    s = arc.new_session()
    s.record_photo(PhotoMeta(1, "01.jpg", 1, 1, 1))
    assert s.remove_photo(99) is False


def test_list_sessions_newest_first(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    s1 = arc.new_session(now=datetime(2026, 1, 1, 10, 0, 0))
    s2 = arc.new_session(now=datetime(2026, 5, 12, 14, 0, 0))
    listed = arc.list_sessions()
    ids = [s.session_id for s in listed]
    assert ids.index(s2.session_id) < ids.index(s1.session_id)


def test_incognito_hidden_from_list(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    arc.new_session(incognito=True)
    visible = arc.new_session(incognito=False)
    listed = arc.list_sessions()
    ids = [s.session_id for s in listed]
    assert visible.session_id in ids
    assert all(not s.meta.incognito for s in listed)


def test_pagination(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    for i in range(5):
        arc.new_session(now=datetime(2026, 5, 10 + i, 12, 0, 0))
    first = arc.list_sessions(limit=2, offset=0)
    second = arc.list_sessions(limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 2
    # No duplicates between pages.
    assert {s.session_id for s in first}.isdisjoint({s.session_id for s in second})
    assert arc.count_sessions() == 5


def test_delete_session(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    s = arc.new_session()
    folder = s.folder
    assert arc.delete_session(s.session_id) is True
    assert not folder.exists()
    assert arc.delete_session(s.session_id) is False


def test_cleanup_older_than(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    old = arc.new_session()
    new = arc.new_session()
    # Backdate the "old" session's mtime by 60 days.
    ancient = time.time() - 60 * 86400
    import os
    os.utime(old.folder, (ancient, ancient))

    removed = arc.cleanup_older_than(days=30)
    assert removed == 1
    assert arc.get(new.session_id) is not None
    assert arc.get(old.session_id) is None


def test_write_extracted_persists_files(tmp_path: Path) -> None:
    arc = SessionArchive(root=tmp_path)
    s = arc.new_session()
    s.record_photo(PhotoMeta(1, "01.jpg", 10, 10, 1))
    (s.folder / "01.jpg").write_bytes(b"x")
    s.write_extracted(
        "extracted text",
        model="gemini_flash",
        request_payload={"model": "gemini_flash", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": "extracted text"}]},
        prompt_id="verbatim-merge",
        duration_s=1.23,
    )
    s.write_meta()

    rehydrated = arc.get(s.session_id)
    assert rehydrated is not None
    assert rehydrated.read_extracted() == "extracted text"
    assert rehydrated.meta.model == "gemini_flash"
    assert rehydrated.meta.prompt_id == "verbatim-merge"
    assert rehydrated.meta.extract_succeeded is True
    assert rehydrated.meta.extracted_chars == len("extracted text")
    assert rehydrated.meta.extract_duration_s == 1.23
