"""Webapp health watchdog (issue #110).

The tray is the only long-lived process watching the webapp, and it needs a
real ``/healthz`` round-trip to tell "up" from "dead" or "hung" — a crashed
uvicorn stops listening entirely, while a wedged one still LISTENs but never
answers. This turns either case into a loud, timestamped breadcrumb + toast
at the moment it's detected, instead of a mystery discovered days later
(photo-ocr#110: the webapp died at tray boot and stayed down for 6 days with
zero visibility).

Ported from app-launcher's ``app/tray/watchdog.py`` (issue #386), with one
addition: ``rearm()`` lets a wedge handler that already acted (e.g. attempted
a respawn) ask to be re-evaluated on the next tick instead of waiting for a
recovery that will never come on its own.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 60.0
DEFAULT_FAILURES_TO_ALERT = 3


class HealthWatchdog:
    """Consecutive-failure health monitor with edge-triggered callbacks.

    ``tick()`` runs one probe. After ``failures_to_alert`` *consecutive*
    failures it fires ``on_wedge(count)`` once — edge-triggered, not again
    until a recovery (or an explicit ``rearm()``) re-arms it — and the first
    success after an alert fires ``on_recover()``. The threshold absorbs a
    normal tray-menu webapp restart (a few seconds of downtime) without a
    false alarm at the default 60 s cadence.
    """

    def __init__(
        self,
        probe: Callable[[], bool],
        on_wedge: Callable[[int], None],
        on_recover: Callable[[], None],
        failures_to_alert: int = DEFAULT_FAILURES_TO_ALERT,
    ) -> None:
        self._probe = probe
        self._on_wedge = on_wedge
        self._on_recover = on_recover
        self._failures_to_alert = failures_to_alert
        self._consecutive_failures = 0
        self._alerted = False

    def rearm(self) -> None:
        """Allow ``on_wedge`` to fire again on the next failing tick, without
        waiting for a recovery. Call from within ``on_wedge`` when it already
        took action (e.g. attempted a respawn) and wants another shot at it
        next interval if that action didn't fix things."""
        self._alerted = False

    def tick(self) -> bool:
        """Run one probe; fire the edge callbacks. Returns the probe result."""
        try:
            ok = bool(self._probe())
        except Exception as exc:  # noqa: BLE001 — a raising probe is a failure
            logger.debug(f"watchdog probe raised: {exc}")
            ok = False

        if ok:
            if self._alerted:
                self._alerted = False
                self._on_recover()
            self._consecutive_failures = 0
            return True

        self._consecutive_failures += 1
        if (
            not self._alerted
            and self._consecutive_failures >= self._failures_to_alert
        ):
            self._alerted = True
            self._on_wedge(self._consecutive_failures)
        return False

    def run(
        self, stop: threading.Event, interval_s: float = DEFAULT_INTERVAL_S
    ) -> None:
        """Poll until ``stop`` is set. First probe fires after one interval,
        giving the webapp its startup window."""
        while not stop.wait(interval_s):
            self.tick()
