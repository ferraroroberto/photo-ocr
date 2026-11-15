"""Browser regression for the Android share-sheet target.

The share sheet POSTs the photos to the manifest's /share-target as a
navigation. The real service worker (sw.js) answers it, parks the photos and
redirects to the app, whose boot uploads them into a new take against the
real disposable webapp. The form below stands in for the share sheet: it
sends the same multipart POST navigation the manifest declares.

Chromium only: share_target is an Android Chrome feature, and the rest of the
suite runs with service workers blocked (they would sit between a page and
its page.route mocks), so this test opens its own context that allows them.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e._surface import surface

pytestmark = pytest.mark.desktop_only

_SHARED = 2


def _jpeg() -> bytes:
    pil = pytest.importorskip("PIL.Image", reason="Pillow needed to share photos")
    img = pil.frombytes("RGB", (128, 128), os.urandom(128 * 128 * 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def sw_page(
    browser: Browser, browser_context_args: dict, auth_token: str
) -> Iterator[Page]:
    context = browser.new_context(**{**browser_context_args, "service_workers": "allow"})
    if auth_token:
        context.add_init_script(
            f"window.localStorage.setItem('photo-ocr.token', {json.dumps(auth_token)});"
        )
    try:
        yield context.new_page()
    finally:
        context.close()


def test_shared_photos_start_a_new_take(sw_page: Page, base_url: str) -> None:
    page = sw_page
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    # The worker must be active before the share, or the server's fallback
    # answers it (the "share again" notice), which is the other path.
    page.evaluate("() => navigator.serviceWorker.ready.then(() => true)")

    page.evaluate(
        """() => {
          const form = document.createElement('form');
          form.method = 'post';
          form.enctype = 'multipart/form-data';
          form.action = '/share-target';
          const input = document.createElement('input');
          input.type = 'file';
          input.name = 'photos';
          input.multiple = true;
          input.id = 'shareSheetProbe';
          form.appendChild(input);
          document.body.appendChild(form);
        }"""
    )
    page.set_input_files(
        "#shareSheetProbe",
        [
            {"name": f"shot-{i}.jpg", "mimeType": "image/jpeg", "buffer": _jpeg()}
            for i in range(1, _SHARED + 1)
        ],
    )
    with page.expect_navigation(url=f"{base_url}/?share-target=photos"):
        page.evaluate("() => document.querySelector('#shareSheetProbe').form.submit()")

    # Uploaded through the normal Capture path: every shared photo reaches
    # the server as one ready tile of a new take, on the Capture tab.
    expect(page.locator("#thumbStrip li.thumb.ready")).to_have_count(_SHARED, timeout=10_000)
    expect(page.locator("#tabCapture")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#extractBtn")).to_be_visible()
    surface(page, "capture.shared")

    # The status is stripped and the parked photos are consumed, so a reload
    # starts empty instead of replaying the share.
    assert "share-target" not in page.url
    left = page.evaluate(
        "() => caches.open('photo-ocr-share').then(c => c.keys()).then(k => k.length)"
    )
    assert left == 0
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#takeEmpty")).to_be_visible()
    expect(page.locator("#thumbStrip li.thumb")).to_have_count(0)
