"""Smoke tests for the photo-ocr webapp.

Tight by design: two tests that catch the bugs we actually hit on the
SPA — one boot test (JS exceptions on boot, empty select dropdowns,
Capture zone, missing login overlay) and one Settings test (the header
gear, prompt preview, dirty-aware Save). Expand iteratively if
regressions slip through — do NOT turn this file into a regression net
for every feature.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import is_unpainted

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
    """Settings is never a tab (fleet NAV-03): it opens from the header gear
    that every pane carries beside the theme toggle, and any tab tap leaves
    it. The prompt preview inside it is always visible (unfolded by request
    — no disclosure). Catches a Settings tab creeping back into the nav, a
    pane missing its gear, a broken gear, a missing prompt preview, and a
    Save button that no longer tracks dirty state (#76) in one shot."""
    _navigate_collecting_errors(authed_page, base_url)
    # renderSettings (and so the dirty-state pass) runs after /api/config.
    authed_page.wait_for_selector("#ocrModel option", state="attached", timeout=5_000)
    # The Capture pane is the default tab; Settings starts hidden.
    expect(authed_page.locator("#paneSettings")).to_be_hidden()
    # The nav lists only the real destinations, and every pane (Settings
    # included) opens with the home-head row: theme toggle, then the gear.
    expect(authed_page.locator('.tabs .tab[data-tab="settings"]')).to_have_count(0)
    expect(authed_page.locator(".tabs .tab")).to_have_count(2)
    for pane in ("#paneCapture", "#paneHistory", "#paneSettings"):
        head = authed_page.locator(f"{pane} > .card.home-head")
        expect(head.locator(".theme-toggle")).to_have_count(1)
        expect(head.locator(".home-settings")).to_have_count(1)
        # .icon-button's (project-scaffolding#339): a glyph on nothing at rest
        assert is_unpainted(head.locator(".home-toggle"))
    authed_page.locator("#paneCapture .home-settings").click()
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

    # Any tab tap leaves Settings; the gear on another pane reopens it.
    authed_page.locator("#tabHistory").click()
    expect(authed_page.locator("#paneSettings")).to_be_hidden()
    expect(authed_page.locator("#paneHistory")).to_be_visible()
    authed_page.locator("#paneHistory .home-settings").click()
    expect(authed_page.locator("#paneSettings")).to_be_visible()
    expect(authed_page.locator("#paneHistory")).to_be_hidden()

    # Type scale (TYPE-01): read lines sit on body-sm (14px) or above, never
    # the 12px caption, and the label role is the spec's 0.875rem.
    sizes = authed_page.evaluate(
        "['#ocrPromptPreview', '.row > span', '.stacked > span']"
        ".map(q => getComputedStyle(document.querySelector(q)).fontSize)"
    )
    assert sizes == ["14px", "14px", "14px"], sizes

    # What the rendered design review measures: every form control (the
    # monospace-free prompt preview included) inherits the page font, field
    # boundaries are a drawn border, button glyphs sit on the 16px step, and
    # the text-size control is findable by its accessible name.
    facts = authed_page.evaluate(
        """() => {
          const body = getComputedStyle(document.body).fontFamily;
          const off = [...document.querySelectorAll('button, input, select, textarea')]
            .filter(e => getComputedStyle(e).fontFamily !== body).length;
          const sel = getComputedStyle(document.getElementById('ocrModel'));
          const save = document.querySelector('#saveSettings .icon').getBoundingClientRect();
          return { off, border: sel.borderTopWidth, saveIcon: Math.round(save.width) };
        }"""
    )
    assert facts == {"off": 0, "border": "1px", "saveIcon": 16}, facts
    expect(authed_page.get_by_label("Text size")).to_be_visible()

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
        "[document.body, document.getElementById('tabHistory')]"
        ".map(e => getComputedStyle(e).fontFamily)"
    )
    assert fonts[0] == fonts[1], f"controls must inherit the page font: {fonts}"


