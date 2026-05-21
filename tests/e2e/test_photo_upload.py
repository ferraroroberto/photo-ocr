"""Regression net for the core feature — photo capture/upload — driven
through the SPA under both projections (notably the WebKit/iPhone one,
since the phone camera path is photo-ocr's primary surface).

What this pins: picking an image feeds it through ``handleFilePick`` →
``uploadPhoto`` → ``POST /api/sessions/{id}/photos``; a thumbnail reaches
the ``ready`` state only after that upload succeeds server-side, so a
``ready`` thumbnail is proof the photo reached a real session. The test
then confirms the session exists via the API and cleans it up.

Out of scope: the OCR extract itself — that round-trip depends on the
local-llm-hub being reachable and is covered by the ``network``-marked
TestClient tests, not this browser smoke test.
"""

from __future__ import annotations

import io

import pytest
import requests
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke


def _jpeg_bytes() -> bytes:
    """A small but valid JPEG the webapp's image validator accepts."""
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed for the upload test")
    buf = io.BytesIO()
    pil.new("RGB", (320, 240), color=(200, 210, 220)).save(buf, format="JPEG")
    return buf.getvalue()


def _session_ids(base_url: str) -> set[str]:
    res = requests.get(
        f"{base_url}/api/sessions?limit=50&offset=0", verify=False, timeout=5
    )
    res.raise_for_status()
    return {s["session_id"] for s in res.json().get("sessions", [])}


def test_photo_upload_reaches_a_session(
    authed_page: Page, base_url: str
) -> None:
    before = _session_ids(base_url)
    new_ids: set[str] = set()
    try:
        authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
        authed_page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)

        # Feed an image through the gallery picker — same handleFilePick
        # path as the camera input, minus the OS camera UI.
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
        ready_thumb = authed_page.locator("#thumbStrip li.thumb.ready")
        expect(ready_thumb).to_have_count(1, timeout=10_000)

        # With a ready photo, Extract must be enabled.
        expect(authed_page.locator("#extractBtn")).to_be_enabled()

        # Server-side confirmation: a new session now exists with the photo.
        after = _session_ids(base_url)
        new_ids = after - before
        assert len(new_ids) == 1, (
            f"expected exactly one new session, got {len(new_ids)}: {new_ids}"
        )
        sid = next(iter(new_ids))
        detail = requests.get(
            f"{base_url}/api/sessions?limit=50&offset=0", verify=False, timeout=5
        ).json()
        match = next(
            s for s in detail["sessions"] if s["session_id"] == sid
        )
        assert match["photo_count"] >= 1, (
            f"new session {sid} has no photos: {match}"
        )
    finally:
        # Don't leave test sessions in the archive.
        for sid in new_ids:
            try:
                requests.delete(
                    f"{base_url}/api/sessions/{sid}", verify=False, timeout=5
                )
            except requests.RequestException:
                pass
