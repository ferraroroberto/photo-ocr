"""Throwaway webapp for the design-review walk's synthetic run.

``/design-review photo-ocr --synthetic`` (and ``measure --e2e``) runs this
(``[design.review.synthetic]`` in ``.fleet.toml``) with the repo's venv python,
from the repo root. It boots a disposable webapp (``scripts/disposable_webapp.py``)
on a free loopback port with a temp runtime-data directory. It prints
``URL=<base>`` once ``/healthz`` answers, then waits until stdin closes and
tears the webapp down.

Never points at the live tray on :8444.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.disposable_webapp import WebappBootError, disposable_webapp  # noqa: E402

logger = logging.getLogger("design_review_synthetic")
STARTUP_TIMEOUT_S = 60.0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        with disposable_webapp(startup_timeout=STARTUP_TIMEOUT_S) as base:
            print(f"URL={base}", flush=True)
            # The walk closes our stdin when it is done; reading to EOF blocks until then.
            sys.stdin.read()
    except WebappBootError as exc:
        logger.error("❌ synthetic webapp: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
