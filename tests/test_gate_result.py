"""The pre-ship gate ends every run with a `GATE-RESULT: PASS|FAIL` stdout line.

A pipeline reports its last command's status, so `verify-before-ship.ps1 | tail`
hands the caller `tail`'s 0 and a red gate reads green. The verdict line is the
part a pipe cannot rewrite. The helper block in the gate (between the
`gate-verdict` markers) is run for real, over a captured stdout (the same
anonymous pipe `|` creates), for a pass, a failed stage and an early terminating
error; the real script is run for the path that needs no stage to execute.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "scripts" / "verify-before-ship.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the gate is a Windows PowerShell script")


def _gate_text() -> str:
    return GATE.read_text(encoding="utf-8").replace("\r\n", "\n")


def _verdict_block() -> str:
    match = re.search(r"# gate-verdict:begin\n(.*?)# gate-verdict:end", _gate_text(), re.DOTALL)
    assert match, "the gate lost its gate-verdict block"
    return match.group(1)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=120,
    )


def _run_scenario(scenario: str) -> subprocess.CompletedProcess[str]:
    # Fail reads the gate's stopwatch, so the scenario defines it as the gate does.
    script = (
        '$ErrorActionPreference = "Stop"\n'
        "$sw = [System.Diagnostics.Stopwatch]::StartNew()\n"
        f"{_verdict_block()}\n{scenario}\n"
    )
    return _run(["-Command", script])


def _last_line(run: subprocess.CompletedProcess[str]) -> str:
    lines = [line.rstrip() for line in run.stdout.splitlines() if line.strip()]
    assert lines, f"nothing on stdout; stderr={run.stderr!r}"
    return lines[-1]


@windows_only
def test_pass_ends_with_pass_and_exits_zero() -> None:
    run = _run_scenario("Complete-Gate 0")
    assert run.returncode == 0, run.stdout
    assert _last_line(run) == "GATE-RESULT: PASS"


@windows_only
def test_failed_stage_ends_with_fail_and_exits_one() -> None:
    run = _run_scenario('Fail "static ratchet failed."')
    assert run.returncode == 1, run.stdout
    assert _last_line(run) == "GATE-RESULT: FAIL"
    assert "[X] static ratchet failed." in run.stdout


@windows_only
def test_early_terminating_error_reports_on_stdout_then_fails() -> None:
    run = _run_scenario('throw "boom before any stage"')
    assert run.returncode == 1, run.stdout
    assert _last_line(run) == "GATE-RESULT: FAIL"
    assert "boom before any stage" in run.stdout, "the error must reach stdout, not only stderr"


@windows_only
def test_missing_venv_ends_with_fail_and_exits_one(tmp_path: Path) -> None:
    """The real script, copied beside no `.venv`, fails before running any stage."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    gate = scripts / GATE.name
    gate.write_bytes(GATE.read_bytes())
    run = _run(["-File", str(gate)])
    assert run.returncode == 1, run.stdout
    assert _last_line(run) == "GATE-RESULT: FAIL"
    assert ".venv missing" in run.stdout


def test_every_exit_goes_through_the_verdict() -> None:
    """A new `exit` that skips `Complete-Gate` would end a run with no verdict."""
    text = _gate_text()
    outside = text.replace(_verdict_block(), "")
    assert not re.search(r"^\s*exit\s+\d", outside, re.MULTILINE), "exit through Complete-Gate, not directly"
    assert text.rstrip().endswith("Complete-Gate 0"), "the pass path must end on its verdict"


def test_verdict_block_is_ascii_for_windows_powershell_5_1() -> None:
    """Windows PowerShell 5.1 reads a BOM-less file as ANSI, so the block stays ASCII."""
    _verdict_block().encode("ascii")
