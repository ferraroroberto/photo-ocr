"""Unit tests for the webapp health watchdog (app/tray/watchdog.py, issue #110).

Covers the edge-triggered alert/recover contract, and the ``rearm()``
addition that lets a wedge handler ask to be re-evaluated on the next tick
instead of waiting forever for a recovery that a dead (not just wedged)
process will never produce on its own.
"""

from __future__ import annotations

from app.tray.watchdog import HealthWatchdog


def test_no_alert_below_threshold() -> None:
    wedges: list[int] = []
    wd = HealthWatchdog(
        probe=lambda: False,
        on_wedge=wedges.append,
        on_recover=lambda: None,
        failures_to_alert=3,
    )
    wd.tick()
    wd.tick()
    assert wedges == []


def test_alerts_once_at_threshold_and_stays_silent() -> None:
    wedges: list[int] = []
    wd = HealthWatchdog(
        probe=lambda: False,
        on_wedge=wedges.append,
        on_recover=lambda: None,
        failures_to_alert=2,
    )
    wd.tick()  # 1 failure
    wd.tick()  # 2 failures -> alert fires
    wd.tick()  # still failing, already alerted -> no re-fire
    wd.tick()
    assert wedges == [2]


def test_recover_fires_once_after_alert_and_resets() -> None:
    wedges: list[int] = []
    recoveries: list[None] = []
    wd = HealthWatchdog(
        probe=lambda: False,
        on_wedge=wedges.append,
        on_recover=lambda: recoveries.append(None),
        failures_to_alert=1,
    )
    wd.tick()  # alert
    assert wedges == [1]

    wd._probe = lambda: True  # simulate the webapp answering again
    wd.tick()
    assert len(recoveries) == 1

    # A later failure re-alerts from a clean slate.
    wd._probe = lambda: False
    wd.tick()
    assert wedges == [1, 1]


def test_rearm_lets_wedge_fire_again_without_a_recovery() -> None:
    """Simulates the "genuinely dead, respawn failed" path: the wedge
    handler acts, fails, calls rearm(), and the watchdog must retry on the
    next failing tick instead of staying silently alerted forever."""
    wedge_count = 0

    def on_wedge(count: int) -> None:
        nonlocal wedge_count
        wedge_count += 1
        wd.rearm()  # respawn attempt failed; ask to be re-evaluated next tick

    wd = HealthWatchdog(
        probe=lambda: False,
        on_wedge=on_wedge,
        on_recover=lambda: None,
        failures_to_alert=1,
    )
    wd.tick()
    wd.tick()
    wd.tick()
    assert wedge_count == 3


def test_probe_exception_counts_as_failure() -> None:
    wedges: list[int] = []

    def raising_probe():
        raise RuntimeError("boom")

    wd = HealthWatchdog(
        probe=raising_probe,
        on_wedge=wedges.append,
        on_recover=lambda: None,
        failures_to_alert=1,
    )
    wd.tick()
    assert wedges == [1]
