r"""The gate's docs-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.docs_ratchet_gate

`scripts/docs_ratchet.py` (vendored, never edited here) exits 0 on a pass, 1 when
a README size or history-marker count rose, 2 on a bad table or baseline and 3
when a doc could not be read (git unusable, not UTF-8, an unclosed fence or
comment). Exit 3 means part of the tree was not checked, so it must fail the
gate as its own state and never read as a pass or a skip. The runner is the
static-ratchet wrapper's (timeout and unstartable script map to exit 3 there);
this module supplies the script and the docs verdict labels, and
`verify-before-ship.ps1` calls it, never the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import static_ratchet_gate as runner

SCRIPT = runner.REPO_ROOT / "scripts" / "docs_ratchet.py"

_FAIL_LABELS = {
    runner.EXIT_NEW: "a README size or history-marker count rose, or the baseline is missing",
    runner.EXIT_CONFIG: "bad [docs_ratchet] table, baseline or arguments",
    runner.EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a doc could not be read, so it was not checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == runner.EXIT_PASS:
        return True, "no README growth and no new history marker"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return runner.run(script, stage="docs ratchet", classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
