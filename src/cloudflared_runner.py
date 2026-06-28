"""Shared cloudflared named-tunnel lifecycle.

Two surfaces drive the same cloudflared named tunnel:

* the **tray** (``app/tray/tray.py``) spawns it on a daemon thread as part
  of normal startup, and
* the **headless script** (``scripts/run_named_tunnel.py``) runs it in the
  foreground for no-tray use.

Both read the public hostname from ``webapp/cloudflared.yml``, build the
public URL (with ``?token=…`` appended when an ``auth_token`` is set), spawn
``cloudflared tunnel … run``, persist the URL to ``last_tunnel_url.txt``, and
tear the process down on exit. That shared lifecycle lives here so a
hostname-format or token-append change is made in exactly one place. Each
surface keeps its own concurrency wrapper — the tray's background thread, the
script's foreground wait loop — and decides for itself how fatal a missing
binary is (the script aborts; the tray degrades to "no public URL").

Nothing here imports a UI framework, per the ``src/`` ↔ ``app/`` split.
"""

from __future__ import annotations

# Standard library imports
import logging
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Third-party imports
import yaml

# Local imports
from src.webapp_config import append_auth_token, load_webapp_config

logger = logging.getLogger(__name__)


class CloudflaredNotFound(RuntimeError):
    """Raised by :func:`spawn_cloudflared` when no ``cloudflared`` is on PATH.

    Callers decide whether that is fatal: the headless script aborts, the
    tray logs a warning and carries on without a public URL.
    """


def read_tunnel_hostname(config_path: Path) -> Optional[str]:
    """Return the first ``ingress[].hostname`` in the cloudflared config.

    Returns ``None`` when the file is missing or unparseable — both callers
    treat either case as "no tunnel".
    """
    if not config_path.exists():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"⚠️  Could not parse {config_path}: {exc}")
        return None
    for entry in data.get("ingress") or []:
        if isinstance(entry, dict) and entry.get("hostname"):
            return str(entry["hostname"]).strip()
    return None


def read_auth_token() -> str:
    """Return the configured webapp ``auth_token``, or ``""`` if unavailable.

    Best-effort: any failure to load the webapp config is swallowed so a
    missing/corrupt config never blocks bringing the tunnel up — it just
    means the persisted URL carries no ``?token=…``.
    """
    try:
        return (load_webapp_config().auth_token or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"could not read auth_token: {exc}")
        return ""


def build_tunnel_url(hostname: str, token: str) -> str:
    """Build ``https://<hostname>`` with ``?token=…`` appended when set.

    Pure function — no I/O — so it is trivially unit-testable.
    """
    url = f"https://{hostname}"
    if token:
        url = append_auth_token(url, token)
    return url


def write_tunnel_url(url_file: Path, url: str) -> None:
    """Persist ``url`` to ``url_file`` (creating parents). Warn on OS error."""
    try:
        url_file.parent.mkdir(parents=True, exist_ok=True)
        url_file.write_text(url + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning(f"⚠️  Could not write {url_file}: {exc}")


def remove_tunnel_url(url_file: Path) -> None:
    """Delete ``url_file`` if present; ignore the case where it is gone."""
    try:
        if url_file.exists():
            url_file.unlink()
    except OSError:
        pass


def spawn_cloudflared(
    config_path: Path,
    cwd: Path,
    *,
    capture_output: bool,
    new_process_group: bool,
) -> subprocess.Popen:
    """Spawn ``cloudflared tunnel --config <config_path> run``.

    Raises :class:`CloudflaredNotFound` when the binary is not on PATH.

    ``capture_output`` / ``new_process_group`` capture the two callers'
    deliberately different spawn shapes:

    * the **script** wants ``capture_output=True`` (pipe + stream stdout to
      its console) and ``new_process_group=False`` (it inherits the console);
    * the **tray** wants ``capture_output=False`` (discard output — it runs
      windowless under ``pythonw``) and ``new_process_group=True`` (own group
      + no console window, so the ``CTRL_BREAK_EVENT`` teardown is scoped to
      the child).
    """
    bin_path = shutil.which("cloudflared")
    if bin_path is None:
        raise CloudflaredNotFound(
            "cloudflared not found on PATH. Install: "
            "winget install Cloudflare.cloudflared"
        )
    cmd = [bin_path, "tunnel", "--config", str(config_path), "run"]
    logger.info(f"🌐 Starting cloudflared: {' '.join(cmd)}")
    kw: dict = dict(cwd=str(cwd))
    if capture_output:
        kw.update(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    else:
        kw.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == "win32" and new_process_group:
        kw["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kw)


def stop_process(proc: subprocess.Popen, name: str) -> None:
    """Stop ``proc``: CTRL_BREAK (Windows) → terminate → kill after 5 s.

    Generic, idempotent-ish process teardown shared by the tunnel and (in
    the script) the uvicorn child. Best-effort; any failure is logged at
    debug, never raised.
    """
    try:
        logger.info(f"🛑 Stopping {name} (pid={proc.pid})")
        if sys.platform == "win32":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"{name} stop failed: {exc}")
