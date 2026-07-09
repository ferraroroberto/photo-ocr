"""Start uvicorn + cloudflared on a named (persistent) tunnel.

Used by `webapp_tunnel_named.bat` for headless / no-tray use. The
tray already does this same work as part of normal startup — only
reach for this script when running without the tray.

Boots:

  1. uvicorn (HTTPS if `webapp/certificates/cert.pem` exists)
  2. cloudflared tunnel --config webapp/cloudflared.yml run

The persistent URL is written to `webapp/last_tunnel_url.txt` (with
`?token=…` appended when an `auth_token` is configured).
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# This script lives in scripts/, so a plain `python scripts/run_named_tunnel.py`
# puts scripts/ — not the repo root — on sys.path[0], and `import src` fails.
# Put the repo root first so the shared src/ helpers resolve under file
# invocation (the tray gets this for free via launcher.py at the root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Local imports (root on sys.path per the bootstrap above)
from app.webapp.event_loop import LOOP_FACTORY  # noqa: E402
from src import cloudflared_runner  # noqa: E402

logger = logging.getLogger("run_named_tunnel")
DEFAULT_CONFIG = PROJECT_ROOT / "webapp" / "cloudflared.yml"
SAMPLE_CONFIG = PROJECT_ROOT / "webapp" / "cloudflared.sample.yml"
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"
DEFAULT_PORT = 8444


def _have_listener(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_python() -> Path:
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return venv_py
    return Path(sys.executable)


def _spawn_uvicorn(port: int) -> subprocess.Popen:
    cert = PROJECT_ROOT / "webapp" / "certificates" / "cert.pem"
    key = PROJECT_ROOT / "webapp" / "certificates" / "key.pem"
    cmd = [
        str(_find_python()),
        "-m",
        "uvicorn",
        "app.webapp.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--loop",
        LOOP_FACTORY,
    ]
    if cert.exists() and key.exists():
        cmd.extend(["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)])
    logger.info(f"🚀 Starting uvicorn: {' '.join(cmd)}")
    kw: dict = dict(cwd=str(PROJECT_ROOT))
    if sys.platform == "win32":
        kw["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kw)


def _wait_for_uvicorn(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _have_listener(port):
            return True
        time.sleep(0.3)
    return False


def _persist_tunnel_url(hostname: str) -> None:
    token = cloudflared_runner.read_auth_token()
    url = cloudflared_runner.build_tunnel_url(hostname, token)
    cloudflared_runner.write_tunnel_url(TUNNEL_URL_FILE, url)
    logger.info(f"📡 Tunnel URL → {TUNNEL_URL_FILE}")
    logger.info(f"   {url}")
    if token:
        logger.info(
            "🔐 auth_token is set — the URL above includes ?token=… so "
            "the phone bootstraps on first load."
        )


def _stream(proc: subprocess.Popen) -> None:
    for line in proc.stdout or ():
        sys.stdout.write(line)
        sys.stdout.flush()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(
        os.environ.get("CLOUDFLARED_CONFIG", str(DEFAULT_CONFIG))
    )
    if not config_path.exists():
        logger.error(
            f"❌ {config_path} missing. Copy {SAMPLE_CONFIG.name} to "
            f"{config_path.name} and fill in your tunnel UUID + hostname."
        )
        return 1

    hostname = cloudflared_runner.read_tunnel_hostname(config_path)
    if hostname:
        logger.info(f"🌍 Public hostname: https://{hostname}")
    else:
        logger.warning(
            "⚠️  No hostname found in ingress[] — tunnel will still run "
            "but last_tunnel_url.txt won't be updated."
        )

    port = int(os.environ.get("WEBAPP_PORT", DEFAULT_PORT))
    uvicorn_proc: Optional[subprocess.Popen] = None
    if _have_listener(port):
        logger.info(f"🔗 Adopting existing webapp on :{port}")
    else:
        uvicorn_proc = _spawn_uvicorn(port)
        if not _wait_for_uvicorn(port):
            logger.error("❌ uvicorn failed to start within 15 s")
            if uvicorn_proc is not None:
                uvicorn_proc.terminate()
            return 1

    try:
        cloudflared = cloudflared_runner.spawn_cloudflared(
            config_path,
            PROJECT_ROOT,
            capture_output=True,
            new_process_group=False,
        )
    except cloudflared_runner.CloudflaredNotFound as exc:
        raise SystemExit(f"❌ {exc}")
    streamer = threading.Thread(target=_stream, args=(cloudflared,), daemon=True)
    streamer.start()

    if hostname:
        _persist_tunnel_url(hostname)

    try:
        cloudflared.wait()
    except KeyboardInterrupt:
        logger.info("⏹️  Ctrl+C — shutting down")
    finally:
        for proc, name in ((cloudflared, "cloudflared"), (uvicorn_proc, "uvicorn")):
            if proc is None:
                continue
            cloudflared_runner.stop_process(proc, name)
        cloudflared_runner.remove_tunnel_url(TUNNEL_URL_FILE)

    return 0


if __name__ == "__main__":
    sys.exit(main())
