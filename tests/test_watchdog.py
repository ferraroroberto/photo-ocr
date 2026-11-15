"""Unit tests for the webapp health watchdog and the tray's respawn guard (app/tray/watchdog.py).

Covers the edge-triggered alert/recover contract, and the ``rearm()``
addition that lets a wedge handler ask to be re-evaluated on the next tick
instead of waiting forever for a recovery that a dead (not just wedged)
process will never produce on its own.
"""

from __future__ import annotations

import pytest

from app.tray import tray
from app.tray.watchdog import DEFAULT_STARTUP_WINDOW_S, HealthWatchdog, RespawnGuard


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


# --- respawn guard: the tray handler never stacks a second webapp ---
#
# On a slow boot the child is still importing when the next health probe fails.
# A handler that spawns again leaves two copies bound to one port. These drive
# ``_handle_wedge`` with a fake process table and a fake clock: no real process
# starts and no real time passes.

class _World:
    """A fake process table, clock and manager the guard and handler act on."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.alive: set[int] = set()
        self.spawned: list[int] = []
        self.stopped: list[int] = []
        self.logs: list[str] = []
        self.rearms = 0
        self.port_in_use = False
        self.next_pid = 100
        self.spawned_pid: int | None = None
        self.guard = RespawnGuard(
            self.logs.append,
            identity=lambda pid: 7 if pid in self.alive else None,
            stop_tree=self._stop,
            clock=lambda: self.now,
        )

    def _stop(self, pid: int) -> None:
        self.stopped.append(pid)
        self.alive.discard(pid)

    # --- the slice of WebappManager the tray handler uses ---
    def start(self, wait: bool = True) -> None:
        self.next_pid += 1
        self.spawned.append(self.next_pid)
        self.alive.add(self.next_pid)
        self.spawned_pid = self.next_pid

    def is_port_in_use(self) -> bool:
        return self.port_in_use

    def wedge(self) -> None:
        tray._handle_wedge(3, self, self.guard, self.logs.append, self._rearm)  # type: ignore[arg-type]

    def _rearm(self) -> None:
        self.rearms += 1


@pytest.fixture(autouse=True)
def _no_toasts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tray, "_notify", lambda title, message: None)


def test_second_spawn_refused_while_first_child_is_starting() -> None:
    w = _World()
    w.wedge()  # first failed probe window: nothing alive, so the first child spawns
    assert w.spawned == [101]

    w.now += 60  # next interval: child alive, port still not bound
    w.wedge()
    w.now += 60
    w.wedge()

    assert w.spawned == [101], "a second copy was spawned beside a booting first"
    assert w.stopped == []
    assert w.rearms == 3
    assert any("still starting" in line for line in w.logs)


def test_child_that_never_gets_healthy_is_stopped_before_the_next_spawn() -> None:
    w = _World()
    w.wedge()
    w.now += DEFAULT_STARTUP_WINDOW_S + 1
    w.wedge()

    assert w.stopped == [101]
    assert w.spawned == [101, 102]
    assert w.alive == {102}, "exactly one child is alive after the respawn"


def test_child_that_already_exited_is_replaced_without_a_stop() -> None:
    w = _World()
    w.wedge()
    w.alive.clear()  # the child crashed
    w.wedge()

    assert w.spawned == [101, 102]
    assert w.stopped == []
    assert any("already exited" in line for line in w.logs)


def test_listening_but_unanswering_webapp_is_never_respawned() -> None:
    w = _World()
    w.wedge()
    w.now += DEFAULT_STARTUP_WINDOW_S + 1  # past the startup window
    w.port_in_use = True  # bound, but /healthz is silent
    w.wedge()

    assert w.spawned == [101]
    assert w.stopped == []


def test_menu_restart_is_refused_while_the_child_is_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _World()
    w.wedge()
    restarts: list[bool] = []

    class _Threads:
        @staticmethod
        def Thread(target, daemon):  # noqa: N802 — mirrors threading.Thread
            class _Run:
                @staticmethod
                def start() -> None:
                    target()

            return _Run

    monkeypatch.setattr(tray.threading, "Thread", _Threads.Thread)
    w.restart = lambda wait=True: restarts.append(wait)  # type: ignore[attr-defined]
    w.base_url = "https://127.0.0.1:1"  # type: ignore[attr-defined]

    tray._restart_webapp_in_background(w, w.guard)  # type: ignore[arg-type]

    assert restarts == []
