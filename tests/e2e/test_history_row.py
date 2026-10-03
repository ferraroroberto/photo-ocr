"""Browser regression for the History row (design.md ``action-row``): tap the
row to copy, everything else behind one overflow menu, Delete last behind a
confirm.

The session endpoints are intercepted so the test is deterministic and never
touches the real archive; it still drives the real SPA and the real vendored
``action-row`` / ``modal`` CSS.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, Route, expect

pytestmark = pytest.mark.smoke

_SESSIONS = [
    {
        "session_id": "s-one",
        "created_at": "2026-10-03T08:00:00+00:00",
        "photo_count": 1,
        "model": None,
        "extract_duration_s": 2.5,
        "extracted_preview": "First take text",
        "source": "webapp",
    },
    {
        "session_id": "s-two",
        "created_at": "2026-10-03T09:00:00+00:00",
        "photo_count": 3,
        "model": None,
        "extract_duration_s": None,
        "extracted_preview": "Second take text",
        "source": "webapp",
    },
]


def test_history_row_tap_copies_and_menu_deletes(
    authed_page: Page, base_url: str
) -> None:
    sessions = list(_SESSIONS)

    def list_sessions(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sessions": sessions, "total": len(sessions)}),
        )

    def session_text(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"extracted": "FULL TEXT of the first take"}),
        )

    def delete_session(route: Route) -> None:
        sessions[:] = [s for s in sessions if s["session_id"] != "s-one"]
        route.fulfill(status=200, content_type="application/json", body="{}")

    authed_page.route("**/api/sessions?*", list_sessions)
    authed_page.route("**/api/sessions/s-one/text", session_text)
    authed_page.route("**/api/sessions/s-one", delete_session)
    # Capture the clipboard write (permissions differ per engine).
    authed_page.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: "
        "{writeText: t => { window.__copied = t; return Promise.resolve(); }}})"
    )
    authed_page.goto(f"{base_url}/", wait_until="domcontentloaded")
    authed_page.locator("#tabHistory").click()

    rows = authed_page.locator("#historyList .action-row")
    expect(rows).to_have_count(2)
    first = rows.first
    # Snippet leads as the title; date · photos · model is the meta line.
    expect(first.locator(".action-row-title")).to_have_text("First take text")
    expect(first.locator(".action-row-meta")).to_contain_text("1 photo ·")
    # No visible per-row Redo / Delete: one tap target plus one kebab.
    expect(first.locator("button")).to_have_count(2)
    box = first.locator(".action-row-kebab").bounding_box()
    assert box and box["width"] >= 44 and box["height"] >= 44, box

    # Tapping the row copies the full text, not the preview.
    first.locator(".action-row-main").click()
    authed_page.wait_for_function("window.__copied !== undefined")
    assert authed_page.evaluate("window.__copied") == "FULL TEXT of the first take"

    # The kebab opens the menu; Delete sits behind a confirm.
    first.locator(".action-row-kebab").click()
    menu = authed_page.locator("#takeMenu")
    expect(menu).to_be_visible()
    expect(authed_page.locator("#takeRedo")).to_be_visible()
    authed_page.once("dialog", lambda d: d.accept())
    authed_page.locator("#takeDelete").click()
    expect(menu).to_be_hidden()
    expect(rows).to_have_count(1)
