"""Browser regression for the Capture thumbnail toolbar (#147).

A thumbnail is a 96px tile; the old per-photo overlay controls (remove 24px,
move 22px, the quality warning's Retake / Keep) sat under the 44px hit-target
floor and the tile clips its overflow, so they could not be expanded. The fix:
tap a thumbnail to select it (``aria-pressed`` + a ring), and act from one
toolbar below the strip whose buttons are real 44px targets.

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
_TOOLBAR_BUTTONS = (
    "#thumbMoveLeft",
    "#thumbMoveRight",
    "#thumbView",
    "#thumbRetake",
    "#thumbKeep",
    "#thumbRemove",
)


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


def test_thumbnail_toolbar_selects_and_acts(authed_page: Page, base_url: str) -> None:
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

    toolbar = authed_page.locator("#thumbToolbar")
    expect(toolbar).to_be_visible()
    # Nothing selected: the toolbar is there but inert.
    for sel in _TOOLBAR_BUTTONS:
        expect(authed_page.locator(sel)).to_be_disabled()
    expect(authed_page.locator("#thumbStrip [aria-pressed='true']")).to_have_count(0)

    # Select photo 1 by tap: ring state is aria-pressed, toolbar arms.
    select = authed_page.locator("#thumbStrip .thumb-select")
    select.nth(0).click()
    expect(select.nth(0)).to_have_attribute("aria-pressed", "true")
    expect(select.nth(1)).to_have_attribute("aria-pressed", "false")
    expect(authed_page.locator("#thumbMoveLeft")).to_be_disabled()  # first photo
    for sel in ("#thumbMoveRight", "#thumbView", "#thumbRemove"):
        expect(authed_page.locator(sel)).to_be_enabled()
    # Not warned: Retake / Keep are not offered.
    expect(authed_page.locator("#thumbRetake")).to_be_hidden()
    expect(authed_page.locator("#thumbKeep")).to_be_hidden()

    # Every thumbnail control is a real 44px target.
    _assert_hit_targets(
        authed_page,
        ["#thumbStrip .thumb-select", "#thumbStrip button"]
        + [s for s in _TOOLBAR_BUTTONS if s not in ("#thumbRetake", "#thumbKeep")],
    )

    # Move right: the selected photo travels with its selection.
    before = _srcs(authed_page)
    authed_page.locator("#thumbMoveRight").click()
    after = _srcs(authed_page)
    assert after[1] == before[0] and after[0] == before[1], (before, after)
    expect(select.nth(1)).to_have_attribute("aria-pressed", "true")
    expect(authed_page.locator("#thumbMoveLeft")).to_be_enabled()

    # The quality-warned photo (now at index 0 → select it): Retake / Keep
    # appear in the toolbar at 44px, and Keep dismisses the warning.
    select.nth(0).click()
    expect(authed_page.locator("#thumbRetake")).to_be_visible()
    expect(authed_page.locator("#thumbKeep")).to_be_visible()
    _assert_hit_targets(authed_page, ["#thumbRetake", "#thumbKeep"])
    authed_page.locator("#thumbKeep").click()
    expect(authed_page.locator("#thumbStrip li.thumb.warned")).to_have_count(0)
    expect(authed_page.locator("#thumbKeep")).to_be_hidden()
    expect(select.nth(0)).to_have_attribute("aria-pressed", "true")

    # View opens the preview (a tap on the tile now selects, not previews).
    authed_page.locator("#thumbView").click()
    expect(authed_page.locator("#previewDialog")).to_be_visible()
    authed_page.locator("#previewClose").click()
    expect(authed_page.locator("#previewDialog")).to_be_hidden()

    # Tapping elsewhere clears the selection.
    authed_page.locator("#captureStatus").click()
    expect(authed_page.locator("#thumbStrip [aria-pressed='true']")).to_have_count(0)
    expect(authed_page.locator("#thumbRemove")).to_be_disabled()

    # Keyboard: a thumbnail is a focusable toggle button.
    select.nth(2).focus()
    authed_page.keyboard.press("Enter")
    expect(select.nth(2)).to_have_attribute("aria-pressed", "true")

    # Remove drops the photo, hits the API, and clears the selection.
    authed_page.locator("#thumbRemove").click()
    expect(authed_page.locator("#thumbStrip li.thumb")).to_have_count(2)
    expect(authed_page.locator("#thumbStrip [aria-pressed='true']")).to_have_count(0)
    expect(authed_page.locator("#thumbRemove")).to_be_disabled()
    assert len(deleted) == 1, deleted
    assert errors == [], errors
