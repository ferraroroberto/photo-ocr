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
    each paid a load per projection): no JS errors, the Settings rows filled
    from the config, the Capture zone rendered, and the login overlay markup
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

    # renderSettings runs after /api/config resolves: the default model and
    # style rows must carry a value, or the config never reached the page.
    for row in ("#setModelValue", "#setStyleValue", "#setRetentionValue", "#setMaxPhotosValue"):
        expect(authed_page.locator(row)).not_to_have_text("")

    # The empty take shows only its two starts (Choose photos the primary,
    # Camera the second): no Extract, no switch row, no status line, and a
    # header with no exception (#194).
    empty = authed_page.locator("#takeEmpty")
    expect(empty).to_be_visible()
    expect(empty.locator(".choose-photos-btn")).to_have_class(re.compile(r"\bbutton-primary\b"))
    expect(empty.locator(".camera-btn")).to_be_visible()
    expect(authed_page.locator("#takeBuild")).to_be_hidden()
    expect(authed_page.locator("#extractBtn")).to_be_hidden()
    expect(authed_page.get_by_role("switch", name="Save to history")).to_be_hidden()
    expect(authed_page.locator("#takeResult")).to_be_hidden()
    expect(authed_page.locator("#captureHeadline")).to_have_text("")

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


def test_settings_groups_save_as_they_change(
    authed_page: Page, base_url: str
) -> None:
    """Settings is never a tab (fleet NAV-03): it opens from the header gear
    every pane carries beside the theme toggle, and any tab tap leaves it.
    It is three inset groups (Display, Reading, Storage) whose rows save to
    the server as they change, with a toast, and revert on a failed save;
    the prompt opens read-only in a sheet; a retention outside the presets
    is shown as-is; the device keeps no model / style of its own (#197).

    ``/api/config`` is served from an in-memory store here, so the test
    never writes the checkout's real config file."""
    import json

    store: dict = {}
    posts: list[dict] = []
    fail = {"next": False}

    def config(route) -> None:
        if route.request.method == "POST":
            patch = json.loads(route.request.post_data or "{}")
            posts.append(patch)
            if fail["next"]:
                fail["next"] = False
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "invented failure"}))
                return
            store.update(patch)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "config": dict(store)}))
            return
        if not store:
            body = route.fetch().json()
            body["ocr_models_available"] = ["gemini_flash", "claude_opus"]
            body["ocr_model_default"] = "gemini_flash"
            body["history_retention_days"] = 45  # outside the presets
            body["max_photos_per_session"] = 50
            store.update(body)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(store))

    authed_page.route("**/api/config", config)
    # A device value left by the old Settings must not shadow the server.
    authed_page.add_init_script(
        "localStorage.setItem('photo-ocr.model', 'claude_opus');"
        "localStorage.setItem('photo-ocr.promptId', 'stale');"
    )
    _navigate_collecting_errors(authed_page, base_url)
    expect(authed_page.locator("#setModelValue")).to_have_text("Gemini Flash")
    assert authed_page.evaluate(
        "[localStorage.getItem('photo-ocr.model'), localStorage.getItem('photo-ocr.promptId')]"
    ) == [None, None]

    # The Capture pane is the default tab; Settings starts hidden. The nav
    # lists only the real destinations, and every pane (Settings included)
    # opens with the home-head row: theme toggle, then the gear.
    expect(authed_page.locator("#paneSettings")).to_be_hidden()
    expect(authed_page.locator('.tabs .tab[data-tab="settings"]')).to_have_count(0)
    expect(authed_page.locator(".tabs .tab")).to_have_count(2)
    for pane in ("#paneCapture", "#paneHistory", "#paneSettings"):
        head = authed_page.locator(f"{pane} > .card.home-head")
        expect(head.locator(".theme-toggle")).to_have_count(1)
        expect(head.locator(".home-settings")).to_have_count(1)
        # .icon-button's (project-scaffolding#339): a glyph on nothing at rest
        assert is_unpainted(head.locator(".home-toggle"))
    authed_page.locator("#paneCapture .home-settings").click()
    pane = authed_page.locator("#paneSettings")
    expect(pane).to_be_visible()
    expect(authed_page.locator("#paneCapture")).to_be_hidden()

    # Three groups under overlines; no card titles, no Save button.
    expect(pane.locator(".settings-overline")).to_have_text(["Display", "Reading", "Storage"])
    expect(pane.locator(".card-title, #saveSettings, #ocrPromptPreview")).to_have_count(0)
    expect(pane.locator(".settings-group").last.locator(".settings-row").last).to_have_id("cleanAll")

    # Default model: a picker sheet; the pick saves at once, with a toast,
    # and survives a reload (the store is what GET /api/config serves).
    authed_page.locator("#setModel").click()
    authed_page.locator("dialog.sheet").get_by_role("button", name="Claude Opus").click()
    expect(authed_page.locator("#setModelValue")).to_have_text("Claude Opus")
    expect(authed_page.locator("#toast")).to_contain_text("Default model: Claude Opus")
    assert posts[-1] == {"ocr_model_default": "claude_opus"}, posts
    # A failed save says so and keeps the row's old value.
    fail["next"] = True
    authed_page.locator("#setModel").click()
    authed_page.locator("dialog.sheet").get_by_role("button", name="Gemini Flash").click()
    expect(authed_page.locator("#toast")).to_contain_text("Save failed")
    expect(authed_page.locator("#setModelValue")).to_have_text("Claude Opus")

    # Keep takes for: 45 days is not a preset, so it shows as-is and no
    # segment is raised until one is picked.
    expect(authed_page.locator("#setRetentionValue")).to_have_text("45 days")
    authed_page.locator("#setRetention").click()
    sheet = authed_page.locator("dialog.sheet")
    expect(sheet).to_contain_text("Now 45 days")
    expect(sheet.locator(".segmented-item[aria-pressed='true']")).to_have_count(0)
    sheet.get_by_role("button", name="90 days").click()
    expect(sheet.locator(".segmented-item[aria-pressed='true']")).to_have_text("90 days")
    expect(authed_page.locator("#setRetentionValue")).to_have_text("90 days")
    assert posts[-1] == {"history_retention_days": 90}, posts
    authed_page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)

    # Max photos: saves when the field is left, inside 1-100 only.
    authed_page.locator("#setMaxPhotos").click()
    field = authed_page.locator("#maxPhotosInput")
    field.fill("500")
    field.press("Enter")
    expect(authed_page.locator(".settings-sheet-error")).to_be_visible()
    expect(field).to_have_value("50")
    field.fill("20")
    field.press("Enter")
    expect(authed_page.locator("#setMaxPhotosValue")).to_have_text("20")
    assert posts[-1] == {"max_photos_per_session": 20}, posts
    authed_page.keyboard.press("Escape")

    # The prompt opens read-only in a sheet.
    authed_page.locator("#setPrompt").click()
    prompt = authed_page.locator("dialog.prompt-sheet-dialog .prompt-sheet-text")
    expect(prompt).to_be_visible()
    expect(prompt).not_to_have_text("")
    authed_page.keyboard.press("Escape")

    # Reload: everything came back from the server, nothing from the device;
    # Capture's chip starts from the new default.
    authed_page.reload(wait_until="domcontentloaded")
    expect(authed_page.locator("#setModelValue")).to_have_text("Claude Opus")
    expect(authed_page.locator("#setRetentionValue")).to_have_text("90 days")
    expect(authed_page.locator("#setMaxPhotosValue")).to_have_text("20")
    assert authed_page.evaluate("localStorage.getItem('photo-ocr.model')") is None
    expect(authed_page.locator("#takeModel")).to_have_attribute(
        "aria-label", "Model for this take: Claude Opus"
    )

    # Any tab tap leaves Settings; the gear on another pane reopens it.
    authed_page.locator("#tabHistory").click()
    expect(pane).to_be_hidden()
    authed_page.locator("#paneHistory .home-settings").click()
    expect(pane).to_be_visible()

    # Type scale (TYPE-01): row values sit on body-sm (14px), row labels on
    # body (16px), never the 12px caption; every control inherits the page
    # font (the vendored base layer).
    sizes = authed_page.evaluate(
        "['#setModelValue', '#setModel .settings-label']"
        ".map(q => getComputedStyle(document.querySelector(q)).fontSize)"
    )
    assert sizes == ["14px", "16px"], sizes
    off = authed_page.evaluate(
        """() => {
          const body = getComputedStyle(document.body).fontFamily;
          return [...document.querySelectorAll('button, input, select, textarea')]
            .filter(e => getComputedStyle(e).fontFamily !== body).length;
        }"""
    )
    assert off == 0, off

    # Text size (A11Y-02): the zoom lock's escape, the vendored segmented
    # control. Large scales the root font-size and survives a reload.
    expect(authed_page.get_by_role("group", name="Text size")).to_be_visible()
    authed_page.locator('#textSizeControl [data-textsize="large"]').click()
    expect(authed_page.locator('#textSizeControl [data-textsize="large"]')).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(authed_page.locator("html")).to_have_attribute("data-textsize", "large")
    root_px = authed_page.evaluate("getComputedStyle(document.documentElement).fontSize")
    assert root_px == "18px", f"Large should be 112.5% of 16px, got {root_px}"
    authed_page.reload(wait_until="domcontentloaded")
    expect(authed_page.locator("html")).to_have_attribute("data-textsize", "large")


