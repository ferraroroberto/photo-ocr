"""Lightweight source pins on the webapp JS — guards against accidental
regression of key invariants without needing a JS test runner.

The webapp JS is split into ES modules under static/; these checks run
against the concatenation of every module so they survive code moving
between files."""

from __future__ import annotations

import re
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
    # Progress counts job parts on the meter, never photos (#194).
    assert "extractProgress" in js_source
    assert "'part '" in js_source
    assert "merging the parts" in js_source


def test_extract_syncs_visible_photo_order(js_source: str) -> None:
    assert "/photos/reorder" in js_source
    assert "syncPhotoOrder" in js_source
    assert "visual only" not in js_source.lower()


def test_history_renders_source_badge(js_source: str) -> None:
    # Externally-sourced takes (app-launcher, api, …) get a neutral status
    # chip in History; the unmarked default is the manual PWA take ("webapp").
    assert "chipEl(take.source, 'neutral')" in js_source
    assert "'webapp'" in js_source


def test_one_status_chip_class() -> None:
    # design.md "status chip": one class with a data-tone, never a chip style
    # per surface. The two one-offs it replaced must not come back.
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert '.chip[data-tone="attention"]' in styles
    assert "warn-chip" not in styles
    assert "source-badge" not in styles


def test_no_native_dialogs(js_source: str) -> None:
    # design.md: confirm, rename and detail use the vendored dialog (the
    # confirm sheet in sheet.js), never the browser's own pop-ups.
    assert not re.search(r"\b(confirm|alert|prompt)\(", js_source)
    assert "confirmSheet(" in js_source


def test_toast_is_the_vendored_neutral_one(js_source: str) -> None:
    # Fleet COLOR-05: only an error tints. A local `.toast` rule or a
    # `'good'` kind is how the green success toast comes back.
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".toast" not in styles
    assert "_vendored/toast/toast.js" in js_source
    assert ", 'good')" not in js_source


def test_no_success_green_token() -> None:
    # Fleet COLOR-04: on-state is the accent, never a success green. The
    # vendored switch reads --accent-fill; a local --on is how a green
    # on-colour (and the copy flash that used it) comes back.
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert "--on:" not in styles
    assert "var(--on)" not in styles


def test_one_clipboard_path(js_source: str) -> None:
    # The History row copy once re-implemented the clipboard and lost the
    # Safari text/plain workaround and the failure path; both callers must go
    # through the shared copyText().
    assert js_source.count("navigator.clipboard.writeText(") == 1
    assert js_source.count("await copyText(") == 2


def test_take_jobs_share_one_busy_cycle(js_source: str) -> None:
    # Extract, Redo and Retry missing run inside runTakeJob(), which alone
    # owns the busy flag.
    assert js_source.count("state.busy = true") == 1
    assert js_source.count("await runTakeJob(") == 3


_EXPORT_RE = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+(\w+)", re.M)


def test_no_dead_js_exports() -> None:
    """Every name an app module exports is referenced somewhere else in static/.

    A helper nothing imports reads as live behavior (an unused auth helper sat
    in state.js); delete it, or stop exporting it if only its own file uses it.
    Vendored components are byte-identical copies and are not judged here."""
    app_files = sorted(STATIC_DIR.glob("*.js"))
    every_file = [*STATIC_DIR.rglob("*.js")]
    texts = {p: p.read_text(encoding="utf-8") for p in every_file}
    dead = []
    for path in app_files:
        for name in _EXPORT_RE.findall(texts[path]):
            word = re.compile(rf"\b{re.escape(name)}\b")
            used_elsewhere = any(
                word.search(text) for other, text in texts.items() if other != path
            )
            used_inside = len(word.findall(texts[path])) > 1
            if not (used_elsewhere or used_inside):
                dead.append(f"{path.name}:{name}")
    assert not dead, f"exported but never referenced: {dead}"
