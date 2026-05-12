"""System-tray launcher — owns the webapp + optional Cloudflare tunnel.

Mobile-first design means there's no real desktop UI to surface; the
tray exists so launching `tray.bat` brings the webapp up alongside
Windows login without keeping a console window open.

Menu:
    Open photo OCR             — open the local URL in the default browser
    Copy local URL             — clipboard the local URL
    Copy Cloudflare URL        — clipboard the public URL with ?token=…
    Status                     — popup with hub + webapp state
    --
    Quit                       — stop the webapp and exit
"""

from __future__ import annotations

# Standard library imports
import logging
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from src import AppConfig
from src.webapp_config import append_auth_token, load_webapp_config

from app.webapp.manager import WebappManager, WebappManagerConfig, load_config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"


def _build_icon():
    """Lazy import pystray + Pillow so plain CLI use doesn't drag them in."""
    from PIL import Image
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

    mgr_cfg = load_config(app_config.webapp)
    manager = WebappManager(mgr_cfg)

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

    def open_local(icon, item):  # noqa: ARG001
        webbrowser.open(manager.base_url)

    def copy_local(icon, item):  # noqa: ARG001
        webapp_cfg = load_webapp_config()
        url = append_auth_token(manager.base_url, webapp_cfg.auth_token)
        if _clipboard_copy(url):
            _notify("Copied local URL", url)
        else:
            _notify("Local URL", url)

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

    def show_status(icon, item):  # noqa: ARG001
        s = manager.status()
        _notify(
            "Photo OCR status",
            f"{s.detail} · {s.base_url}",
        )

    def quit_app(icon, item):  # noqa: ARG001
        logger.info("👋 Tray quit requested")
        try:
            manager.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️  stop failed: {exc}")
        icon.stop()

    def on_left_click(icon, item):  # noqa: ARG001
        # Left-click on tray icon opens the webapp.
        webbrowser.open(manager.base_url)

    menu = Menu(
        MenuItem("📷 Open photo OCR", on_left_click, default=True),
        MenuItem("📋 Copy local URL", copy_local),
        MenuItem("📋 Copy Cloudflare URL", copy_tunnel),
        Menu.SEPARATOR,
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
