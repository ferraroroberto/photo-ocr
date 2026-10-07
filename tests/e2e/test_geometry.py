"""Rendered geometry for the primary controls, across the viewport x theme matrix.

The static design lint proves authored tokens; this proves what the browser
laid out (design.md hit-target and no-horizontal-scroll contracts) at the
fleet's four widths in light and dark. One test per engine walks every leg
rather than parametrising, so the gate pays for one page, not eight.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.e2e._geometry import (
    MATRIX,
    apply_matrix_leg,
    assert_min_target,
    assert_no_horizontal_overflow,
    assert_no_overlap,
    matrix_id,
)

pytestmark = pytest.mark.smoke

_CAPTURE_TARGETS = (".add-photo-btn", "#extractBtn")
_HISTORY_TARGETS = ("#refreshHistory", "#cleanAll")
# The vendored bottom-tab nav owns its own geometry (project-scaffolding): its
# desktop-measure tabs are 37px, so the 44px touch floor is held on phone widths.
_NAV_TABS = ("#tabCapture", "#tabHistory")
_PHONE_MAX_PX = 430


def _canvas(page: Page) -> str:
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


def test_primary_controls_fit_every_viewport_and_theme(
    authed_page: Page, base_url: str
) -> None:
    page = authed_page
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)
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
            page.locator(tab).click()
            where = f"{matrix_id(leg)} {tab}"
            if width <= _PHONE_MAX_PX:
                targets = _NAV_TABS + targets
            try:
                assert_no_horizontal_overflow(page)
                for sel in targets:
                    assert_min_target(page.locator(sel).locator("visible=true"))
                assert_no_overlap(
                    [page.locator(sel).locator("visible=true") for sel in targets]
                )
            except AssertionError as exc:
                raise AssertionError(f"{where}: {exc}") from exc
    assert canvas["light"] != canvas["dark"], canvas
