"""Windows console-suppression flag for every ``subprocess`` spawn in this repo.

Global ``CLAUDE.md`` convention (fleet-config#399): any spawn of an external
executable must pass ``creationflags=subprocess.CREATE_NO_WINDOW`` on Windows,
because a parent with no console of its own — the tray, a scheduled task, a
daemon — otherwise gets a console window flashed on screen for every call.
That is this repo's normal case: the tray and the webapp it manages run with
no console, and each unsuppressed spawn beneath them (tailscale, git, clip)
would flash a window.

The convention also says a repo with 3+ call sites factors the ternary into
one helper instead of repeating it. This module is that helper — imported by
``app/webapp/manager.py``, ``app/tray/tray.py``, ``src/static_versioning.py``,
and ``scripts/gen_tailscale_cert.py``. The vendored ``scripts/classify_e2e.py``
keeps its own copy (byte-identical from project-scaffolding; not part of this
repo's own call-site count).

Do not combine with ``DETACHED_PROCESS`` — the two flags are mutually
exclusive. A long-lived child that also needs ``CTRL_BREAK_EVENT`` combines
this with ``CREATE_NEW_PROCESS_GROUP`` instead.

stdlib only.
"""

from __future__ import annotations

import subprocess
import sys

# ``subprocess.CREATE_NO_WINDOW`` is Windows-only; the conditional expression
# evaluates the platform test first, so the attribute is never touched on POSIX.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
