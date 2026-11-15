"""Unit tests for the initial-spawn retry/backoff the tray uses at boot
(``retry_with_backoff`` in the vendored app/tray/watchdog.py).

``tray.py``'s ``_start()`` used to call ``WebappManager.start()`` exactly once;
any failure — including a purely transient one — left the webapp dead until a
human clicked "Restart webapp". The retry is what makes the first N failures
self-heal instead.
"""

from __future__ import annotations

import pytest

from app.tray.watchdog import DEFAULT_STARTUP_RETRY_DELAYS_S, retry_with_backoff


def test_succeeds_on_first_try_no_sleep() -> None:
    slept: list[float] = []

    calls = []
    retry_with_backoff(lambda: calls.append(1), delays=(5.0, 15.0), sleep=slept.append)

    assert calls == [1]
    assert slept == []


def test_recovers_after_transient_failures() -> None:
    """A transient failure (port not free yet, cert renewal in flight) at tray
    boot must not permanently kill the webapp — it must retry and succeed."""
    slept: list[float] = []

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError(f"transient failure #{attempts['n']}")

    failures_seen = []
    retry_with_backoff(
        flaky,
        delays=(5.0, 15.0, 30.0),
        on_attempt_failed=lambda attempt, exc: failures_seen.append((attempt, str(exc))),
        sleep=slept.append,
    )

    assert attempts["n"] == 3
    assert slept == [5.0, 15.0]
    assert failures_seen == [
        (1, "transient failure #1"),
        (2, "transient failure #2"),
    ]


def test_raises_after_delays_exhausted() -> None:
    slept: list[float] = []

    def always_fails():
        raise RuntimeError("permanently down")

    failures_seen = []
    with pytest.raises(RuntimeError, match="permanently down"):
        retry_with_backoff(
            always_fails,
            delays=(1.0, 2.0),
            on_attempt_failed=lambda attempt, exc: failures_seen.append(attempt),
            sleep=slept.append,
        )

    # 1 initial attempt + 2 retries = 3 failures total, 2 sleeps in between.
    assert failures_seen == [1, 2, 3]
    assert slept == [1.0, 2.0]


def test_default_startup_delays_are_nonempty() -> None:
    # Sanity: the production constant the tray's real _start() uses must
    # actually retry, not be an accidental no-op tuple.
    assert len(DEFAULT_STARTUP_RETRY_DELAYS_S) >= 2
