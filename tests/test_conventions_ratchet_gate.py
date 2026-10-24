"""The gate's conventions-ratchet stage fails on exit 3 as NOT CONFIRMED.

`scripts/conventions_ratchet.py` exits 3 when git is unusable or a tracked `.py`
file cannot be read or parsed: part of the tree was not checked. These tests pin
that the script really exits 3 then, and that the pre-ship gate maps 3 to its own
failure -- never a pass, never a skip -- and runs the stage unrouted. Throwaway
git repos in a temp directory: nothing real is rewritten.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PS1 = REPO_ROOT / "scripts" / "verify-before-ship.ps1"
SCRIPT = REPO_ROOT / "scripts" / "conventions_ratchet.py"


def _gate_text() -> str:
    return GATE_PS1.read_text(encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )


def test_an_unparseable_file_exits_three_not_a_pass(tmp_path: Path) -> None:
    (tmp_path / ".fleet.toml").write_text(
        '[conventions_ratchet]\nenabled = true\nrules = ["no-window"]\n', encoding="utf-8"
    )
    (tmp_path / ".conventions-ratchet-baseline.json").write_text(
        '{"version": 1, "entries": []}\n', encoding="utf-8"
    )
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW, check=False,
    )
    assert proc.returncode == 3
    assert "broken.py" in proc.stdout + proc.stderr


def test_the_gate_maps_exit_three_to_its_own_failure() -> None:
    lines = _gate_text().splitlines()
    run = [line for line in lines if "conventions_ratchet" in line and line.lstrip().startswith("&")]
    assert len(run) == 1
    exit_three = [line for line in lines if "$conventionsExit -eq 3" in line]
    assert len(exit_three) == 1
    assert "NOT CONFIRMED" in exit_three[0]
    assert "Fail" in exit_three[0]
    assert any("$conventionsExit -ne 0" in line and "Fail" in line for line in lines)


def test_the_stage_follows_the_other_ratchets_and_is_not_path_routed() -> None:
    text = _gate_text()
    assert text.index("scripts.docs_ratchet_gate") < text.index("conventions_ratchet.py")
    assert text.index("structure_ratchet.py") < text.index("conventions_ratchet.py")
    # Before the e2e routing block, so a diff that routes to tier `skip` still runs
    # it: a rule count needs the whole tree.
    assert text.index("conventions_ratchet.py") < text.index('$tier = "full"')


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    table = fleet["conventions_ratchet"]
    assert table["enabled"] is True
    assert (REPO_ROOT / table["baseline"]).is_file()
    # A helper rule without its helper would exit 2; the repo has neither helper.
    assert set(table["rules"]) == {"no-window", "logging-not-print", "no-drive-path"}
    entry = fleet["vendored"]["conventions_ratchet"]
    assert entry["dest"] == "scripts/conventions_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
