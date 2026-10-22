"""The gate's docs-ratchet stage maps the vendored script's exit codes.

`scripts/docs_ratchet.py` exits 3 when a doc could not be read (git unusable, a
file that is not UTF-8, a fence or comment that never closes): part of the tree
was not checked. These tests pin that 3 fails as NOT CONFIRMED -- never a pass,
never a skip -- and that the pre-ship gate calls the wrapper that guarantees it
rather than the script. The runner (timeout and unstartable-script handling) is
the static-ratchet wrapper's and is tested in test_static_ratchet_gate.py. Fake
scripts in a temp directory: no real docs are rewritten and never a real photo
library, a private image or a live OCR backend.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts import docs_ratchet_gate as gate
from tests.test_static_ratchet_gate import _fake_script

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PS1 = REPO_ROOT / "scripts" / "verify-before-ship.ps1"


def test_exit_three_is_not_confirmed_and_never_a_skip() -> None:
    ok, label = gate.classify_exit(3)
    assert not ok
    assert "NOT CONFIRMED" in label
    assert "skip" not in label.lower()
    assert [gate.classify_exit(c)[0] for c in (0, 1, 2, 4, -1)] == [True] + [False] * 4


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_exits_nonzero_for_every_script_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    got = gate.run(_fake_script(tmp_path, code, "README.md: fence never closes"))
    out = capsys.readouterr().out
    assert got == expected
    assert "fence never closes" in out  # the script's own finding is echoed
    last = out.rstrip().splitlines()[-1]
    assert last.startswith("OK: docs ratchet" if code == 0 else "FAIL: docs ratchet")
    assert ("NOT CONFIRMED" in last) is (code == 3)


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    # Comments may name the script; only a line that runs something counts.
    ratchet = [
        line
        for line in GATE_PS1.read_text(encoding="utf-8").splitlines()
        if "docs_ratchet" in line and line.lstrip().startswith("&")
    ]
    assert len(ratchet) == 1
    assert "-m scripts.docs_ratchet_gate" in ratchet[0]
    assert "docs_ratchet.py" not in ratchet[0]


def test_the_docs_stage_follows_the_static_ratchet_and_is_not_path_routed() -> None:
    text = GATE_PS1.read_text(encoding="utf-8")
    assert text.index("scripts.static_ratchet_gate") < text.index("scripts.docs_ratchet_gate")
    # Before the e2e routing block, so a docs-only diff (e2e tier `skip`) still
    # runs it: a count needs the whole tree.
    assert text.index("scripts.docs_ratchet_gate") < text.index('$tier = "full"')


def test_the_ratchet_is_declared_and_its_script_is_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet["docs_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["docs_ratchet"]["baseline"]).is_file()
    entry = fleet["vendored"]["docs_ratchet"]
    assert entry["dest"] == "scripts/docs_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
