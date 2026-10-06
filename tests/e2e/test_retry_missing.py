"""Browser regression for "Retry missing" (#168): a partly read take shows one
action beside the result, a click re-reads only the unread photos, and the
outcome is one toast.

The real SPA and the real photo upload run; only the OCR job endpoints are
intercepted, so the test is deterministic and never touches the hub. The
photo is a synthetic fill, never a real document.
"""

from __future__ import annotations

import io
import json

import pytest
import requests
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.smoke

_PARTIAL = "page 1\n[missing: photo 2 (02.jpg) could not be read]\npage 3"
_WHOLE = "page 1\npage 2\npage 3"


def _jpeg_bytes() -> bytes:
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed for the upload test")
    buf = io.BytesIO()
    pil.new("RGB", (320, 240), color=(180, 190, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _session_ids(base_url: str) -> set:
    res = requests.get(
        f"{base_url}/api/sessions?limit=50&offset=0", verify=False, timeout=5
    )
    res.raise_for_status()
    return {s["session_id"] for s in res.json().get("sessions", [])}


def _status(phase: str, extracted: str, missing: list) -> dict:
    return {
        "session_id": "mocked",
        "phase": phase,
        "chunks_total": 1,
        "chunks_done": 1,
        "model": "gemini_flash",
        "prompt_id": "verbatim-merge",
        "duration_s": 3.2,
        "extract_succeeded": True,
        "extracted_chars": len(extracted),
        "error": None,
        "reused": False,
        "missing_photos": missing,
        "retry_error": None,
        "extracted": extracted,
    }


def test_retry_missing_appears_only_when_photos_are_missing_and_fills_the_gap(
    authed_page: Page, base_url: str
) -> None:
    before = _session_ids(base_url)
    retried = False

    def json_route(body: dict):
        def handler(route: Route) -> None:
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )
        return handler

    def handle_status(route: Route) -> None:
        if retried:
            body = _status("succeeded", _WHOLE, [])
        else:
            body = _status("succeeded", _PARTIAL, ["02.jpg"])
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    def handle_retry(route: Route) -> None:
        nonlocal retried
        retried = True
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_status("queued", _PARTIAL, ["02.jpg"])),
        )

    try:
        authed_page.route(
            "**/api/sessions/*/extract",
            json_route(_status("queued", "", [])),
        )
        authed_page.route("**/api/sessions/*/extract/status", handle_status)
        authed_page.route("**/api/sessions/*/retry-missing", handle_retry)
        authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
        authed_page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)

        retry = authed_page.locator("#retryMissing")
        expect(retry).to_be_hidden()

        authed_page.set_input_files(
            "#galleryInput",
            files=[
                {"name": "take.jpg", "mimeType": "image/jpeg", "buffer": _jpeg_bytes()}
            ],
        )
        expect(authed_page.locator("#thumbStrip li.thumb.ready")).to_have_count(
            1, timeout=10_000
        )
        authed_page.locator("#extractBtn").click()

        expect(authed_page.locator("#extracted")).to_have_value(_PARTIAL, timeout=5_000)
        expect(retry).to_be_visible()

        retry.click()

        expect(authed_page.locator("#extracted")).to_have_value(_WHOLE, timeout=5_000)
        expect(retry).to_be_hidden()
        expect(authed_page.locator("#toast")).to_contain_text("All photos read")
    finally:
        for sid in _session_ids(base_url) - before:
            try:
                requests.delete(
                    f"{base_url}/api/sessions/{sid}", verify=False, timeout=5
                )
            except requests.RequestException:
                pass
