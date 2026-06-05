"""Browser regression for non-blocking extract progress.

The test uses the real SPA and real photo upload path, but intercepts
only the OCR job endpoints. That keeps it deterministic and independent
of the local LLM hub while still proving the UI advances through chunk
progress and renders the final result.
"""

from __future__ import annotations

import io
import json

import pytest
import requests
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.smoke


def _jpeg_bytes() -> bytes:
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed for the upload test")
    buf = io.BytesIO()
    pil.new("RGB", (320, 240), color=(180, 190, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _session_ids(base_url: str) -> set[str]:
    res = requests.get(
        f"{base_url}/api/sessions?limit=50&offset=0", verify=False, timeout=5
    )
    res.raise_for_status()
    return {s["session_id"] for s in res.json().get("sessions", [])}


def test_extract_progress_advances_and_renders_result(
    authed_page: Page, base_url: str
) -> None:
    before = _session_ids(base_url)
    new_ids: set[str] = set()
    status_calls = 0

    def handle_extract(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "session_id": "mocked",
                    "phase": "queued",
                    "chunks_total": 2,
                    "chunks_done": 0,
                    "model": "gemini_flash",
                    "prompt_id": "verbatim-merge",
                    "reused": False,
                }
            ),
        )

    def handle_status(route: Route) -> None:
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            phase = "running"
            chunks_done = 0
            extracted = None
        elif status_calls == 2:
            phase = "running"
            chunks_done = 1
            extracted = None
        else:
            phase = "succeeded"
            chunks_done = 2
            extracted = "done text"

        body = {
            "session_id": "mocked",
            "phase": phase,
            "chunks_total": 2,
            "chunks_done": chunks_done,
            "model": "gemini_flash",
            "prompt_id": "verbatim-merge",
            "duration_s": 3.2,
            "extract_succeeded": phase == "succeeded",
            "extracted_chars": len(extracted or ""),
            "error": None,
            "reused": False,
        }
        if extracted is not None:
            body["extracted"] = extracted
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(body),
        )

    try:
        authed_page.route("**/api/sessions/*/extract", handle_extract)
        authed_page.route("**/api/sessions/*/extract/status", handle_status)
        authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
        authed_page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)

        authed_page.set_input_files(
            "#galleryInput",
            files=[
                {
                    "name": "take.jpg",
                    "mimeType": "image/jpeg",
                    "buffer": _jpeg_bytes(),
                }
            ],
        )
        expect(authed_page.locator("#thumbStrip li.thumb.ready")).to_have_count(
            1, timeout=10_000
        )
        authed_page.locator("#extractBtn").click()

        status = authed_page.locator("#captureStatus")
        expect(status).to_contain_text("Chunk 1 of 2", timeout=5_000)
        expect(status).to_contain_text("Chunk 2 of 2", timeout=5_000)
        expect(authed_page.locator("#extracted")).to_have_value(
            "done text", timeout=5_000
        )
        expect(status).to_contain_text("Done in 3.2 s", timeout=5_000)
    finally:
        after = _session_ids(base_url)
        new_ids = after - before
        for sid in new_ids:
            try:
                requests.delete(
                    f"{base_url}/api/sessions/{sid}", verify=False, timeout=5
                )
            except requests.RequestException:
                pass
