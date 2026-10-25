"""Browser regression for History (design.md ``action-row``, #196): search
first, takes under date overlines with exception chips only, a row tap
copies, its ⋯ opens the take sheet (the text, Copy, Retry when unread, Redo
with… a picked model, Delete last behind the confirm sheet), and Delete all
lives in Settings › Storage behind the same confirm sheet.

Every session and search endpoint is intercepted (invented takes), so the
test is deterministic and never touches the real archive; it still drives
the real SPA and the real vendored ``action-row`` / ``modal`` CSS.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e._surface import surface
from tests.e2e.conftest import is_unpainted

pytestmark = pytest.mark.smoke


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _sessions() -> list[dict]:
    return [
        {
            "session_id": "s-one",
            "created_at": _iso(0),
            "photo_count": 1,
            "model": "gemini_flash",
            "prompt_id": "verbatim-merge",
            "extract_duration_s": 2.5,
            "extracted_preview": "First take text",
            "source": "webapp",
            "error": None,
            "extract_progress": {"missing_photos": []},
        },
        {
            "session_id": "s-two",
            "created_at": _iso(0),
            "photo_count": 3,
            "model": "gemini_flash",
            "prompt_id": "verbatim-merge",
            "extract_duration_s": None,
            "extracted_preview": "Second take text",
            "source": "app-launcher",
            "error": None,
            "extract_progress": {"missing_photos": ["02.jpg"]},
        },
        {
            "session_id": "s-old",
            "created_at": _iso(40),
            "photo_count": 2,
            "model": "gemini_flash",
            "prompt_id": "verbatim-merge",
            "extract_duration_s": 1.0,
            "extracted_preview": None,
            "source": "webapp",
            "error": "invented failure",
            "extract_progress": {"missing_photos": []},
        },
    ]


def _ok(route: Route, body) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


class _HistoryMock:
    """Invented archive behind the History endpoints, with the calls it saw."""

    def __init__(self) -> None:
        self.sessions = _sessions()
        self.more_left = 12  # pretend 12 older takes are not loaded yet
        self.deletes: list = []
        self.redo_bodies: list = []
        self.list_calls = 0

    def config(self, route: Route) -> None:
        body = route.fetch().json()
        body["ocr_models_available"] = ["gemini_flash", "claude_opus"]
        body["search_enabled"] = True
        body["history_retention_days"] = 30
        _ok(route, body)

    def list_sessions(self, route: Route) -> None:
        self.list_calls += 1
        _ok(route, {"sessions": self.sessions, "total": len(self.sessions) + self.more_left})

    def text(self, route: Route) -> None:
        sid = route.request.url.split("/api/sessions/")[1].split("/")[0]
        _ok(route, {"extracted": f"FULL TEXT of {sid}"})

    def status(self, route: Route) -> None:
        _ok(route, {"phase": "succeeded", "missing_photos": ["02.jpg"], "duration_s": 3.0,
                    "model": "gemini_flash", "prompt_id": "verbatim-merge"})

    def search(self, route: Route) -> None:
        q = route.request.url.split("q=")[1].split("&")[0]
        hits = [] if q == "zzz" else [{
            "session_id": "s-two",
            "created_at": self.sessions[1]["created_at"] if len(self.sessions) > 1 else _iso(0),
            "model": "gemini_flash", "snippet": "[Second] take text", "source": "app-launcher",
        }]
        _ok(route, {"query": q, "enabled": True, "results": hits})

    def redo(self, route: Route) -> None:
        self.redo_bodies.append(json.loads(route.request.post_data or "{}"))
        _ok(route, {"phase": "succeeded", "extracted": "REDONE", "model": "claude_opus",
                    "duration_s": 2.0, "missing_photos": []})

    def delete_one(self, route: Route) -> None:
        if route.request.method != "DELETE":
            route.fallback()
            return
        self.deletes.append("s-one")
        self.sessions[:] = [s for s in self.sessions if s["session_id"] != "s-one"]
        _ok(route, {})

    def delete_all(self, route: Route) -> None:
        if route.request.method != "DELETE":
            route.fallback()
            return
        self.deletes.append("all")
        self.sessions.clear()
        self.more_left = 0
        _ok(route, {})

    def install(self, page: Page) -> None:
        page.route("**/api/config", self.config)
        page.route("**/api/sessions?*", self.list_sessions)
        page.route("**/api/sessions/*/text", self.text)
        page.route("**/api/sessions/s-two/extract/status", self.status)
        page.route("**/api/search?*", self.search)
        page.route("**/api/sessions/s-one/redo", self.redo)
        page.route("**/api/sessions/s-one", self.delete_one)
        page.route("**/api/sessions", self.delete_all)


def _assert_list_chrome(page: Page) -> None:
    pane = page.locator("#paneHistory")
    # Search first, then the takes; no tool row, no "0/0".
    expect(pane.locator("#historySearch")).to_be_visible()
    expect(pane.locator("#refreshHistory, #historyCount, .history-tools")).to_have_count(0)
    expect(pane.locator("#historySearch")).to_have_attribute("placeholder", "Search 15 saved takes…")
    # Header names the exception among the loaded takes.
    expect(page.locator("#historyHeadline")).to_have_text("1 partly read")
    # Date overlines group the rows; Load more says what is left.
    expect(page.locator("#historyList .history-group")).to_have_count(2)
    expect(page.locator("#historyList .history-group").first).to_have_text("Today")
    expect(page.locator("#loadMoreHistory")).to_have_text("Load more (12 left)")


def _assert_kebab_is_unpainted_44px(row) -> None:
    # One tap target plus one kebab, the kebab a 44px unpainted icon button.
    expect(row.locator("button")).to_have_count(2)
    box = row.locator(".action-row-kebab").bounding_box()
    assert box and box["width"] >= 44 and box["height"] >= 44, box
    assert is_unpainted(row.locator(".action-row-kebab"))


def _open_first_take_sheet(page: Page, first):
    # The kebab opens the take sheet: the text, Copy, no Retry (fully read),
    # Redo with…, Delete last in danger text.
    first.locator(".action-row-kebab").click()
    sheet = page.locator("dialog.take-sheet-dialog")
    expect(sheet).to_be_visible()
    expect(sheet.locator("h2")).to_contain_text("Today")
    expect(sheet.locator(".take-sheet-text")).to_have_text("FULL TEXT of s-one")
    expect(sheet.locator(".take-sheet-meta")).to_contain_text("2.5 s")
    expect(sheet.get_by_role("button", name="Retry 0 unread")).to_be_hidden()
    expect(sheet.locator(".sheet-action").last).to_have_text("Delete take")
    expect(sheet.locator(".sheet-action").last).to_have_attribute("data-tone", "danger")
    assert is_unpainted(sheet.locator(".detail-close"))
    return sheet


def _redo_with_picked_model(page: Page, mock: _HistoryMock, sheet) -> None:
    # Redo with… opens a model picker marking the take's own model and posts
    # the picked one; the list refreshes after.
    sheet.get_by_role("button", name="Redo with…").click()
    picker = page.locator("dialog.sheet")
    expect(picker.locator("[aria-current='true']")).to_have_text("Gemini Flash")
    surface(page, "history.redo-picker")
    calls_before = mock.list_calls
    picker.get_by_role("button", name="Claude Opus").click()
    expect(page.locator("#toast")).to_contain_text("Redo done")
    assert mock.redo_bodies == [{"model": "claude_opus", "prompt_id": "verbatim-merge"}], mock.redo_bodies
    assert mock.list_calls > calls_before


def _search_hit_opens_sheet(page: Page, sheet) -> None:
    # A search hit opens the same sheet; its unread count comes from the
    # server once the sheet is up.
    page.locator("#historySearch").fill("Second")
    hit = page.locator("#historyList .action-row")
    expect(hit).to_have_count(1)
    expect(hit.locator("mark")).to_have_text("Second")
    expect(page.locator("#historyList .history-group")).to_have_count(0)
    hit.locator(".action-row-kebab").click()
    expect(sheet.get_by_role("button", name="Retry 1 unread")).to_be_visible()
    expect(sheet.locator(".take-sheet-text")).to_have_text("FULL TEXT of s-two")
    page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)


def test_history_rows_take_sheet_and_deletes(authed_page: Page, base_url: str) -> None:
    page = authed_page
    mock = _HistoryMock()
    mock.install(page)
    # Capture the clipboard write (permissions differ per engine).
    page.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: "
        "{writeText: t => { window.__copied = t; return Promise.resolve(); }}})"
    )
    # A native dialog must never appear: record (and dismiss) any that does.
    native: list = []
    page.on("dialog", lambda d: (native.append(d.type), d.dismiss()))
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    calls_before = mock.list_calls
    page.locator("#tabHistory").click()
    # Opening the tab reloads the list (there is no Refresh button).
    expect(page.locator("#historyList .action-row")).to_have_count(3)
    assert mock.list_calls > calls_before

    _assert_list_chrome(page)

    rows = page.locator("#historyList .action-row")
    first, second, old = rows.nth(0), rows.nth(1), rows.nth(2)
    # Chips only for exceptions: none on a normal take; unread + caller; failed.
    expect(first.locator(".chip")).to_have_count(0)
    expect(second.locator(".chip[data-tone='attention']")).to_have_text("1 unread")
    expect(second.locator(".chip[data-tone='neutral']")).to_have_text("app-launcher")
    expect(old.locator(".chip[data-tone='danger']")).to_have_text("Failed")
    expect(old.locator(".action-row-title")).to_have_text("invented failure")
    surface(page, "history.list")
    expect(first.locator(".action-row-meta")).to_contain_text("1 photo · Gemini Flash")
    _assert_kebab_is_unpainted_44px(first)

    # Tapping the row copies the full text, not the preview.
    first.locator(".action-row-main").click()
    page.wait_for_function("window.__copied !== undefined")
    assert page.evaluate("window.__copied") == "FULL TEXT of s-one"
    expect(page.locator("#toast")).to_contain_text("Copied 18 chars")

    sheet = _open_first_take_sheet(page, first)
    surface(page, "history.take-sheet")

    _redo_with_picked_model(page, mock, sheet)

    # A partly read take's sheet offers Retry with the count.
    second.locator(".action-row-kebab").click()
    expect(sheet.get_by_role("button", name="Retry 1 unread")).to_be_visible()
    page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)

    _search_hit_opens_sheet(page, sheet)
    # No matches offers clearing the search, which restores the list.
    page.locator("#historySearch").fill("zzz")
    expect(page.locator("#historyList")).to_contain_text("No matches")
    surface(page, "history.no-matches")
    page.get_by_role("button", name="Clear search").click()
    expect(page.locator("#historySearch")).to_have_value("")
    expect(rows).to_have_count(3)

    # Delete from the sheet goes through the confirm sheet: Escape answers
    # no, the verb deletes.
    rows.nth(0).locator(".action-row-kebab").click()
    sheet.get_by_role("button", name="Delete take").click()
    confirm = page.locator("dialog.confirm-sheet")
    expect(confirm.locator("h2")).to_have_text("Delete this take?")
    surface(page, "history.confirm-delete")
    page.keyboard.press("Escape")
    expect(confirm).to_have_count(0)
    assert mock.deletes == []
    rows.nth(0).locator(".action-row-kebab").click()
    sheet.get_by_role("button", name="Delete take").click()
    confirm.locator("[data-confirm='yes']").click()
    expect(rows).to_have_count(2)
    assert mock.deletes == ["s-one"]

    # Delete all lives in Settings › Storage, behind the same confirm sheet.
    page.locator("#paneHistory .home-settings").click()
    page.locator("#cleanAll").click()
    expect(confirm.locator("h2")).to_have_text("Delete all saved takes?")
    surface(page, "history.confirm-delete-all")
    confirm.get_by_role("button", name="Cancel").click()
    expect(confirm).to_have_count(0)
    page.locator("#cleanAll").click()
    confirm.locator("[data-confirm='yes']").click()
    expect(page.locator("#toast")).to_contain_text("History cleared")
    assert mock.deletes == ["s-one", "all"]

    # The empty state names its way forward.
    page.locator("#tabHistory").click()
    expect(rows).to_have_count(0)
    empty = page.locator("#historyList .empty-state")
    expect(empty).to_contain_text("No saved takes")
    expect(empty).to_contain_text("30 days")
    surface(page, "history.empty")
    empty.get_by_role("button", name="Capture a take").click()
    expect(page.locator("#paneCapture")).to_be_visible()
    assert native == []
