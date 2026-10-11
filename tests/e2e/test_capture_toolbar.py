"""Browser regression for the Capture photo sheet (#147, #195).

A thumbnail is a 96px tile; per-photo overlay controls on it sat under the
44px hit-target floor and the tile clips its overflow (#147). Tapping a tile
opens one sheet with the photo and every photo action (move left / right,
Retake and Keep for a quality-warned photo, Remove) as real 44px targets; it
replaced the selection toolbar and the separate full-size preview (#195).

The session endpoints are mocked (no real archive session is created); the
quality gate runs for real on the picked pixels, so a flat grey photo is the
quality-warned one and a noise photo is the clean one. Runs under both
projections — the iPhone one is the touch surface the targets are for.
"""

from __future__ import annotations

import io
import json
import os

import pytest
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.smoke

_HIT_MIN = 44
_SHEET = "dialog.photo-sheet-dialog"


def _jpeg(*, noisy: bool) -> bytes:
    """A sharp noise photo (no quality warning) or a flat one (blurry)."""
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed to pick photos")
    if noisy:
        img = pil.frombytes("RGB", (128, 128), os.urandom(128 * 128 * 3))
    else:
        img = pil.new("RGB", (128, 128), color=(180, 190, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _file(name: str, *, noisy: bool) -> dict:
    return {"name": name, "mimeType": "image/jpeg", "buffer": _jpeg(noisy=noisy)}


def _mock_session_api(page: Page) -> list[str]:
    """Stub the take's session endpoints; returns the DELETE paths seen."""
    deleted: list[str] = []
    uploaded = 0

    def sessions(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"session_id": "mocked"}),
        )

    def photos(route: Route) -> None:
        nonlocal uploaded
        uploaded += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"added": [{"sequence_index": uploaded}], "photos": []}),
        )

    def photo(route: Route) -> None:
        if route.request.method != "DELETE":  # `photos/reorder` also matches
            route.fallback()
            return
        deleted.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"photos": []}),
        )

    def reorder(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"photos": []}),
        )

    def config(route: Route) -> None:
        # The quality gate is a config flag; pin it on so the warned photo is
        # warned whatever this checkout's webapp_config.json says.
        body = route.fetch().json()
        body["quality_gate_enabled"] = True
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page.route("**/api/config", config)
    page.route("**/api/sessions", sessions)
    page.route("**/api/sessions/mocked/photos", photos)
    page.route("**/api/sessions/mocked/photos/reorder", reorder)
    page.route("**/api/sessions/mocked/photos/*", photo)
    return deleted


def _srcs(page: Page) -> list[str]:
    return page.evaluate(
        "[...document.querySelectorAll('#thumbStrip li.thumb img')].map(i => i.src)"
    )


