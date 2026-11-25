"""The launchers live in scripts/; the root keeps only tray.bat and setup.bat.

tray.bat's `-TrayMatch` must find a tray started from either spelling of the launcher so a
restart replaces a tray that was launched before the move, and the moved launcher must still
resolve the repo root from its new folder.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAY_BAT = REPO_ROOT / "tray.bat"


def _tray_match_pattern() -> re.Pattern[str]:
    text = TRAY_BAT.read_text(encoding="utf-8")
    found = re.search(r'-TrayMatch "([^"]+)"', text)
    assert found, "tray.bat no longer passes -TrayMatch"
    return re.compile(found.group(1))


@pytest.mark.parametrize(
    "command_line",
    [
        r"C:\repo\.venv\Scripts\pythonw.exe launcher.py tray",
        r"C:\repo\.venv\Scripts\pythonw.exe scripts\launcher.py tray",
        r"C:\repo\.venv\Scripts\pythonw.exe scripts/launcher.py tray",
    ],
)
def test_tray_match_finds_both_launcher_spellings(command_line: str) -> None:
    assert _tray_match_pattern().search(command_line)


def test_tray_match_ignores_other_launcher_commands() -> None:
    assert not _tray_match_pattern().search(r"pythonw.exe scripts\launcher.py webapp")


def test_tray_launches_the_moved_launcher() -> None:
    assert r'set "TRAY_LAUNCH=scripts\launcher.py tray"' in TRAY_BAT.read_text(encoding="utf-8")


def test_root_holds_no_moved_launcher_files() -> None:
    for name in ("launcher.py", "webapp.bat", "webapp_tunnel_named.bat"):
        assert not (REPO_ROOT / name).exists(), name
        assert (REPO_ROOT / "scripts" / name).is_file(), name


@pytest.mark.parametrize("name", ["webapp.bat", "webapp_tunnel_named.bat"])
def test_moved_bat_resolves_the_repo_root_from_its_parent(name: str) -> None:
    src = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
    assert r'for %%I in ("%~dp0..") do set "SCRIPT_DIR=%%~fI\"' in src


def test_moved_launcher_resolves_the_repo_root_and_prints_usage() -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter and script
        [sys.executable, str(REPO_ROOT / "scripts" / "launcher.py"), "no-such-command"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT.parent,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert done.returncode == 2
    assert "usage:" in done.stderr
