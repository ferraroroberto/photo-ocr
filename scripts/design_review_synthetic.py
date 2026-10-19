"""Throwaway webapp for the design-review walk's synthetic run (fleet-config#1383).

``/design-review photo-ocr --synthetic`` (and ``measure --e2e``) runs this
(``[design.review.synthetic]`` in ``.fleet.toml``) with the repo's venv python,
from the repo root. It boots a disposable webapp on a free loopback port with
``PHOTO_OCR_DATA_DIR`` pointed at a temp directory, so History is empty and
nothing touches the live archive or its usage log. It prints ``URL=<base>`` once
``/healthz`` answers, then waits until stdin closes and tears the webapp down.

Never points at the live tray on :8444.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.webapp.event_loop import LOOP_FACTORY  # noqa: E402
from app.webapp.manager import cert_paths  # noqa: E402
from src.no_window import NEW_GROUP_NO_WINDOW  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("design_review_synthetic")
STARTUP_TIMEOUT_S = 60.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _healthy(base: str) -> bool:
    try:
        return requests.get(f"{base}/healthz", timeout=2, verify=False).status_code == 200
    except requests.RequestException:
        return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    port = _free_port()
    certs = cert_paths()
    base = f"{'https' if certs else 'http'}://127.0.0.1:{port}"
    data_dir = Path(tempfile.mkdtemp(prefix="photo-ocr-synthetic-"))
    cmd = [
        sys.executable, "-m", "uvicorn", "app.webapp.server:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--log-level", "warning", "--loop", LOOP_FACTORY,
    ]
    if certs:
        cert, key = certs
        cmd += ["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)]
    log_fp = (data_dir / "webapp.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=log_fp, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1", "PHOTO_OCR_DATA_DIR": str(data_dir)},
        creationflags=NEW_GROUP_NO_WINDOW,
    )
    try:
        deadline = time.time() + STARTUP_TIMEOUT_S
        while not _healthy(base):
            if proc.poll() is not None:
                logger.error("❌ synthetic webapp exited early; log: %s", data_dir / "webapp.log")
                return 1
            if time.time() > deadline:
                logger.error("❌ synthetic webapp not reachable after %ss", STARTUP_TIMEOUT_S)
                return 1
            time.sleep(0.1)
        logger.info("✅ synthetic webapp up on %s (data %s)", base, data_dir)
        print(f"URL={base}", flush=True)
        # The walk closes our stdin when it is done; reading to EOF blocks until then.
        sys.stdin.read()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fp.close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
