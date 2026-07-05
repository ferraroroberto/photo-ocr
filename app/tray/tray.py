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
import json
import logging
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from src import AppConfig, cloudflared_runner
from src.webapp_config import append_auth_token, load_webapp_config

from app.tray.single_instance import SingleInstance
from app.webapp.manager import (
    WebappManager,
    WebappManagerConfig,
    cert_paths,
    load_config,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"
TUNNEL_CONFIG_PATH = PROJECT_ROOT / "webapp" / "cloudflared.yml"


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
            )
            return p.returncode == 0
        except OSError as exc:
            logger.debug(f"clip failed: {exc}")
    # POSIX best-effort via xclip / pbcopy if installed.
    return False


def _tailscale_hostname() -> Optional[str]:
    """Return the tailnet hostname for this machine, or None if Tailscale is unavailable."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--self=true", "--peers=false", "--json"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
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
    dns = (self_node.get("DNSName") or "").rstrip(".")
    if not dns:
        return None
    short = dns.split(".")[0]
    return short or dns


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

    # In-process single-instance guard (project-scaffolding#39): the tray.bat CIM
    # pre-check can let two near-simultaneous launches through, so the guarantee
    # must live in the process. Held for the tray's lifetime; the OS frees the
    # named mutex on exit. `instance` is intentionally kept referenced (quit).
    instance = SingleInstance(r"Global\photo-ocr-tray")
    if not instance.acquired:
        logger.info("ℹ️  Another photo-ocr tray is already running; exiting.")
        return 0

    mgr_cfg = load_config(app_config.webapp)
    manager = WebappManager(mgr_cfg)

    # Cloudflare named tunnel — spawned alongside uvicorn so a single
    # launch (`tray.bat`) brings the public URL up too. Hostname is read
    # from webapp/cloudflared.yml; missing config skips the tunnel.
    tunnel_hostname = cloudflared_runner.read_tunnel_hostname(TUNNEL_CONFIG_PATH)
    tunnel_state: dict = {"proc": None}

    # Kick off the webapp on a background thread so the tray comes up
    # quickly even if uvicorn takes a second to start.
    starter_error: dict = {"exc": None}

    def _start():
        try:
            manager.start(wait=True)
            _notify(
                "Photo OCR webapp ready",
                manager.base_url,
            )
        except Exception as exc:  # noqa: BLE001
            starter_error["exc"] = exc
            logger.error(f"❌ webapp start failed: {exc}")
            _notify("Photo OCR start failed", str(exc))

    threading.Thread(target=_start, daemon=True).start()

    def _start_tunnel():
        """Spawn cloudflared and persist the public URL.

        Best-effort: a missing binary or failed launch is logged + toasted
        but doesn't take the tray down.
        """
        if tunnel_hostname is None:
            return
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
        tunnel_state["proc"] = proc
        logger.info(
            f"🌍 Cloudflare tunnel started → https://{tunnel_hostname} "
            f"(pid={proc.pid})"
        )

        # Persist the URL so the "Copy Cloudflare URL" menu item finds it.
        token = cloudflared_runner.read_auth_token()
        url = cloudflared_runner.build_tunnel_url(tunnel_hostname, token)
        cloudflared_runner.write_tunnel_url(TUNNEL_URL_FILE, url)

    def _stop_tunnel():
        proc = tunnel_state.get("proc")
        tunnel_state["proc"] = None
        if proc is None:
            return
        cloudflared_runner.stop_process(proc, "cloudflared")
        cloudflared_runner.remove_tunnel_url(TUNNEL_URL_FILE)

    if tunnel_hostname is not None:
        threading.Thread(target=_start_tunnel, daemon=True).start()

    def open_local(icon, item):  # noqa: ARG001
        webbrowser.open(manager.base_url)

    def copy_local(icon, item):  # noqa: ARG001
        webapp_cfg = load_webapp_config()
        url = append_auth_token(manager.base_url, webapp_cfg.auth_token)
        if _clipboard_copy(url):
            _notify("Copied local URL", url)
        else:
            _notify("Local URL", url)

    def copy_tailscale(icon, item):  # noqa: ARG001
        host = _tailscale_hostname()
        if not host:
            _notify(
                "Tailscale not available",
                "Couldn't resolve a tailnet hostname (is `tailscale` installed and logged in?).",
            )
            return
        scheme = "https" if cert_paths() else "http"
        url = f"{scheme}://{host}:{manager.config.port}"
        webapp_cfg = load_webapp_config()
        url = append_auth_token(url, webapp_cfg.auth_token)
        if _clipboard_copy(url):
            _notify("Copied Tailscale URL", url)
        else:
            _notify("Tailscale URL", url)

    def copy_tunnel(icon, item):  # noqa: ARG001
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
        if _clipboard_copy(url):
            _notify("Copied Cloudflare URL", url)
        else:
            _notify("Cloudflare URL", url)

    def restart_webapp(icon, item):  # noqa: ARG001
        def _do_restart():
            try:
                _notify("Photo OCR", "Restarting webapp…")
                manager.restart(wait=True)
                _notify("Photo OCR webapp restarted", manager.base_url)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"❌ webapp restart failed: {exc}")
                _notify("Restart failed", str(exc))

        threading.Thread(target=_do_restart, daemon=True).start()

    def show_status(icon, item):  # noqa: ARG001
        s = manager.status()
        _notify(
            "Photo OCR status",
            f"{s.detail} · {s.base_url}",
        )

    def quit_app(icon, item):  # noqa: ARG001
        logger.info("👋 Tray quit requested")
        # Stop cloudflared first so the public URL 5xx's immediately
        # while the webapp shutdown runs.
        _stop_tunnel()
        try:
            manager.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️  stop failed: {exc}")
        instance.release()
        icon.stop()

    def on_left_click(icon, item):  # noqa: ARG001
        # Left-click on tray icon opens the webapp.
        webbrowser.open(manager.base_url)

    menu = Menu(
        MenuItem("📷 Open photo OCR", on_left_click, default=True),
        MenuItem("📋 Copy local URL", copy_local),
        MenuItem("📋 Copy Tailscale URL", copy_tailscale),
        MenuItem("📋 Copy Cloudflare URL", copy_tunnel),
        Menu.SEPARATOR,
        MenuItem("🔄 Restart webapp", restart_webapp),
        MenuItem("ℹ️ Status", show_status),
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
