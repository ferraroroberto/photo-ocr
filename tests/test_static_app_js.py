"""Lightweight source pins on the webapp JS — guards against accidental
regression of key invariants without needing a JS test runner.

The webapp JS is split into ES modules under static/; these checks run
against the concatenation of every module so they survive code moving
between files."""

from __future__ import annotations

from pathlib import Path

import pytest

STATIC_DIR = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "webapp"
    / "static"
)


@pytest.fixture(scope="module")
def js_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(STATIC_DIR.glob("*.js"))
    )


def test_clipboard_uses_text_plain_mime(js_source: str) -> None:
    # voice-transcriber's troubleshooting flagged styled-DOM leakage with
    # writeText on some Safari versions — assert we use the explicit MIME.
    assert "'text/plain'" in js_source
    assert "ClipboardItem" in js_source


def test_token_keys_are_namespaced(js_source: str) -> None:
    # Avoid colliding with voice-transcriber's localStorage on phones that
    # serve both apps from the same origin.
    assert "photo-ocr.token" in js_source
    assert "photo-ocr.promptId" in js_source
    assert "photo-ocr.model" in js_source


def test_history_page_size(js_source: str) -> None:
    assert "HISTORY_PAGE_SIZE = 10" in js_source


def test_extract_uses_authorization_header(js_source: str) -> None:
    assert "Authorization" in js_source
    assert "Bearer" in js_source


def test_extract_uses_timeout_and_status_polling(js_source: str) -> None:
    assert "AbortController" in js_source
    assert "/extract/status" in js_source
    assert "Chunk " in js_source
    assert "Merging" in js_source


def test_extract_syncs_visible_photo_order(js_source: str) -> None:
    assert "/photos/reorder" in js_source
    assert "syncPhotoOrder" in js_source
    assert "visual only" not in js_source.lower()


def test_history_renders_source_badge(js_source: str) -> None:
    # Externally-sourced takes (app-launcher, api, …) get a History badge;
    # the unmarked default is the manual PWA take ("webapp").
    assert "source-badge" in js_source
    assert "'webapp'" in js_source
