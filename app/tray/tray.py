"""System-tray launcher — owns the webapp + optional Cloudflare tunnel.

Mobile-first design means there's no real desktop UI to surface; the
tray exists so launching `tray.bat` brings the webapp up alongside
Windows login without keeping a console window open.

Menu:
    Open photo OCR             — open the local URL in the default browser
    Copy local URL             — clipboard the local URL
    Copy Tailscale URL         — clipboard https://<tailscale-host>:8444?token=…
    Copy Cloudflare URL        — clipboard the public URL with ?token=…
    Restart webapp             — stop + start so a new pull is picked up
    Status                     — popup with hub + webapp state
    --
    Quit                       — stop the webapp and exit
"""

from __future__ import annotations

# Standard library imports
import datetime
import json
import logging
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from src import AppConfig, cloudflared_runner
from src.no_window import NO_WINDOW
from src.webapp_config import append_auth_token, load_webapp_config

from app.tray.single_instance import SingleInstance
from app.tray.watchdog import HealthWatchdog
from app.webapp.manager import (
    WebappManager,
    cert_paths,
    load_config,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"
TUNNEL_CONFIG_PATH = PROJECT_ROOT / "webapp" / "cloudflared.yml"

# The tray's own logger writes to sys.stderr via
# logging.basicConfig(), which doesn't exist under pythonw — so a boot-time
# failure was previously captured nowhere. This breadcrumb file is the actual
# persistent record of webapp start/retry/watchdog activity.
WATCHDOG_LOG = PROJECT_ROOT / "webapp" / "watchdog.log"

# Backoff between retries of the *initial* webapp spawn at tray boot, so a
# transient race (port not free yet, cert renewal still in flight, hub not
# up yet) doesn't permanently kill the webapp for the tray's lifetime.
STARTUP_RETRY_DELAYS_S = (5.0, 15.0, 30.0)


def _breadcrumb(path: Path, msg: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
    except OSError:
        pass


def _wd_log(msg: str) -> None:
    """Append a breadcrumb to the watchdog log (best-effort)."""
    logger.debug(f"watchdog: {msg}")
    _breadcrumb(WATCHDOG_LOG, msg)


def _retry_with_backoff(fn, delays, on_attempt_failed=None) -> None:
    """Call ``fn()`` until it returns normally or every delay in ``delays``
    has been used. Sleeps ``delays[i]`` seconds after the ``(i+1)``-th
    failure before retrying. Re-raises the last exception once attempts are
    exhausted. ``on_attempt_failed(attempt_number, exc)`` — 1-indexed — fires
    after each failure, before sleeping/raising, so the caller can log."""
    attempt = 0
    while True:
        try:
            fn()
            return
        except Exception as exc:  # noqa: BLE001 — deliberately generic retry
            attempt += 1
            if on_attempt_failed is not None:
                on_attempt_failed(attempt, exc)
            if attempt > len(delays):
                raise
            time.sleep(delays[attempt - 1])


def _build_icon():
    """Lazy import pystray + Pillow so plain CLI use doesn't drag them in."""
    from PIL import Image
    tray_ico = PROJECT_ROOT / "assets" / "tray" / "photo-ocr.ico"
    if tray_ico.exists():
        return Image.open(tray_ico)
    icon_path = (
        PROJECT_ROOT / "app" / "webapp" / "static" / "icon-512.png"
    )
    if icon_path.exists():
        return Image.open(icon_path)
    # Fallback: a tiny solid block.
    return Image.new("RGB", (32, 32), (74, 138, 243))


def _clipboard_copy(text: str) -> bool:
    """Best-effort cross-platform clipboard. Returns True on success."""
    # Windows path first — bundled tooling.
    if sys.platform == "win32":
        try:
            import subprocess
            p = subprocess.run(
                ["clip"],
                input=text,
                text=True,
                check=False,
                encoding="utf-8",
                creationflags=NO_WINDOW,
            )
            return p.returncode == 0
        except OSError as exc:
            logger.debug(f"clip failed: {exc}")
    # POSIX best-effort via xclip / pbcopy if installed.
    return False


def _tailscale_hostname() -> Optional[str]:
    """Return this machine's full tailnet DNS name (``pc.<tailnet>.ts.net``), or
    None if Tailscale is unavailable. The full name is what the ``tailscale cert``
    certificate is issued for; the bare first label fails the TLS name check."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--self=true", "--peers=false", "--json"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        logger.debug(f"tailscale lookup failed: {exc}")
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    self_node = data.get("Self") or {}
    return (self_node.get("DNSName") or "").rstrip(".") or None


def _notify(title: str, message: str) -> None:
    """Show a Windows toast notification when available; log otherwise."""
    logger.info(f"🔔 {title}: {message}")
    if sys.platform != "win32":
        return
    try:
        from winotify import Notification  # type: ignore

        toast = Notification(app_id="Photo OCR", title=title, msg=message)
        toast.show()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"winotify failed: {exc}")


def _start_webapp_in_background(manager: WebappManager) -> dict:
    """Start the webapp on a background thread so the tray comes up quickly
    even if uvicorn takes a second to start. The returned dict's ``exc`` is
    set when every attempt failed."""
    starter_error: dict = {"exc": None}

    def _on_start_attempt_failed(attempt: int, exc: Exception) -> None:
        _wd_log(f"webapp start attempt {attempt} failed: {exc}")
        if attempt <= len(STARTUP_RETRY_DELAYS_S):
            delay = STARTUP_RETRY_DELAYS_S[attempt - 1]
            logger.warning(
                f"⚠️  webapp start attempt {attempt} failed, "
                f"retrying in {delay:.0f}s: {exc}"
            )

    def _start():
        try:
            _retry_with_backoff(
                lambda: manager.start(wait=True),
                STARTUP_RETRY_DELAYS_S,
                _on_start_attempt_failed,
            )
            _notify("Photo OCR webapp ready", manager.base_url)
        except Exception as exc:  # noqa: BLE001
            starter_error["exc"] = exc
            logger.error(
                f"❌ webapp start failed after {len(STARTUP_RETRY_DELAYS_S) + 1} attempts: {exc}"
            )
            _notify("Photo OCR start failed", str(exc))

    threading.Thread(target=_start, daemon=True).start()
    return starter_error


def _start_health_watchdog(
    manager: WebappManager, watchdog_stop: threading.Event
) -> None:
    """Health watchdog: a probe covers both "crashed and not listening" (safe
    to auto-respawn) and "listening but not answering /healthz" (wedged —
    recovery stays manual via the tray menu until the failure mode is
    understood, as app-launcher does for that riskier case; auto-killing a
    stuck process could mask what's actually wrong)."""

    def _on_webapp_wedge(count: int) -> None:
        if manager.is_port_in_use():
            logger.error(
                f"❌ webapp appears wedged ({count} consecutive health-check failures)"
            )
            _wd_log(f"webapp wedged ({count} consecutive failures) — listening but not answering")
            _notify(
                "Photo OCR webapp wedged",
                "Listening but not responding — use Restart webapp",
            )
            return

        logger.warning(
            f"⚠️  webapp not listening ({count} consecutive checks) — respawning"
        )
        _wd_log(f"webapp not listening ({count} consecutive failures) — respawning")
        try:
            manager.start(wait=True)
            _notify("Photo OCR webapp respawned", manager.base_url)
            _wd_log("webapp respawned successfully")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"❌ webapp respawn failed: {exc}")
            _wd_log(f"webapp respawn failed: {exc}")
            _notify("Photo OCR respawn failed", str(exc))
            # Still down — ask the watchdog to try again next interval
            # instead of staying silently alerted forever.
            watchdog.rearm()

    def _on_webapp_recover() -> None:
        logger.info("✅ webapp health recovered")
        _wd_log("webapp health recovered")
        _notify("Photo OCR webapp recovered", manager.base_url)

    watchdog = HealthWatchdog(
        probe=manager.is_reachable,
        on_wedge=_on_webapp_wedge,
        on_recover=_on_webapp_recover,
    )
    threading.Thread(target=watchdog.run, args=(watchdog_stop,), daemon=True).start()


class _Tunnel:
    """Cloudflare named tunnel — spawned alongside uvicorn so a single launch
    (`tray.bat`) brings the public URL up too. The hostname is read from
    webapp/cloudflared.yml; a missing config skips the tunnel."""

    def __init__(self) -> None:
        self.hostname = cloudflared_runner.read_tunnel_hostname(TUNNEL_CONFIG_PATH)
        self.proc = None

    def start_in_background(self) -> None:
        if self.hostname is not None:
            threading.Thread(target=self._start, daemon=True).start()

    def _start(self) -> None:
        """Spawn cloudflared and persist the public URL.

        Best-effort: a missing binary or failed launch is logged + toasted
        but doesn't take the tray down.
        """
        try:
            proc = cloudflared_runner.spawn_cloudflared(
                TUNNEL_CONFIG_PATH,
                PROJECT_ROOT,
                capture_output=False,
                new_process_group=True,
            )
        except cloudflared_runner.CloudflaredNotFound:
            logger.warning(
                "⚠️  cloudflared not on PATH — public URL won't be reachable. "
                "Install: winget install Cloudflare.cloudflared"
            )
            _notify(
                "Cloudflare tunnel",
                "cloudflared not on PATH — install via winget",
            )
            return
        except OSError as exc:
            logger.warning(f"⚠️  cloudflared failed to launch: {exc}")
            _notify("Cloudflare tunnel", f"Failed to start: {exc}")
            return
        self.proc = proc
        logger.info(
            f"🌍 Cloudflare tunnel started → https://{self.hostname} "
            f"(pid={proc.pid})"
        )

        # Persist the URL so the "Copy Cloudflare URL" menu item finds it.
        token = cloudflared_runner.read_auth_token()
        url = cloudflared_runner.build_tunnel_url(self.hostname, token)
        cloudflared_runner.write_tunnel_url(TUNNEL_URL_FILE, url)

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        cloudflared_runner.stop_process(proc, "cloudflared")
        cloudflared_runner.remove_tunnel_url(TUNNEL_URL_FILE)


def _notify_copied(url: str, copied_title: str, plain_title: str) -> None:
    """Copy ``url`` and toast; when the clipboard is unavailable the toast
    shows the URL itself."""
    if _clipboard_copy(url):
        _notify(copied_title, url)
    else:
        _notify(plain_title, url)


def _copy_local_url(manager: WebappManager) -> None:
    url = append_auth_token(manager.base_url, load_webapp_config().auth_token)
    _notify_copied(url, "Copied local URL", "Local URL")


def _copy_tailscale_url(manager: WebappManager) -> None:
    host = _tailscale_hostname()
    if not host:
        _notify(
            "Tailscale not available",
            "Couldn't resolve a tailnet hostname (is `tailscale` installed and logged in?).",
        )
        return
    scheme = "https" if cert_paths() else "http"
    url = f"{scheme}://{host}:{manager.config.port}"
    url = append_auth_token(url, load_webapp_config().auth_token)
    _notify_copied(url, "Copied Tailscale URL", "Tailscale URL")


def _copy_tunnel_url() -> None:
    if not TUNNEL_URL_FILE.exists():
        _notify(
            "No tunnel URL yet",
            "Run webapp_tunnel_named.bat to bring up the Cloudflare tunnel.",
        )
        return
    try:
        url = TUNNEL_URL_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _notify("Tunnel URL read failed", str(exc))
        return
    if not url:
        _notify("Tunnel URL is empty", str(TUNNEL_URL_FILE))
        return
    _notify_copied(url, "Copied Cloudflare URL", "Cloudflare URL")


def _restart_webapp_in_background(manager: WebappManager) -> None:
    def _do_restart():
        try:
            _notify("Photo OCR", "Restarting webapp…")
            manager.restart(wait=True)
            _notify("Photo OCR webapp restarted", manager.base_url)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"❌ webapp restart failed: {exc}")
            _notify("Restart failed", str(exc))

    threading.Thread(target=_do_restart, daemon=True).start()


def _show_status(manager: WebappManager) -> None:
    s = manager.status()
    _notify(
        "Photo OCR status",
        f"{s.detail} · {s.base_url}",
    )


def run_tray(app_config: AppConfig) -> int:
    """Run the tray icon. Returns when the user picks Quit."""
    try:
        import pystray  # type: ignore
        from pystray import Menu, MenuItem
    except ImportError as exc:
        logger.error(
            f"❌ pystray not installed ({exc}); install via `pip install -r requirements.txt`"
        )
        return 1

    # In-process single-instance guard: the tray.bat CIM
    # pre-check can let two near-simultaneous launches through, so the guarantee
    # must live in the process. Held for the tray's lifetime; the OS frees the
    # named mutex on exit. `instance` is intentionally kept referenced (quit).
    instance = SingleInstance(r"Global\photo-ocr-tray")
    if not instance.acquired:
        logger.info("ℹ️  Another photo-ocr tray is already running; exiting.")
        return 0

    mgr_cfg = load_config(app_config.webapp)
    manager = WebappManager(mgr_cfg)

    tunnel = _Tunnel()
    starter_error = _start_webapp_in_background(manager)
    watchdog_stop = threading.Event()
    _start_health_watchdog(manager, watchdog_stop)
    tunnel.start_in_background()

    def quit_app(icon, item):  # noqa: ARG001
        logger.info("👋 Tray quit requested")
        watchdog_stop.set()
        # Stop cloudflared first so the public URL 5xx's immediately
        # while the webapp shutdown runs.
        tunnel.stop()
        try:
            manager.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️  stop failed: {exc}")
        instance.release()
        icon.stop()

    # Left-click on the tray icon opens the webapp.
    menu = Menu(
        MenuItem("📷 Open photo OCR", lambda icon, item: webbrowser.open(manager.base_url), default=True),
        MenuItem("📋 Copy local URL", lambda icon, item: _copy_local_url(manager)),
        MenuItem("📋 Copy Tailscale URL", lambda icon, item: _copy_tailscale_url(manager)),
        MenuItem("📋 Copy Cloudflare URL", lambda icon, item: _copy_tunnel_url()),
        Menu.SEPARATOR,
        MenuItem("🔄 Restart webapp", lambda icon, item: _restart_webapp_in_background(manager)),
        MenuItem("ℹ️ Status", lambda icon, item: _show_status(manager)),
        Menu.SEPARATOR,
        MenuItem("🚪 Quit", quit_app),
    )

    icon = pystray.Icon(
        "photo-ocr",
        icon=_build_icon(),
        title="Photo OCR",
        menu=menu,
    )
    icon.run()
    if starter_error["exc"] is not None:
        return 1
    return 0