def _wait_until(page: Page, predicate, timeout_ms: int = 5_000) -> None:
    """Poll a Python-side condition (a route handler filled a list)."""
    for _ in range(timeout_ms // 50):
        if predicate():
            return
        page.wait_for_timeout(50)
    raise AssertionError("condition not met in time")


def _assert_hit_targets(page: Page, selectors: list[str]) -> None:
    for sel in selectors:
        for i, box in enumerate(
            b.bounding_box() for b in page.locator(sel).all() if b.is_visible()
        ):
            assert box is not None, f"{sel}[{i}] has no box"
            assert box["width"] >= _HIT_MIN and box["height"] >= _HIT_MIN, (
                f"{sel}[{i}] is {box['width']:.0f}x{box['height']:.0f}px, "
                f"under the {_HIT_MIN}px floor"
            )


def test_photo_sheet_holds_every_photo_action(authed_page: Page, base_url: str) -> None:
    deleted = _mock_session_api(authed_page)
    errors: list[str] = []
    authed_page.on("pageerror", lambda exc: errors.append(str(exc)))
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)

    authed_page.set_input_files(
        "#galleryInput",
        files=[
            _file("a.jpg", noisy=True),
            _file("b.jpg", noisy=False),  # the quality-warned one
            _file("c.jpg", noisy=True),
        ],
    )
    thumbs = authed_page.locator("#thumbStrip li.thumb.ready")
    expect(thumbs).to_have_count(3, timeout=10_000)
    expect(authed_page.locator("#thumbStrip li.thumb.warned")).to_have_count(1)

    # A tile is one 44px+ button that opens its photo's sheet.
    tiles = authed_page.locator("#thumbStrip .thumb-select")
    expect(tiles.nth(0)).to_have_attribute("aria-haspopup", "dialog")
    _assert_hit_targets(authed_page, ["#thumbStrip button"])

    tiles.nth(0).click()
    sheet = authed_page.locator(_SHEET)
    expect(sheet).to_be_visible()
    expect(sheet.locator("h2")).to_have_text("Photo 1 of 3")
    expect(sheet.locator(".photo-sheet-frame img")).to_have_count(1)
    move_left = sheet.get_by_role("button", name="Move left")
    move_right = sheet.get_by_role("button", name="Move right")
    expect(move_left).to_be_disabled()  # first photo
    expect(move_right).to_be_enabled()
    # Not warned: Retake / Keep are not offered; Remove is, last.
    expect(sheet.get_by_role("button", name="Retake")).to_have_count(0)
    expect(sheet.locator(".sheet-action").last).to_have_text("Remove photo")
    # The × is the vendored modal close (34px + its ::before band to 44).
    _assert_hit_targets(authed_page, [f"{_SHEET} button:not(.detail-close)"])

    # Move right: the photo travels, the sheet stays open on it.
    before = _srcs(authed_page)
    move_right.click()
    after = _srcs(authed_page)
    assert after[1] == before[0] and after[0] == before[1], (before, after)
    expect(sheet.locator("h2")).to_have_text("Photo 2 of 3")
    expect(move_left).to_be_enabled()
    # Escape closes it; focus returns to that photo's tile.
    authed_page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)
    expect(tiles.nth(1)).to_be_focused()

    # The quality-warned photo (now at index 0): Retake / Keep, the warning
    # named; Keep dismisses it.
    tiles.nth(0).click()
    expect(sheet.locator(".photo-sheet-warning")).to_contain_text("Blurry")
    expect(sheet.get_by_role("button", name="Retake")).to_be_visible()
    sheet.get_by_role("button", name="Keep it").click()
    expect(sheet).to_have_count(0)
    expect(authed_page.locator("#thumbStrip li.thumb.warned")).to_have_count(0)

    # Keyboard: a tile opens its sheet with Enter; a backdrop tap closes it.
    tiles.nth(2).focus()
    authed_page.keyboard.press("Enter")
    expect(sheet.locator("h2")).to_have_text("Photo 3 of 3")
    authed_page.mouse.click(2, 2)
    expect(sheet).to_have_count(0)

    # Remove drops the photo and hits the API.
    tiles.nth(2).click()
    sheet.get_by_role("button", name="Remove photo").click()
    expect(authed_page.locator("#thumbStrip li.thumb")).to_have_count(2)
    _wait_until(authed_page, lambda: deleted)
    assert len(deleted) == 1, deleted
    assert errors == [], errors


def test_removing_a_photo_mid_upload_deletes_it_server_side(
    authed_page: Page, base_url: str
) -> None:
    """Remove (or Retake) while the upload is still in flight: the photo is
    dropped from the strip at once, and once the upload lands the page deletes
    the stray server copy — else the next order sync is one photo short and
    Extract fails."""
    deleted: list[str] = []
    held: list[Route] = []

    def json_route(body: dict):
        return lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    def photo(route: Route) -> None:
        if route.request.method != "DELETE":
            route.fallback()
            return
        deleted.append(route.request.url)
        json_route({"photos": []})(route)

    authed_page.route("**/api/sessions", json_route({"session_id": "mocked"}))
    authed_page.route("**/api/sessions/mocked/photos", lambda route: held.append(route))  # held open
    authed_page.route("**/api/sessions/mocked/photos/*", photo)
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)

    authed_page.set_input_files("#galleryInput", files=[_file("a.jpg", noisy=True)])
    expect(authed_page.locator("#thumbStrip li.thumb.uploading")).to_have_count(1)
    authed_page.locator("#thumbStrip .thumb-select").click()
    authed_page.locator(_SHEET).get_by_role("button", name="Remove photo").click()
    expect(authed_page.locator("#thumbStrip li.thumb")).to_have_count(0)

    _wait_until(authed_page, lambda: held)
    assert deleted == []
    held[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"added": [{"sequence_index": 1}], "photos": []}),
    )
    _wait_until(authed_page, lambda: deleted)
    assert deleted[0].endswith("/api/sessions/mocked/photos/1"), deleted
