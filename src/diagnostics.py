"""Runtime diagnostics — log capture and port-owner introspection.

Two pieces, both pure data (no UI imports):

- ``RingLogHandler`` keeps the last N formatted Python logging lines in
  memory so a UI surface can show what the app has been doing without
  parsing files. Attached once to the root logger from ``cli.main``.

- ``port_owner`` answers "who is actually serving on port 8444?" using
  psutil. Useful when the tray is sharing a webapp it didn't spawn.
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, List, Optional

# Third-party imports
try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover — psutil is in requirements.txt
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_RING_CAPACITY = 500


# --------------------------------------------------------------------- logging


class RingLogHandler(logging.Handler):
    """Thread-safe in-memory ring buffer of formatted log lines."""

    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY) -> None:
        super().__init__()
        self._buffer: Deque[str] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001 — logging contract: never raise
            self.handleError(record)
            return
        with self._lock:
            self._buffer.append(line)

    def lines(self) -> List[str]:
        with self._lock:
            return list(self._buffer)


_handler_lock = threading.Lock()
_handler: Optional[RingLogHandler] = None


def app_log_handler() -> RingLogHandler:
    """Return the singleton handler, creating it on first call."""
    global _handler
    with _handler_lock:
        if _handler is None:
            h = RingLogHandler()
            h.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            _handler = h
        return _handler


def attach_app_log_handler() -> None:
    """Idempotently attach the ring handler to the root logger."""
    h = app_log_handler()
    root = logging.getLogger()
    if h not in root.handlers:
        root.addHandler(h)


# ------------------------------------------------------------- port introspection


@dataclass
class PortOwner:
    pid: int
    name: str = ""
    exe: str = ""
    cmdline: List[str] = field(default_factory=list)
    exe_dir_files: List[str] = field(default_factory=list)

    def cmdline_str(self) -> str:
        return " ".join(self.cmdline) if self.cmdline else ""


def port_owner(port: int) -> Optional[PortOwner]:
    """Best-effort lookup of the process LISTENing on ``port``.

    Returns ``None`` when psutil is unavailable, the lookup is denied,
    or no listener is found.
    """
    if psutil is None:
        return None

    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError, OSError) as exc:
        logger.debug(f"port_owner: net_connections denied ({exc})")
        return None

    for conn in connections:
        try:
            if not conn.laddr or conn.laddr.port != port:
                continue
            if conn.status != psutil.CONN_LISTEN:
                continue
            if conn.pid is None:
                continue
        except AttributeError:
            continue

        owner = PortOwner(pid=int(conn.pid))
        try:
            proc = psutil.Process(conn.pid)
            owner.name = proc.name() or ""
            try:
                owner.exe = proc.exe() or ""
            except (psutil.AccessDenied, FileNotFoundError):
                owner.exe = ""
            try:
                owner.cmdline = list(proc.cmdline() or [])
            except (psutil.AccessDenied, FileNotFoundError):
                owner.cmdline = []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        if owner.exe:
            try:
                owner.exe_dir_files = sorted(os.listdir(Path(owner.exe).parent))
            except OSError:
                owner.exe_dir_files = []
        return owner

    return None
