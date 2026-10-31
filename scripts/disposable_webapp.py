"""Boot a throwaway webapp on a free loopback port, for tests and design review.

``tests/e2e/conftest.py`` (the autoboot fixture) and
``scripts/design_review_synthetic.py`` both need a webapp that is not the live
tray: a free port, a temp runtime-data directory, a ``/healthz`` wait and a clean
stop. They share this one implementation; the uvicorn argv comes from
``WebappManager._build_command`` so the bind, loop factory and cert flags stay in
one place.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator, Optional

import requests
import urllib3

from app.webapp.manager import PROJECT_ROOT, WebappManager, cert_paths
from src.archive import ARCHIVE_DIR_ENV

logger = logging.getLogger(__name__)

# The cert is for the .ts.net name, not loopback: silence the urllib3 noise.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Short so a fast boot is not padded with dead time on every gate run; the
# caller owns the overall deadline.
HEALTHZ_POLL_INTERVAL_S = 0.1
_LOG_TAIL_LINES = 20


class WebappBootError(RuntimeError):
    """The disposable webapp exited early or never answered ``/healthz``."""


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_healthz(
    base: str, timeout: float, proc: Optional[subprocess.Popen] = None
) -> bool:
    """Poll ``/healthz`` until it answers 200, ``timeout`` passes or ``proc`` exits."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            if requests.get(f"{base}/healthz", timeout=2, verify=False).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(HEALTHZ_POLL_INTERVAL_S)
    return False


def terminate(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            try:
                # Own process group (see _spawn), so CTRL_BREAK reaches only it.
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception as exc:  # best effort
                logger.debug("CTRL_BREAK_EVENT failed: %s", exc)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception as exc:  # best effort
        logger.warning("⚠️  disposable webapp teardown failed: %s", exc)


def _spawn(cmd: list[str], log: IO[str], data_dir: Path) -> subprocess.Popen:
    kwargs: dict = dict(
        cwd=str(PROJECT_ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            # Usage events and the SQLite files go here, not into the
            # developer's real runtime-data directory.
            "PHOTO_OCR_DATA_DIR": str(data_dir),
            # The session archive too, or the boot prune would run on the live one.
            ARCHIVE_DIR_ENV: str(data_dir / "archive"),
        },
    )
    if sys.platform == "win32":
        # New process group so CTRL_BREAK reaches it for a clean stop; no
        # window so the run does not flash consoles.
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kwargs)


def _log_tail(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no log)"
    return "\n".join(lines[-_LOG_TAIL_LINES:]) or "(empty log)"


@contextmanager
def disposable_webapp(
    *, startup_timeout: float, log_path: Optional[Path] = None
) -> Iterator[str]:
    """Run a webapp on a free loopback port; yield its base URL, stop it on exit.

    Raises ``WebappBootError`` (with the log tail) if it exits early or does not
    answer ``/healthz`` within ``startup_timeout``. The temp data directory is
    removed on exit; ``log_path`` defaults to a file inside it.
    """
    data_dir = Path(tempfile.mkdtemp(prefix="photo-ocr-disposable-"))
    log_path = log_path or data_dir / "webapp.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc: Optional[subprocess.Popen] = None
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            try:
                # A free port, never the hardcoded 8444: the live tray may hold it.
                port = free_tcp_port()
                scheme = "https" if cert_paths() else "http"
                base = f"{scheme}://127.0.0.1:{port}"
                cmd = WebappManager()._build_command(host="127.0.0.1", port=port)
                proc = _spawn(cmd, log, data_dir)
                if not wait_healthz(base, startup_timeout, proc):
                    reason = "exited early" if proc.poll() is not None else (
                        f"did not answer /healthz within {startup_timeout:g}s"
                    )
                    log.flush()
                    raise WebappBootError(
                        f"disposable webapp {reason} at {base}; log {log_path}:\n"
                        f"{_log_tail(log_path)}"
                    )
                logger.info("✅ disposable webapp ready at %s", base)
                yield base
            finally:
                terminate(proc)
    finally:
        # After the log handle closes: Windows cannot delete an open file.
        shutil.rmtree(data_dir, ignore_errors=True)
