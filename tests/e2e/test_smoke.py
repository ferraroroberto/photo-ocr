"""Smoke tests for the photo-ocr webapp.

Tight by design: two tests that catch the bugs we actually hit on the
SPA — one boot test (JS exceptions on boot, empty select dropdowns,
Capture zone, missing login overlay) and one Settings test (broken tab
switch, prompt preview, dirty-aware Save). Expand iteratively if
regressions slip through — do NOT turn this file into a regression net
for every feature.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.smoke


def _navigate_collecting_errors(
    page: Page, base_url: str, *, boot_settled: bool = False
) -> list[str]:
    """Open the SPA and capture any uncaught JS errors during boot.

    ``boot_settled`` also waits for boot's last network step (the History
    request), so the response's render is the only thing left to run.
    """
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    if boot_settled:
        with page.expect_response(lambda r: "/api/sessions?" in r.url):
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
    else:
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
    # #extractBtn is rendered server-side in index.html; waiting for it
    # confirms the static document parsed without an early script crash.
    page.wait_for_selector("#extractBtn", state="attached", timeout=5_000)
    return errors


def test_boot_renders_spa(authed_page: Page, base_url: str) -> None:
    """One page load, four boot checks (they shared an identical setup and
    each paid a load per projection): no JS errors, both OCR selects
    populated, the Capture zone rendered, and the login overlay markup
    wired. The overlay check mutates the DOM, so it runs last."""
    errors = _navigate_collecting_errors(authed_page, base_url, boot_settled=True)
    # boot() awaits fetchConfig, then the History request, then renders.
    # Two animation frames after that response let the continuation that
    # consumes it run, so anything thrown during boot has fanned out as
    # pageerror — no fixed timer.
    authed_page.evaluate(
        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )
    assert errors == [], "JS errors during boot:\n  - " + "\n  - ".join(errors)

    # renderOcrOptions runs after /api/config resolves. Both selects must
    # end up with at least one <option> or the dropdowns are empty.
    # state="attached" not "visible" — <option> inside a collapsed <select>
    # has no layout box so the default visible state never resolves.
    authed_page.wait_for_selector("#ocrModel option", state="attached", timeout=5_000)
    authed_page.wait_for_selector("#ocrStyle option", state="attached", timeout=5_000)
    model_count = authed_page.locator("#ocrModel option").count()
    style_count = authed_page.locator("#ocrStyle option").count()
    assert model_count >= 1, f"#ocrModel rendered no options (got {model_count})"
    assert style_count >= 1, f"#ocrStyle rendered no options (got {style_count})"

    # The Extract button is the headline UI; if it's gone, the app is
    # useless. It MUST be disabled with no photos added — asserting that
    # state catches the regression where the disable-until-photo logic
    # accidentally gets removed.
    extract_btn = authed_page.locator("#extractBtn")
    expect(extract_btn).to_be_visible()
    expect(extract_btn).to_be_disabled()
    expect(extract_btn).to_contain_text("Extract")
    expect(authed_page.locator("#captureStatus")).to_be_visible()

    # The login overlay markup is wired so showLogin() can reveal it. We
    # exercise the DOM directly rather than triggering a real 401: the
    # bearer middleware bypasses loopback, so a bad token from 127.0.0.1
    # won't surface the overlay. This still catches overlay element +
    # password input missing or renamed.
    overlay = authed_page.locator("#loginOverlay")
    expect(overlay).to_be_hidden()
    authed_page.evaluate(
        "document.getElementById('loginOverlay').hidden = false"
    )
    expect(overlay).to_be_visible()
    pw = authed_page.locator("#loginPassword")
    expect(pw).to_be_editable()
    pw.fill("dummy")
    expect(pw).to_have_value("dummy")


def test_settings_pane_shows_prompt_preview(
    authed_page: Page, base_url: str
) -> None:
    """Settings lives behind the vendored bottom-tab nav; the prompt
    preview inside it is always visible (unfolded by request — no
    disclosure). Catches a broken tab switch, a missing pane, a missing
    prompt preview, and a Save button that no longer tracks dirty state
    (#76) in one shot."""
    _navigate_collecting_errors(authed_page, base_url)
    # renderSettings (and so the dirty-state pass) runs after /api/config.
    authed_page.wait_for_selector("#ocrModel option", state="attached", timeout=5_000)
    # The Capture pane is the default tab; Settings starts hidden.
    expect(authed_page.locator("#paneSettings")).to_be_hidden()
    authed_page.locator("#tabSettings").click()
    expect(authed_page.locator("#paneSettings")).to_be_visible()
    expect(authed_page.locator("#paneCapture")).to_be_hidden()
    expect(authed_page.locator("#ocrPromptPreview")).to_be_visible()
    # Save defaults is dirty-aware: disabled while the selection matches the
    # persisted defaults, armed once an input differs.
    save_btn = authed_page.locator("#saveSettings")
    expect(save_btn).to_be_visible()
    expect(save_btn).to_be_disabled()
    max_photos = authed_page.locator("#maxPhotos")
    current = int(max_photos.input_value() or "50")
    max_photos.fill(str(current + 1))
    expect(save_btn).to_be_enabled()

    # Text size (A11Y-02): the zoom lock's escape. Large scales the root
    # font-size, survives a reload through the pre-paint boot, and form
    # controls inherit the page font (TYPE-02, the vendored base layer).
    authed_page.locator('#textSizeControl [data-textsize="large"]').click()
    expect(authed_page.locator("html")).to_have_attribute("data-textsize", "large")
    root_px = authed_page.evaluate(
        "getComputedStyle(document.documentElement).fontSize"
    )
    assert root_px == "18px", f"Large should be 112.5% of 16px, got {root_px}"
    authed_page.reload(wait_until="domcontentloaded")
    expect(authed_page.locator("html")).to_have_attribute("data-textsize", "large")
    fonts = authed_page.evaluate(
        "[document.body, document.getElementById('tabSettings')]"
        ".map(e => getComputedStyle(e).fontFamily)"
    )
    assert fonts[0] == fonts[1], f"controls must inherit the page font: {fonts}"
