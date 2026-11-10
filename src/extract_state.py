"""The extraction lifecycle of a take, declared once.

A take's extraction is a small state machine. Its facts are stored in two
places (``meta.extra["extract_progress"]["phase"]`` and
``meta.extract_succeeded``); this module owns the phase names, the set of
non-terminal phases, the one function that maps those facts to a phase, and
the table of legal moves between phases. Every reader asks here instead of
re-spelling a phase string. See ``docs/extract-phase-state.md``.

Framework-free: no FastAPI, no archive import.
"""

from __future__ import annotations

# Standard library imports
from enum import Enum
from typing import Dict, FrozenSet, Optional


class ExtractPhase(str, Enum):
    """Where a take's extraction is. The values are the wire strings stored
    in ``meta.json`` and returned by the API."""

    IDLE = "idle"  # never read; nothing queued
    QUEUED = "queued"  # accepted, the job has not started
    RUNNING = "running"  # reading photos (chunks_done / chunks_total)
    MERGING = "merging"  # chunks read, collating and writing the text
    SUCCEEDED = "succeeded"  # a read finished; the text on disk is current
    FAILED = "failed"  # the last run failed; ``error`` says why


# The phases a live job can hold; a take in one of them cannot be edited,
# restarted or retried.
ACTIVE_PHASES: FrozenSet[ExtractPhase] = frozenset(
    {ExtractPhase.QUEUED, ExtractPhase.RUNNING, ExtractPhase.MERGING}
)

# Legal moves between two different phases; staying in a phase (a progress
# tick) is always legal. A take that is not in flight (idle, succeeded, failed)
# may enter any active phase: a redo or retry queues, the single-shot route
# starts running, and a boot sweep may find a job at any active phase. A job
# that dies is settled from an active phase to a terminal one; a terminal
# phase never turns into the other terminal phase.
_LEGAL_MOVES: Dict[ExtractPhase, FrozenSet[ExtractPhase]] = {
    ExtractPhase.IDLE: ACTIVE_PHASES,
    ExtractPhase.QUEUED: frozenset(
        {ExtractPhase.RUNNING, ExtractPhase.SUCCEEDED, ExtractPhase.FAILED}
    ),
    ExtractPhase.RUNNING: frozenset(
        {ExtractPhase.MERGING, ExtractPhase.SUCCEEDED, ExtractPhase.FAILED}
    ),
    ExtractPhase.MERGING: frozenset({ExtractPhase.SUCCEEDED, ExtractPhase.FAILED}),
    ExtractPhase.SUCCEEDED: ACTIVE_PHASES,
    ExtractPhase.FAILED: ACTIVE_PHASES,
}


class IllegalExtractTransition(ValueError):
    """A write that would move a take's extraction along an edge that does
    not exist (for example ``succeeded`` straight to ``failed``)."""


def derive_extract_phase(
    stored_phase: Optional[str], extract_succeeded: Optional[bool]
) -> ExtractPhase:
    """The phase of a take from the two stored facts.

    A recorded phase wins. A take written before phases were recorded (or
    whose recorded phase is not one of ours) is read from ``extract_succeeded``:
    True is succeeded, False is failed, None is idle.
    """
    try:
        return ExtractPhase(stored_phase)
    except ValueError:
        pass
    if extract_succeeded is True:
        return ExtractPhase.SUCCEEDED
    if extract_succeeded is False:
        return ExtractPhase.FAILED
    return ExtractPhase.IDLE


def is_legal_transition(current: ExtractPhase, new: ExtractPhase) -> bool:
    return current == new or new in _LEGAL_MOVES[current]


def require_legal_transition(current: ExtractPhase, new: ExtractPhase) -> None:
    """Refuse a move the table does not list."""
    if not is_legal_transition(current, new):
        raise IllegalExtractTransition(
            f"extraction cannot move from {current.value} to {new.value}"
        )
