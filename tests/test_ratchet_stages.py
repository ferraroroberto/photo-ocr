"""Gate wiring for the scaffold ratchets added after the conventions stage.

Every row of STAGES is one vendored ratchet. Per row, three facts are pinned: the
script exits 3 (NOT CONFIRMED) when part of the tree cannot be read, the pre-ship
gate maps 3 to its own failure and runs the stage ahead of the e2e routing block,
and the ratchet is declared in `.fleet.toml` with its script vendored. Throwaway git
repos in a temp directory: nothing real is rewritten.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PS1 = REPO_ROOT / "scripts" / "verify-before-ship.ps1"

# name -> (exit variable in the gate, repo files that make the script exit 3)
STAGES: dict[str, tuple[str, dict[str, bytes]]] = {
    "comment": ("$commentExit", {"broken.py": b"def f(:\n"}),
    "instruction": ("$instructionExit", {"CLAUDE.md": b"\xff\xfe not utf-8\n"}),
    "doc_refs": ("$docRefsExit", {"README.md": b"\xff\xfe not utf-8\n"}),
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
    )


@pytest.mark.parametrize("name", STAGES)
def test_an_unreadable_tree_exits_three_not_a_pass(name: str, tmp_path: Path) -> None:
    files = {
        ".fleet.toml": f"[{name}_ratchet]\nenabled = true\n".encode(),
        f".{name.replace('_', '-')}-ratchet-baseline.json": b'{"version": 1, "entries": []}\n',
        **STAGES[name][1],
    }
    for rel, data in files.items():
        (tmp_path / rel).write_bytes(data)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / f"{name}_ratchet.py"), "--root", str(tmp_path)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW, check=False,
    )
    assert proc.returncode == 3
    assert next(iter(STAGES[name][1])) in proc.stdout + proc.stderr


@pytest.mark.parametrize("name", STAGES)
def test_the_gate_runs_the_stage_unrouted_and_fails_closed_on_three(name: str) -> None:
    text = GATE_PS1.read_text(encoding="utf-8")
    lines = text.splitlines()
    var = STAGES[name][0]
    run = [ln for ln in lines if f"{name}_ratchet.py" in ln and ln.lstrip().startswith("&")]
    assert len(run) == 1
    three = [ln for ln in lines if f"{var} -eq 3" in ln]
    assert len(three) == 1 and "NOT CONFIRMED" in three[0] and "Fail" in three[0]
    assert any(f"{var} -ne 0" in ln and "Fail" in ln for ln in lines)
    # Ahead of the e2e routing block, so a diff routed to tier `skip` still runs it:
    # a whole-tree count cannot be narrowed by the diff.
    assert text.index(f"{name}_ratchet.py") < text.index('$tier = "full"')


@pytest.mark.parametrize("name", STAGES)
def test_the_ratchet_is_declared_with_its_script_vendored(name: str) -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    table = fleet[f"{name}_ratchet"]
    assert table["enabled"] is True
    assert (REPO_ROOT / table["baseline"]).is_file()
    entry = fleet["vendored"][f"{name}_ratchet"]
    assert entry["dest"] == f"scripts/{name}_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
