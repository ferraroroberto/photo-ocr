"""Browser regression for the core capture flow: photo upload, this take's
model and style chosen from the Capture chips, non-blocking extract progress
on the meter, then the result card (meta line, Copy text, the photos row,
New take).

The test uses the real SPA and real photo upload path, but intercepts
only the OCR job endpoints. That keeps it deterministic and independent
of the local LLM hub while still proving the UI advances through chunk
progress and renders the final result.

Upload half (runs under both projections, notably the WebKit/iPhone one,
since the phone camera path is photo-ocr's primary surface): picking an
image feeds it through ``handleFilePick`` → ``uploadPhoto`` →
``POST /api/sessions/{id}/photos``; a thumbnail reaches ``ready`` only
after that upload succeeds server-side, so a ``ready`` thumbnail is proof
the photo reached a real session. The test then confirms the session
exists via the API, and cleans it up.

Out of scope: the real OCR round-trip — it depends on the local-llm-hub
being reachable and is covered by the ``network``-marked TestClient tests.
"""

from __future__ import annotations

import io
import json
import re

import pytest
import requests
from playwright.sync_api import Page, Route, expect

from tests.e2e._surface import surface

pytestmark = pytest.mark.smoke

# Gap between two extract/status polls — must match sleep(1000) in
# app/webapp/static/poll.js.
_POLL_INTERVAL_MS = 1_000


def _jpeg_bytes() -> bytes:
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed for the upload test")
    buf = io.BytesIO()
    pil.new("RGB", (320, 240), color=(180, 190, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _sessions_by_id(base_url: str) -> dict[str, dict]:
    res = requests.get(
        f"{base_url}/api/sessions?limit=50&offset=0", verify=False, timeout=5
    )
    res.raise_for_status()
    return {s["session_id"]: s for s in res.json().get("sessions", [])}


def test_photo_upload_then_extract_progress(
    authed_page: Page, base_url: str
) -> None:
    before = set(_sessions_by_id(base_url))
    status_calls = 0
    extract_bodies: list[dict] = []

    def handle_config(route: Route) -> None:
        # Two known models and an invented style, so the chips have a choice
        # to make whatever this checkout's config holds.
        body = route.fetch().json()
        body["ocr_models_available"] = ["gemini_flash", "claude_opus"]
        body["ocr_model_default"] = "gemini_flash"
        body["ocr_prompts"] = list(body.get("ocr_prompts") or []) + [
            {"id": "invented-style", "label": "Invented style", "description": "", "system": "x"}
        ]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def handle_extract(route: Route) -> None:
        extract_bodies.append(json.loads(route.request.post_data or "{}"))
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
        authed_page.route("**/api/config", handle_config)
        authed_page.route("**/api/sessions/*/extract", handle_extract)
        authed_page.route("**/api/sessions/*/extract/status", handle_status)
        authed_page.clock.install()
        # Capture the clipboard write (permissions differ per engine).
        authed_page.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {value: "
            "{writeText: t => { window.__copied = t; return Promise.resolve(); }}})"
        )
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
        # A thumbnail flips to `ready` only after POST /photos succeeds.
        expect(authed_page.locator("#thumbStrip li.thumb.ready")).to_have_count(
            1, timeout=10_000
        )

        # With a ready photo, Extract appears, enabled.
        expect(authed_page.locator("#extractBtn")).to_be_visible()
        expect(authed_page.locator("#extractBtn")).to_be_enabled()

        # This take's model and style come from the chips' picker sheets,
        # which mark the current choice; Extract must post what was picked.
        model_chip = authed_page.locator("#takeModel")
        expect(model_chip).to_contain_text("Gemini Flash")
        model_chip.click()
        picker = authed_page.locator("dialog.sheet")
        expect(picker.locator("[aria-current='true']")).to_have_text("Gemini Flash")
        surface(authed_page, "capture.model-picker")
        picker.get_by_role("button", name="Claude Opus").click()
        expect(picker).to_have_count(0)
        expect(model_chip).to_contain_text("Claude Opus")
        authed_page.locator("#takeStyle").click()
        surface(authed_page, "capture.style-picker")
        authed_page.locator("dialog.sheet").get_by_role("button", name="Invented style").click()
        expect(authed_page.locator("#takeStyle")).to_contain_text("Invented style")

        # Server-side confirmation: a new session now exists with the photo.
        sessions = _sessions_by_id(base_url)
        new_ids = set(sessions) - before
        assert len(new_ids) == 1, (
            f"expected exactly one new session, got {len(new_ids)}: {new_ids}"
        )
        sid = next(iter(new_ids))
        assert sessions[sid]["photo_count"] >= 1, (
            f"new session {sid} has no photos: {sessions[sid]}"
        )

        authed_page.locator("#extractBtn").click()

        # In flight: the meter counts job parts (never photos) and the header
        # names the exception; the outcome line stays empty until the end.
        meter = authed_page.locator("#extractMeter")
        headline = authed_page.locator("#captureHeadline")
        result = authed_page.locator("#takeResult")
        expect(meter).to_contain_text("part 1 of 2", timeout=5_000)
        expect(headline).to_contain_text("Reading")
        expect(result).to_be_hidden()
        assert extract_bodies == [{"model": "claude_opus", "prompt_id": "invented-style"}], extract_bodies
        surface(authed_page, "capture.extracting")
        # The SPA waits POLL_INTERVAL_MS between status polls; fast-forward
        # its clock instead of sleeping the real second twice.
        authed_page.clock.run_for(_POLL_INTERVAL_MS)
        expect(meter).to_contain_text("part 2 of 2", timeout=5_000)
        expect(meter.locator(".meter")).to_have_attribute("aria-valuenow", "50")
        authed_page.clock.run_for(_POLL_INTERVAL_MS)
        expect(authed_page.locator("#extracted")).to_have_value(
            "done text", timeout=5_000
        )
        # The result takes the card: meta line, Copy text as the primary,
        # the photos folded to one row; no Retry on a fully read take.
        expect(result).to_be_visible()
        expect(authed_page.locator("#takeBuild")).to_be_hidden()
        expect(authed_page.locator("#resultMeta")).to_contain_text("3.2 s")
        expect(authed_page.locator("#resultMeta")).to_contain_text("saved to History")
        expect(authed_page.locator("#retryMissing")).to_be_hidden()
        expect(meter).to_be_hidden()
        expect(headline).not_to_contain_text("Reading")
        copy = authed_page.locator("#copyExtracted")
        expect(copy).to_have_class(re.compile(r"\bbutton-primary\b"))
        surface(authed_page, "capture.result")
        copy.click()
        expect(copy).to_contain_text("Copied")
        assert authed_page.evaluate("window.__copied") == "done text"

        # "1 photo ›" reopens the strip (Extract again is there), and the
        # text is one row away.
        authed_page.locator("#showPhotos").click()
        expect(authed_page.locator("#takeBuild")).to_be_visible()
        expect(authed_page.locator("#extractBtn")).to_contain_text("Extract again")
        authed_page.locator("#showText").click()
        expect(result).to_be_visible()

        # New take: an empty take again.
        authed_page.locator("#newTake").click()
        expect(authed_page.locator("#takeEmpty")).to_be_visible()
        expect(result).to_be_hidden()
    finally:
        # Don't leave test sessions in the archive.
        new_ids = set(_sessions_by_id(base_url)) - before
        for sid in new_ids:
            try:
                requests.delete(
                    f"{base_url}/api/sessions/{sid}", verify=False, timeout=5
                )
            except requests.RequestException:
                pass
