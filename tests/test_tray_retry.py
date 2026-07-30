"""Unit tests for the initial-spawn retry/backoff helper (app/tray/tray.py,
issue #110).

Before this fix, ``tray.py``'s ``_start()`` called ``WebappManager.start()``
exactly once at tray boot; any failure — including a purely transient one —
left the webapp dead until a human noticed and clicked "Restart webapp".
``_retry_with_backoff`` is what makes the first N failures self-heal instead.
"""

from __future__ import annotations

import pytest

from app.tray.tray import STARTUP_RETRY_DELAYS_S, _retry_with_backoff


def test_succeeds_on_first_try_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("app.tray.tray.time.sleep", slept.append)

    calls = []
    _retry_with_backoff(lambda: calls.append(1), delays=(5.0, 15.0))

    assert calls == [1]
    assert slept == []


def test_recovers_after_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the original bug's fix: a transient failure (port not free
    yet, cert renewal in flight) at tray boot must no longer permanently kill
    the webapp — it must retry and succeed."""
    slept: list[float] = []
    monkeypatch.setattr("app.tray.tray.time.sleep", slept.append)

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError(f"transient failure #{attempts['n']}")

    failures_seen = []
    _retry_with_backoff(
        flaky,
        delays=(5.0, 15.0, 30.0),
        on_attempt_failed=lambda attempt, exc: failures_seen.append((attempt, str(exc))),
    )

    assert attempts["n"] == 3
    assert slept == [5.0, 15.0]
    assert failures_seen == [
        (1, "transient failure #1"),
        (2, "transient failure #2"),
    ]


def test_raises_after_delays_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("app.tray.tray.time.sleep", slept.append)

    def always_fails():
        raise RuntimeError("permanently down")

    failures_seen = []
    with pytest.raises(RuntimeError, match="permanently down"):
        _retry_with_backoff(
            always_fails,
            delays=(1.0, 2.0),
            on_attempt_failed=lambda attempt, exc: failures_seen.append(attempt),
        )

    # 1 initial attempt + 2 retries = 3 failures total, 2 sleeps in between.
    assert failures_seen == [1, 2, 3]
    assert slept == [1.0, 2.0]


def test_default_startup_delays_are_nonempty() -> None:
    # Sanity: the production constant used by tray.py's real _start() must
    # actually retry, not be an accidental no-op tuple.
    assert len(STARTUP_RETRY_DELAYS_S) >= 2