def test_save_to_history_switch_sets_incognito(
    authed_page: Page, base_url: str
) -> None:
    """The take's labelled "Save to history" switch is on by default and its
    inverse is the session's ``incognito`` flag. The switch appears with the
    first photo, after that photo's upload created the session, so a flip is
    written to the live take (``PATCH``), and the header names the off state."""
    import io
    import json
    import os

    pil = pytest.importorskip("PIL.Image", reason="Pillow needed to pick a photo")
    buf = io.BytesIO()
    # Noise, so the quality gate has nothing to say and the header's only
    # exception is the switch's.
    pil.frombytes("RGB", (128, 128), os.urandom(128 * 128 * 3)).save(buf, format="JPEG")

    created: list[dict] = []
    patched: list[dict] = []

    def create_session(route) -> None:
        created.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"session_id": "mocked"}),
        )

    def patch_session(route) -> None:
        patched.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body="{}")

    authed_page.route("**/api/sessions", create_session)
    authed_page.route("**/api/sessions/mocked", patch_session)
    authed_page.route(
        "**/api/sessions/mocked/photos",
        lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"added": [{"sequence_index": 1}], "photos": []}),
        ),
    )
    _navigate_collecting_errors(authed_page, base_url)

    authed_page.locator("#galleryInput").set_input_files(
        {"name": "p.jpg", "mimeType": "image/jpeg", "buffer": buf.getvalue()}
    )
    switch = authed_page.get_by_role("switch", name="Save to history")
    expect(switch).to_be_visible()
    expect(switch).to_have_attribute("aria-checked", "true")
    for _ in range(50):
        if created:
            break
        authed_page.wait_for_timeout(100)
    assert created and created[0].get("incognito") is False, created
    # The upload ran after the session was known client-side.
    expect(authed_page.locator("#thumbStrip li.thumb.ready")).to_have_count(1)
    expect(authed_page.locator("#captureHeadline")).to_have_text("")

    headline = authed_page.locator("#captureHeadline")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(headline).to_contain_text("Not saved to History")
    # The row's text toggles it too.
    label = authed_page.locator("#saveToHistoryLabel")
    label.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(headline).not_to_contain_text("Not saved")
    label.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    for _ in range(50):
        if len(patched) == 3:
            break
        authed_page.wait_for_timeout(100)
    assert patched == [{"incognito": True}, {"incognito": False}, {"incognito": True}], patched


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
