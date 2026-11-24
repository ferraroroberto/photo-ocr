"""The main page keeps its request budget on a frozen clock.

The request-budget ratchet reads source; this runs the page. A fake clock
(`page.clock`) lets the vendored probe count what the Capture screen really
sends in a visible minute, in a hidden minute, and on the return to the
foreground (project-scaffolding docs/polling-budget.md, "The runtime check").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

from playwright.sync_api import Page

from tests.e2e.budget._request_budget import (
    BudgetReading,
    assert_request_budget,
    measure_request_budget,
    visible_budget,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The one request a hidden page may send. The usage log pushes `session_end`
# and flushes its queue once, as a beacon, at the moment the tab hides, so the
# next visit's events are not lost; it is a single send at the transition, not
# a poll, and nothing follows it while the page stays hidden.
_HIDE_FLUSH = Counter({"/api/usage/events": 1})


def _without_hide_flush(reading: BudgetReading) -> BudgetReading:
    """`reading` minus the allowed hide-time flush; a second one still counts."""
    return replace(reading, hidden=reading.hidden - _HIDE_FLUSH)


def test_main_page_keeps_its_request_budget(isolated_page: Page, base_url: str) -> None:
    reading = measure_request_budget(isolated_page, f"{base_url}/")
    assert_request_budget(_without_hide_flush(reading), visible_budget(_REPO_ROOT))
