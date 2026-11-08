"""The runtime writers refuse a path under a code folder and write elsewhere."""

from __future__ import annotations

import logging

import pytest

from src import runtime_files


def test_checked_refuses_every_code_folder() -> None:
    for name in runtime_files.CODE_FOLDERS:
        with pytest.raises(ValueError, match="code folder"):
            runtime_files.checked(runtime_files.REPO_ROOT / name / "x.log")


def test_checked_refuses_the_code_folder_itself() -> None:
    with pytest.raises(ValueError):
        runtime_files.checked(runtime_files.REPO_ROOT / "src")


def test_checked_allows_gitignored_live_state_in_the_checkout() -> None:
    for name in ("webapp", "config", "archive"):
        target = runtime_files.REPO_ROOT / name / "x.log"
        assert runtime_files.checked(target) == target.resolve()


def test_writers_write_outside_the_code_folders(tmp_path) -> None:
    folder = runtime_files.make_dirs(tmp_path / "a" / "b")
    runtime_files.save_text(folder / "t.txt", "héllo")
    runtime_files.save_bytes(folder / "b.bin", b"\x00\x01")
    with runtime_files.open_for_write(folder / "t.txt", "a", encoding="utf-8") as fh:
        fh.write("!")
    assert (folder / "t.txt").read_text(encoding="utf-8") == "héllo!"
    assert (folder / "b.bin").read_bytes() == b"\x00\x01"


def test_file_handler_logs_to_the_named_file(tmp_path) -> None:
    handler = runtime_files.file_handler(tmp_path / "x.log")
    logger = logging.getLogger("photo_ocr.test_runtime_files")
    logger.addHandler(handler)
    try:
        logger.warning("line")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert "line" in (tmp_path / "x.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("writer", ["make_dirs", "save_text", "save_bytes", "open_for_write", "file_handler"])
def test_every_writer_refuses_a_code_folder(writer: str) -> None:
    target = runtime_files.REPO_ROOT / "src" / "never_written"
    args = {
        "make_dirs": (target,),
        "save_text": (target, "x"),
        "save_bytes": (target, b"x"),
        "open_for_write": (target,),
        "file_handler": (target,),
    }[writer]
    with pytest.raises(ValueError):
        getattr(runtime_files, writer)(*args)
    assert not target.exists()
