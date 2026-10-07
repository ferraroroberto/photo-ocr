"""Generate PWA/tray/Stream-Deck icons from the shared fleet icon-brand generator.

Thin caller onto ``project-scaffolding``'s ``brand_gen.render_set()`` — the
master art is photo-ocr's vendored Lucide ``camera.svg``, not a bespoke
Pillow-drawn silhouette (app-launcher#65: a coherent icon family across the
fleet).

Writes into ``app/webapp/static/``: ``icon-512.png``, ``icon-512-maskable.png``,
``icon-180.png``, ``icon-192.png``, ``favicon.ico``. Into ``assets/tray/``:
``photo-ocr.ico``. Into ``assets/stream-deck/``: ``photo-ocr-144.png``.

The generated icons are committed, so this is a dev-only regen, not part of
first-time ``setup.bat``. It needs a sibling ``project-scaffolding`` checkout:
defaults to ``PROJECT_SCAFFOLDING_ROOT`` env var, else the usual fleet layout
(``<this repo>/../project-scaffolding``); set the env var when the fleet
isn't laid out that way on this machine.

Usage:
    & .\\.venv\\Scripts\\python.exe scripts\\gen_icons.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAFFOLDING_ROOT = Path(
    os.environ.get("PROJECT_SCAFFOLDING_ROOT")
    or str(PROJECT_ROOT.parent / "project-scaffolding")
)
SCAFFOLDING_SCRIPTS = SCAFFOLDING_ROOT / "scripts"
SCAFFOLDING_MASTER = SCAFFOLDING_ROOT / "brand" / "camera.svg"

if not SCAFFOLDING_SCRIPTS.is_dir():
    raise SystemExit(
        f"project-scaffolding not found at {SCAFFOLDING_ROOT!s} — icons are "
        "already committed under app/webapp/static/, so this script is only "
        "needed for a deliberate icon refresh. Clone project-scaffolding "
        "alongside this repo, or set PROJECT_SCAFFOLDING_ROOT to its path."
    )

sys.path.insert(0, str(SCAFFOLDING_SCRIPTS))

from brand_gen import render_set  # noqa: E402

STATIC_DIR = PROJECT_ROOT / "app" / "webapp" / "static"


def main() -> None:
    render_set(
        master=SCAFFOLDING_MASTER,
        out_dir=STATIC_DIR,
        tray_out_dir=PROJECT_ROOT / "assets" / "tray",
        stream_deck_out_dir=PROJECT_ROOT / "assets" / "stream-deck",
        project_slug="photo-ocr",
    )
    print(f"wrote icons to {STATIC_DIR}")


if __name__ == "__main__":
    main()