def test_save_to_history_switch_sets_incognito(
    authed_page: Page, base_url: str
) -> None:
    """The Capture card's labelled "Save to history" switch is on by default
    and its inverse is the session's ``incognito`` flag: turning it off makes
    the next take's ``POST /api/sessions`` carry ``incognito: true``."""
    import io
    import json

    pil = pytest.importorskip("PIL.Image", reason="Pillow needed to pick a photo")
    buf = io.BytesIO()
    pil.new("RGB", (64, 48), color=(180, 190, 200)).save(buf, format="JPEG")

    created: list[dict] = []

    def create_session(route) -> None:
        created.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"session_id": "mocked"}),
        )

    # Only the create call matters; the photo upload that follows is dropped.
    authed_page.route("**/api/sessions", create_session)
    authed_page.route("**/api/sessions/mocked/photos", lambda r: r.abort())
    _navigate_collecting_errors(authed_page, base_url)

    switch = authed_page.get_by_role("switch", name="Save to history")
    expect(switch).to_have_attribute("aria-checked", "true")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    # The row's text toggles it too.
    label = authed_page.locator("#saveToHistoryLabel")
    label.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    label.click()
    expect(switch).to_have_attribute("aria-checked", "false")

    authed_page.locator("#galleryInput").set_input_files(
        {"name": "p.jpg", "mimeType": "image/jpeg", "buffer": buf.getvalue()}
    )
    for _ in range(50):
        if created:
            break
        authed_page.wait_for_timeout(100)
    assert created and created[0].get("incognito") is True, created


_SHOW_TOAST_JS = r"""async ([message, kind]) => {
  const url = performance.getEntriesByType('resource')
    .map((r) => r.name)
    .find((n) => /\/static\/state\.js(\?|$)/.test(n));
  const { toast } = await import(url);
  toast(message, kind);
  const el = document.getElementById('toast');
  const style = getComputedStyle(el);
  return {
    cls: el.className,
    live: el.getAttribute('aria-live'),
    background: style.backgroundColor,
    border: style.borderTopColor,
    visible: !el.hidden,
  };
}"""


def _channels(css_color: str) -> tuple[float, ...]:
    """RGB on 0-255 whatever the serialisation: ``rgb(...)`` is 0-255, but a
    ``color-mix()`` result comes back as ``color(srgb 0.1 0.5 0.2 / 0.9)``."""
    nums = tuple(float(n) for n in re.findall(r"[\d.]+", css_color)[:3])
    return tuple(n * 255 for n in nums) if css_color.startswith("color(") else nums


def test_toast_is_neutral_and_only_an_error_tints(
    authed_page: Page, base_url: str
) -> None:
    """Fleet COLOR-05: a success or info message never takes a colour; only a
    real error tints and is announced assertively. Drives the app's own
    ``toast`` (the stamped ``state.js`` the page already loaded, so the same
    module instance) and reads the painted result."""
    _navigate_collecting_errors(authed_page, base_url)
    authed_page.wait_for_load_state("networkidle")

    ok = authed_page.evaluate(_SHOW_TOAST_JS, ["Defaults saved.", "good"])
    assert ok["visible"]
    # The nav bar's glass: white or near-black, never a hue, border included.
    for part in ("background", "border"):
        r, g, b = _channels(ok[part])
        assert max(r, g, b) - min(r, g, b) < 24, f"toast {part} is tinted: {ok[part]}"
    assert "error" not in ok["cls"].split()
    assert ok["live"] == "polite"

    bad = authed_page.evaluate(_SHOW_TOAST_JS, ["Save failed", "error"])
    assert bad["live"] == "assertive"
    assert "error" in bad["cls"].split()
    r, g, b = _channels(bad["background"])
    assert r > g + 40 and r > b + 40, f"error toast is not danger-tinted: {bad['background']}"
