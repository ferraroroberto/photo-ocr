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
    Status                     — popup with webapp state
    --
    Quit                       — stop the webapp and exit
"""

from __future__ import annotations

# Standard library imports
import json
import logging
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from app.tray.single_instance import SingleInstance
from app.tray.watchdog import (
    DEFAULT_STARTUP_RETRY_DELAYS_S,
    BreadcrumbLog,
    HealthWatchdog,
    RespawnGuard,
    RespawnOutcome,
    retry_with_backoff,
)
from app.webapp.manager import (
    WebappManager,
    cert_paths,
    load_config,
)
from src import AppConfig, cloudflared_runner
from src.no_window import NO_WINDOW
from src.webapp_config import append_auth_token, load_webapp_config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"
TUNNEL_CONFIG_PATH = PROJECT_ROOT / "webapp" / "cloudflared.yml"

# The tray's own logger writes to sys.stderr via
# logging.basicConfig(), which doesn't exist under pythonw — so a boot-time
# failure would be captured nowhere. This breadcrumb file is the actual
# persistent record of webapp start/retry/watchdog activity.
WATCHDOG_LOG = PROJECT_ROOT / "webapp" / "watchdog.log"

# `clip` copies a short URL and answers in well under a second, so a child still
# alive after 10 s is wedged and must not stall the tray menu thread.
CLIP_TIMEOUT_S = 10


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
                timeout=CLIP_TIMEOUT_S,
            )
            return p.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning(f"clip did not finish within {CLIP_TIMEOUT_S}s; clipboard not set")
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


class _NothingSpawned(Exception):
    """``manager.start`` returned without launching a child: the webapp is
    disabled in config, or an external copy already holds the port and was adopted."""


def _spawn_webapp(manager: WebappManager) -> int:
    """Launch the webapp and return its pid without waiting for readiness.

    Waiting would raise on a readiness timeout with the child still running and
    lose its pid; readiness is the watchdog probe's job.
    """
    manager.start(wait=False)
    pid = manager.spawned_pid
    if pid is None:
        raise _NothingSpawned("no child spawned (webapp disabled or external copy adopted)")
    return pid


def _respawn(manager: WebappManager, guard: RespawnGuard) -> Optional[RespawnOutcome]:
    """Spawn through the guard so a second copy never stacks on a live first one.

    Returns None when nothing was spawned by design (see :class:`_NothingSpawned`).
    """
    try:
        return guard.respawn(lambda: _spawn_webapp(manager))
    except _NothingSpawned:
        return None


def _start_webapp_in_background(
    manager: WebappManager, guard: RespawnGuard, wd_log: BreadcrumbLog
) -> dict:
    """Start the webapp on a background thread so the tray comes up quickly
    even if uvicorn takes a second to start. The returned dict's ``exc`` is
    set when every attempt failed."""
    starter_error: dict = {"exc": None}

    def _on_start_attempt_failed(attempt: int, exc: BaseException) -> None:
        wd_log(f"webapp start attempt {attempt} failed: {exc}")
        if attempt <= len(DEFAULT_STARTUP_RETRY_DELAYS_S):
            delay = DEFAULT_STARTUP_RETRY_DELAYS_S[attempt - 1]
            logger.warning(
                f"⚠️  webapp start attempt {attempt} failed, "
                f"retrying in {delay:.0f}s: {exc}"
            )

    def _start():
        try:
            retry_with_backoff(
                lambda: _respawn(manager, guard),
                DEFAULT_STARTUP_RETRY_DELAYS_S,
                _on_start_attempt_failed,
            )
            wd_log("webapp start requested")
        except Exception as exc:  # noqa: BLE001
            starter_error["exc"] = exc
            wd_log(f"webapp start FAILED permanently: {exc}")
            logger.error(
                f"❌ webapp start failed after "
                f"{len(DEFAULT_STARTUP_RETRY_DELAYS_S) + 1} attempts: {exc}"
            )
            _notify("Photo OCR start failed", str(exc))

    threading.Thread(target=_start, daemon=True).start()
    return starter_error


def _handle_wedge(
    count: int,
    manager: WebappManager,
    guard: RespawnGuard,
    wd_log: BreadcrumbLog,
    rearm: Callable[[], None],
) -> None:
    """Act on ``count`` consecutive failed health probes.

    Starting (our own child still inside its startup window): back off, never
    spawn or alert. Listening but not answering: alert only, recovery stays
    manual via the tray menu, since auto-killing a stuck process could mask
    what's actually wrong. Not listening: respawn through the guard, which
    stops the child we spawned last first. Either way ``rearm`` asks the
    watchdog to re-evaluate next interval instead of waiting on a recovery a
    dead process never produces.
    """
    if guard.is_starting():
        logger.info(f"ℹ️  webapp pid {guard.pid} still starting ({count} failed probes)")
        wd_log(f"webapp pid {guard.pid} still starting ({count} failed probes)")
        rearm()
        return

    if manager.is_port_in_use():
        logger.error(
            f"❌ webapp appears wedged ({count} consecutive health-check failures)"
        )
        wd_log(f"webapp wedged ({count} consecutive failures) — listening but not answering")
        _notify(
            "Photo OCR webapp wedged",
            "Listening but not responding — use Restart webapp",
        )
        return

    logger.warning(
        f"⚠️  webapp not listening ({count} consecutive checks) — respawning"
    )
    wd_log(f"webapp not listening ({count} consecutive failures) — respawning")
    try:
        _respawn(manager, guard)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"❌ webapp respawn failed: {exc}")
        wd_log(f"webapp respawn failed: {exc}")
        _notify("Photo OCR respawn failed", str(exc))
    rearm()


def _start_health_watchdog(
    manager: WebappManager,
    guard: RespawnGuard,
    wd_log: BreadcrumbLog,
    watchdog_stop: threading.Event,
) -> None:
    """Health watchdog: a probe covers "starting", "crashed and not listening"
    (safe to auto-respawn) and "listening but not answering /healthz" (wedged);
    :func:`_handle_wedge` owns the response to each."""

    def _on_webapp_wedge(count: int) -> None:
        _handle_wedge(count, manager, guard, wd_log, watchdog.rearm)

    def _on_webapp_recover() -> None:
        guard.mark_healthy()
        logger.info("✅ webapp health recovered")
        wd_log("webapp health recovered")
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
            "Run scripts/webapp_tunnel_named.bat to bring up the Cloudflare tunnel.",
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


def _restart_webapp_in_background(manager: WebappManager, guard: RespawnGuard) -> None:
    def _do_restart():
        # A booting child is not "running" to the manager, so a restart now would
        # spawn a second copy beside it instead of replacing it.
        if guard.is_starting():
            _notify("Photo OCR", "Webapp is still starting — try again shortly")
            return
        try:
            _notify("Photo OCR", "Restarting webapp…")
            manager.restart(wait=True)
            _notify("Photo OCR webapp restarted", manager.base_url)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"❌ webapp restart failed: {exc}")
            _notify("Restart failed", str(exc))
        finally:
            # The guard owns the one child: report the spawn made here, even a
            # restart whose readiness wait timed out with the child still alive.
            pid = manager.spawned_pid
            if pid is not None:
                guard.track(pid)

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
    wd_log = BreadcrumbLog(WATCHDOG_LOG)
    guard = RespawnGuard(wd_log)
    starter_error = _start_webapp_in_background(manager, guard, wd_log)
    watchdog_stop = threading.Event()
    _start_health_watchdog(manager, guard, wd_log, watchdog_stop)
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
        MenuItem("🔄 Restart webapp", lambda icon, item: _restart_webapp_in_background(manager, guard)),
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
