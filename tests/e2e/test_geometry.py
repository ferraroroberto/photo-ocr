"""Rendered geometry for the primary controls, across the viewport x theme matrix.

The static design lint proves authored tokens; this proves what the browser
laid out (design.md hit-target and no-horizontal-scroll contracts) at the
fleet's four widths in light and dark. One test per engine walks every leg
rather than parametrising, so the gate pays for one page, not eight.
"""

from __future__ import annotations

import io
import json

import pytest
from playwright.sync_api import Locator, Page, Route, expect

from tests.e2e._geometry import (
    MATRIX,
    apply_matrix_leg,
    assert_min_target,
    assert_no_horizontal_overflow,
    assert_no_overlap,
    effective_rects,
    matrix_id,
)

pytestmark = pytest.mark.smoke

# The empty take's two starts; once a photo is ready, the take's own controls.
_CAPTURE_TARGETS = (".choose-photos-btn", ".camera-btn")
_TAKE_TARGETS = ("#takeModel", "#takeStyle", "#extractBtn", "#takeMenuBtn", ".thumb-add-btn")
# Whatever History shows: its rows (tap + kebab) or the empty state's action.
_HISTORY_TARGETS = ("#historyList button",)
# The vendored bottom-tab nav owns its own geometry (project-scaffolding): its
# desktop-measure tabs are 37px, so the 44px touch floor is held on phone widths.
_NAV_TABS = ("#tabCapture", "#tabHistory")
_PHONE_MAX_PX = 430


def _canvas(page: Page) -> str:
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


def _open_tab(page: Page, tab: str) -> None:
    """Click *tab*; for History, wait until its reload has rebuilt the list.

    Opening History re-fetches and rebuilds `#historyList` (`innerHTML = ''`),
    so a measurement racing that rebuild reads the old, detached button as 0x0
    (#225). Waiting for the old first row to detach makes the rebuild, which
    is synchronous from there, land before the check.
    """
    if tab != "#tabHistory":
        page.locator(tab).click()
        return
    old = page.locator("#historyList > li").first.element_handle()
    page.locator(tab).click()
    page.wait_for_function("el => !el.isConnected", arg=old, timeout=5_000)


def _assert_rendered_min_target(locator: Locator) -> None:
    """The 44px floor, with "not rendered" told apart from "too small".

    `visible=true` only matches laid-out boxes, so a 0x0 measurement means the
    element collapsed or detached between query and measure: say so rather
    than report it as an undersized target.
    """
    for index, target in enumerate(effective_rects(locator)):
        vis = target.visual
        if vis.width == 0 and vis.height == 0:
            raise AssertionError(
                f"target #{index} of {locator}: not rendered at measurement "
                "(0x0 visual box; detached or collapsed after the visible "
                "query), not an undersized target"
            )
    assert_min_target(locator)


def _add_ready_photo(page: Page) -> None:
    """One ready photo on stubbed session endpoints (nothing reaches the archive)."""
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed to pick a photo")
    buf = io.BytesIO()
    pil.new("RGB", (64, 48), color=(180, 190, 200)).save(buf, format="JPEG")

    def fulfill(body: dict):
        return lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        )

    page.route("**/api/sessions", fulfill({"session_id": "mocked"}))
    page.route(
        "**/api/sessions/mocked/photos",
        fulfill({"added": [{"sequence_index": 1}], "photos": []}),
    )
    page.locator("#galleryInput").set_input_files(
        {"name": "p.jpg", "mimeType": "image/jpeg", "buffer": buf.getvalue()}
    )
    expect(page.locator("#thumbStrip li.thumb.ready")).to_have_count(1)


def test_primary_controls_fit_every_viewport_and_theme(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    # An empty History whatever the data root holds: its one target is the
    # empty state's action.
    page.route(
        "**/api/sessions?*",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"sessions": [], "total": 0}'
        ),
    )
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)
    # Boot's own History load has rendered (config loaded), so from here every
    # History click triggers exactly one rebuild for _open_tab to wait out.
    page.wait_for_selector("#historyList .empty-state-action", state="attached", timeout=5_000)
    canvas: dict[str, str] = {}
    for leg in MATRIX:
        width, theme = leg
        apply_matrix_leg(page, width, theme)
        # Boot check: the theme must actually have flipped a themed token, or
        # every leg below would be measuring the same page.
        canvas[theme] = _canvas(page)
        for tab, targets in (
            ("#tabCapture", _CAPTURE_TARGETS),
            ("#tabHistory", _HISTORY_TARGETS),
        ):
            _open_tab(page, tab)
            where = f"{matrix_id(leg)} {tab}"
            if width <= _PHONE_MAX_PX:
                targets = _NAV_TABS + targets
            try:
                assert_no_horizontal_overflow(page)
                for sel in targets:
                    _assert_rendered_min_target(page.locator(sel).locator("visible=true"))
                assert_no_overlap(
                    [page.locator(sel).locator("visible=true") for sel in targets]
                )
            except AssertionError as exc:
                raise AssertionError(f"{where}: {exc}") from exc
    assert canvas["light"] != canvas["dark"], canvas

    # With a ready photo the take's chips, Extract, its overflow and the add
    # tile hold the floor without overlapping, at the narrowest width.
    apply_matrix_leg(page, MATRIX[0][0], MATRIX[0][1])
    page.locator("#tabCapture").click()
    _add_ready_photo(page)
    try:
        assert_no_horizontal_overflow(page)
        for sel in _TAKE_TARGETS:
            _assert_rendered_min_target(page.locator(sel).locator("visible=true"))
        assert_no_overlap([page.locator(sel).locator("visible=true") for sel in _TAKE_TARGETS])
    except AssertionError as exc:
        raise AssertionError(f"{matrix_id(MATRIX[0])} take: {exc}") from exc
