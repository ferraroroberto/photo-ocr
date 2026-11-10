"""The extraction phase, declared once: the derive table, the transition
table, the refusal of an illegal move, and the meta.json shapes older builds
wrote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.archive import Session, SessionArchive
from src.extract_job import extract_status_payload, set_extract_progress
from src.extract_state import (
    ACTIVE_PHASES,
    ExtractPhase,
    IllegalExtractTransition,
    derive_extract_phase,
    is_legal_transition,
)

P = ExtractPhase


@pytest.mark.parametrize(
    "stored, succeeded, expected",
    [
        # No stored phase: read from extract_succeeded (takes older than phases).
        (None, None, P.IDLE),
        (None, True, P.SUCCEEDED),
        (None, False, P.FAILED),
        ("", None, P.IDLE),
        # A stored phase wins over extract_succeeded, whichever it says.
        ("idle", None, P.IDLE),
        ("queued", None, P.QUEUED),
        ("running", None, P.RUNNING),
        ("merging", None, P.MERGING),
        ("succeeded", True, P.SUCCEEDED),
        ("failed", False, P.FAILED),
        # A redo in flight over a good text: extract_succeeded stays True.
        ("queued", True, P.QUEUED),
        ("running", True, P.RUNNING),
        ("merging", True, P.MERGING),
        # A redo that failed over a good text: extract_succeeded flipped to False.
        ("failed", False, P.FAILED),
        # A retry that read nothing: phase back to succeeded, flag still False.
        ("succeeded", False, P.SUCCEEDED),
        # A phase this build does not know falls back to extract_succeeded.
        ("paused", True, P.SUCCEEDED),
        ("paused", False, P.FAILED),
        ("paused", None, P.IDLE),
    ],
)
def test_derive_extract_phase_table(
    stored: Optional[str], succeeded: Optional[bool], expected: ExtractPhase
) -> None:
    assert derive_extract_phase(stored, succeeded) is expected


def test_phase_values_are_the_wire_strings() -> None:
    assert {p.value for p in ExtractPhase} == {
        "idle", "queued", "running", "merging", "succeeded", "failed",
    }
    assert P.RUNNING == "running"  # a str Enum: compares equal to the wire string
    assert ACTIVE_PHASES == {P.QUEUED, P.RUNNING, P.MERGING}


@pytest.mark.parametrize(
    "current, new, legal",
    [
        (P.IDLE, P.QUEUED, True),
        (P.IDLE, P.RUNNING, True),  # the single-shot route skips the queue
        (P.QUEUED, P.RUNNING, True),
        (P.RUNNING, P.RUNNING, True),  # a progress tick
        (P.RUNNING, P.MERGING, True),
        (P.MERGING, P.SUCCEEDED, True),
        (P.RUNNING, P.FAILED, True),
        (P.QUEUED, P.FAILED, True),  # settled after a crash before the job began
        (P.SUCCEEDED, P.QUEUED, True),  # redo, retry
        (P.FAILED, P.QUEUED, True),  # redo after a failure
        (P.SUCCEEDED, P.MERGING, True),  # a boot sweep finds a redo mid-merge
        (P.SUCCEEDED, P.SUCCEEDED, True),  # a reused read
        # Edges that do not exist.
        (P.IDLE, P.SUCCEEDED, False),
        (P.QUEUED, P.MERGING, False),
        (P.RUNNING, P.QUEUED, False),
        (P.MERGING, P.RUNNING, False),
        (P.MERGING, P.QUEUED, False),
        (P.SUCCEEDED, P.FAILED, False),
        (P.FAILED, P.SUCCEEDED, False),
    ],
)
def test_transition_table(current: ExtractPhase, new: ExtractPhase, legal: bool) -> None:
    assert is_legal_transition(current, new) is legal


# ------------------------------------------------------------- on a real take


def _take(tmp_path: Path, meta_patch: Dict[str, Any]) -> Session:
    """A take whose meta.json carries ``meta_patch``, as an older build wrote it."""
    archive = SessionArchive(root=tmp_path / "archive")
    session = archive.new_session()
    path = session.folder / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(meta_patch)
    path.write_text(json.dumps(meta), encoding="utf-8")
    loaded = archive.get(session.session_id)
    assert loaded is not None
    return loaded


def test_illegal_transition_is_refused_and_leaves_the_take_untouched(
    tmp_path: Path,
) -> None:
    # state-machine-proof: extract-phase
    session = _take(
        tmp_path,
        {"extract_succeeded": True, "extra": {"extract_progress": {"phase": "succeeded"}}},
    )
    before = (session.folder / "meta.json").read_text(encoding="utf-8")

    with pytest.raises(IllegalExtractTransition):
        set_extract_progress(session, phase="failed")

    assert (session.folder / "meta.json").read_text(encoding="utf-8") == before
    assert extract_status_payload(session)["phase"] == "succeeded"


def test_unknown_phase_string_is_refused(tmp_path: Path) -> None:
    session = _take(tmp_path, {})
    with pytest.raises(ValueError):
        set_extract_progress(session, phase="paused")


def test_a_legal_chain_is_written(tmp_path: Path) -> None:
    session = _take(tmp_path, {})
    for phase in ("queued", "running", "running", "merging", "succeeded"):
        set_extract_progress(session, phase=phase)
        assert extract_status_payload(session, include_extracted=False)["phase"] == phase


# Archives written by earlier builds report the same phase after the change.
@pytest.mark.parametrize(
    "meta_patch, phase",
    [
        ({"extra": {"extract_progress": {"phase": "running"}}}, "running"),
        (
            {"extract_succeeded": True, "extra": {"extract_progress": {"phase": "succeeded"}}},
            "succeeded",
        ),
        ({"extract_succeeded": True}, "succeeded"),  # no phase recorded
        ({"extract_succeeded": False, "error": "boom"}, "failed"),  # no phase recorded
        ({}, "idle"),  # neither
    ],
)
def test_archive_written_before_the_change_reports_the_same_phase(
    tmp_path: Path, meta_patch: Dict[str, Any], phase: str
) -> None:
    payload = extract_status_payload(_take(tmp_path, meta_patch), include_extracted=False)
    assert payload["phase"] == phase


def test_failed_redo_over_text_is_failed_and_still_has_text(tmp_path: Path) -> None:
    session = _take(
        tmp_path,
        {
            "extract_succeeded": False,
            "error": "hub said no",
            "extracted_chars": 11,
            "extra": {"extract_progress": {"phase": "failed", "error": "hub said no"}},
        },
    )
    (session.folder / "extracted.txt").write_text("hello world", encoding="utf-8")

    payload = extract_status_payload(session, include_extracted=False)

    assert payload["phase"] == "failed"
    assert payload["has_text"] is True
    assert payload["error"] == "hub said no"


def test_failed_first_read_has_no_text(tmp_path: Path) -> None:
    session = _take(
        tmp_path,
        {"extract_succeeded": False, "error": "hub said no"},
    )
    payload = extract_status_payload(session, include_extracted=False)
    assert payload["phase"] == "failed"
    assert payload["has_text"] is False


@pytest.mark.parametrize(
    "stored, in_flight, failed",
    [
        ("queued", True, False),
        ("running", True, False),
        ("merging", True, False),
        ("succeeded", False, False),
        ("failed", False, True),
        (None, False, False),
    ],
)
def test_payload_flags_follow_the_derived_phase(
    tmp_path: Path, stored: Optional[str], in_flight: bool, failed: bool
) -> None:
    progress = {"phase": stored} if stored else {}
    session = _take(tmp_path, {"extra": {"extract_progress": progress}})
    payload = extract_status_payload(session, include_extracted=False)
    assert (payload["in_flight"], payload["failed"]) == (in_flight, failed)
