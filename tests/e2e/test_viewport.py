"""Sanity-check that the WebKit projection actually applies the iPhone
descriptor.

Confirms ``browser_context_args`` in conftest.py merged in
``playwright.devices["iPhone 14"]`` — without this the WebKit run would
silently use a desktop viewport and the smoke suite wouldn't be
exercising an iPhone-shaped target at all (issue #7).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.smoke

# Matches playwright.devices["iPhone 14"]["viewport"]["width"].
_IPHONE_14_WIDTH = 390


def test_iphone_viewport_active_on_webkit(
    authed_page: Page, base_url: str, browser_name: str
) -> None:
    if browser_name != "webkit":
        pytest.skip("iPhone projection only applies to the WebKit browser")
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    width = authed_page.evaluate("window.innerWidth")
    assert width == _IPHONE_14_WIDTH, (
        f"expected iPhone 14 width {_IPHONE_14_WIDTH}, got {width} — the "
        "device descriptor merge in conftest.py didn't take effect"
    )


def test_touch_is_enabled_on_webkit(
    authed_page: Page, base_url: str, browser_name: str
) -> None:
    if browser_name != "webkit":
        pytest.skip("touch projection only applies to the WebKit browser")
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    # iPhone descriptors set has_touch — ontouchstart exists on a real
    # touch context. Pins that the photo-capture UI is tested touch-first.
    has_touch = authed_page.evaluate("'ontouchstart' in window")
    assert has_touch, "WebKit projection should expose a touch context"
