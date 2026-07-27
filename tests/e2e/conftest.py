"""Fixtures for the photo-ocr Playwright smoke suite.

Three run modes:

* **Default (bare ``pytest tests/e2e``).** Guarded by the vendor-verbatim
  ``tests/e2e/_e2e_live_guard.py`` (project-scaffolding issue #191/#194;
  same module every fleet adopter copies byte-identical). If the live
  tray's port is occupied and ``PHOTO_OCR_E2E_LIVE`` isn't set, it
  refuses via ``pytest.exit`` naming the flag — an accidental bare run
  must not silently load-test the tray a phone may be using. If the
  port is free, the suite falls back to booting its own disposable
  instance, same as autoboot below. See issue #108.
* **Live (explicit opt-in).** ``PHOTO_OCR_E2E_LIVE=1``
  (``scripts/run-e2e.ps1`` sets it — the deliberate dev-loop entry
  point) means the caller has chosen to *act* on the already-running
  tray at https://127.0.0.1:8444. What "act" means is caller-owned and
  repo-specific per the guard's own contract; this repo's choice is
  read-only smoke checks against the live tray, never a kill (the
  canonical restart path is ``tray.bat --restart``, reserved for an
  actual restart need, not a test opt-in).
* **Autoboot (pre-ship gate).** Enabled with ``--e2e-autoboot`` or the
  ``PHOTO_OCR_E2E_AUTOBOOT=1`` env var. ``_autoboot_server`` spawns a
  disposable webapp on a free TCP port (HTTPS, reusing
  webapp/certificates/). In this mode a failure to boot is a hard
  *failure*, never a skip — the whole point of the gate is that a
  missing server can't silently pass. See issue #8.

Dual projection (issue #7): when ``--browser`` isn't passed the suite
runs in two projections — **Chromium desktop** and **WebKit projected
onto an iPhone 14** (viewport, user-agent, touch). WebKit is the same
engine family as iOS Mobile Safari, so this catches the bulk of
"Safari is unhappy" regressions before they reach a phone. A test
marked ``desktop_only`` opts out of the WebKit/iPhone projection.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib3
from pathlib import Path
from typing import IO, Iterator, List, Optional

import pytest
import requests
from playwright.sync_api import BrowserContext, Page

from tests.e2e._e2e_live_guard import require_disposable_instance

logger = logging.getLogger(__name__)

# The cert on 8444 is for the .ts.net name, not loopback — silence the
# urllib3 noise from /healthz.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBAPP_CONFIG = _REPO_ROOT / "config" / "webapp_config.json"
_BASE_URL = "https://127.0.0.1:8444"
_TOKEN_KEY = "photo-ocr.token"  # must match TOKEN_KEY in app/webapp/static/state.js

# Playwright device descriptor for the WebKit projection — iOS Mobile
# Safari's engine family on an iPhone-shaped viewport.
_IPHONE_DEVICE = "iPhone 14"

_AUTOBOOT_ENV = "PHOTO_OCR_E2E_AUTOBOOT"
# Explicit opt-in for adopting the LIVE tray on :8444 (issue #108) — the
# flag name passed to the vendored _e2e_live_guard.require_disposable_instance.
# Without it a bare `pytest tests/e2e` must not silently load-test whatever
# instance is currently listening — that instance may be the one a phone
# is using.
_LIVE_ENV = "PHOTO_OCR_E2E_LIVE"
_LIVE_PORT = 8444

# Bounded default Playwright timeout (issue #45).
# Playwright's built-in default is 30 s — an implicit auto-wait that times
# out with no locator name, stacking toward a multi-minute black-box CI
# hang.  Capping it here means a stuck .click() / goto() / wait_for_*()
# with no explicit timeout= fails at ~15 s and names the locator in the
# error, making the hang self-diagnosing.  Set E2E_DEFAULT_TIMEOUT_MS to
# override (e.g. 20000 for slower CI runners).  expect() assertions keep
# their own 5 s default; per-call timeout= still overrides this cap.
_DEFAULT_TIMEOUT_MS = int(os.environ.get("E2E_DEFAULT_TIMEOUT_MS", "15000"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--e2e-autoboot",
        action="store_true",
        default=False,
        help="Boot a disposable webapp on a free port instead of "
        "requiring a live tray. Equivalent to PHOTO_OCR_E2E_AUTOBOOT=1.",
    )


def _autoboot_enabled(config: pytest.Config) -> bool:
    return bool(config.getoption("--e2e-autoboot")) or (
        os.environ.get(_AUTOBOOT_ENV, "") == "1"
    )


def pytest_configure(config: pytest.Config) -> None:
    # Default the e2e suite to dual projections (Chromium-desktop +
    # WebKit-iPhone) when --browser wasn't passed, so WebKit coverage is
    # impossible to forget (issue #7). A single engine can still be
    # pinned with `--browser chromium` for a faster dev loop;
    # pytest-playwright treats --browser as append-style.
    selected: List[str] = config.option.browser
    if not selected:
        selected.extend(["chromium", "webkit"])


# ----------------------------------------------------------- autoboot


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn(cmd: List[str], log: IO[str]) -> subprocess.Popen:
    kwargs: dict = dict(
        cwd=str(_REPO_ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    if sys.platform == "win32":
        # New process group so CTRL_BREAK reaches it for a clean stop;
        # no window so the test run doesn't flash consoles.
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(cmd, **kwargs)


def _terminate(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("CTRL_BREAK_EVENT failed: %s", exc)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("⚠️  autoboot: process teardown failed: %s", exc)


def _wait_healthz(base: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            res = requests.get(f"{base}/healthz", timeout=2, verify=False)
            if res.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.4)
    return False


@pytest.fixture(scope="session")
def _autoboot_server() -> Iterator[str]:
    """Spawn a disposable webapp and yield its base URL.

    A hard failure (``pytest.fail``) — never a skip — if it doesn't come
    up: under the pre-ship gate a missing server must not pass silently.
    """
    from app.webapp.event_loop import LOOP_FACTORY
    from app.webapp.manager import cert_paths

    logs_dir = _REPO_ROOT / "webapp"  # gitignored runtime dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_handle: Optional[IO[str]] = None
    wa_proc: Optional[subprocess.Popen] = None

    try:
        log_handle = (logs_dir / "e2e-autoboot-webapp.log").open(
            "w", encoding="utf-8", errors="replace"
        )
        # A free port, never the hardcoded 8444 — the dev tray may hold it.
        port = _free_tcp_port()
        certs = cert_paths()
        scheme = "https" if certs else "http"
        cmd = [
            sys.executable,
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
        if certs:
            cert, key = certs
            cmd += ["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)]
        wa_proc = _spawn(cmd, log_handle)

        base = f"{scheme}://127.0.0.1:{port}"
        if not _wait_healthz(base, timeout=10):
            _terminate(wa_proc)
            pytest.fail(
                f"autoboot: webapp did not answer /healthz at {base} "
                "within 10s — see webapp/e2e-autoboot-webapp.log"
            )
        logger.info("✅ autoboot: webapp ready at %s", base)
        yield base
    finally:
        _terminate(wa_proc)
        if log_handle is not None:
            try:
                log_handle.close()
            except Exception:  # pragma: no cover
                pass


# ----------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _skip_desktop_only_on_webkit(
    request: pytest.FixtureRequest, browser_name: str
) -> None:
    """Honour the ``desktop_only`` marker — its tests never run under the
    WebKit/iPhone projection."""
    if browser_name == "webkit" and request.node.get_closest_marker(
        "desktop_only"
    ):
        pytest.skip("desktop_only — not run on the WebKit/iPhone projection")


@pytest.fixture(autouse=True)
def _bound_default_timeouts(context: BrowserContext) -> None:
    """Cap Playwright's implicit action + navigation timeout (issue #45).

    Pages created from this context inherit the cap, so a stuck
    wait_for_selector / click / goto with no explicit timeout= fails at
    _DEFAULT_TIMEOUT_MS and names the locator — instead of silently
    stacking toward Playwright's opaque 30 s default.
    """
    context.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(_DEFAULT_TIMEOUT_MS)


@pytest.fixture(scope="session")
def base_url(request: pytest.FixtureRequest) -> str:
    if _autoboot_enabled(request.config):
        return request.getfixturevalue("_autoboot_server")

    # Vendored guard (issue #108): refuses via pytest.exit if the live
    # port is occupied and PHOTO_OCR_E2E_LIVE isn't set. This repo's
    # caller-side choice on an opt-in hit is to *adopt* the live tray for
    # read-only smoke checks, never kill it — see the module docstring
    # above.
    live_opt_in = require_disposable_instance(_LIVE_PORT, _LIVE_ENV)
    if live_opt_in and _wait_healthz(_BASE_URL, timeout=2):
        logger.info("✅ %s=1 — adopting live tray at %s", _LIVE_ENV, _BASE_URL)
        return _BASE_URL
    return request.getfixturevalue("_autoboot_server")


@pytest.fixture(scope="session")
def webapp_config() -> dict:
    # Missing config is fine — loopback bypasses the bearer gate, so the
    # suite still runs end-to-end. Returning {} keeps autoboot working on
    # a clean checkout where webapp_config.json hasn't been created yet.
    if not _WEBAPP_CONFIG.exists():
        return {}
    return json.loads(_WEBAPP_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def auth_token(webapp_config: dict) -> str:
    # Loopback bypasses the bearer middleware, so an empty token is fine
    # for these local tests. We still seed it when present so the SPA boot
    # path mirrors a real phone session.
    return (webapp_config.get("auth_token") or "").strip()


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict, browser_name: str, playwright
) -> dict:
    # The cert on 8444 is for the .ts.net name, not 127.0.0.1 — the SPA
    # won't load otherwise.
    args = {**browser_context_args, "ignore_https_errors": True}
    if browser_name == "webkit":
        # Project the WebKit engine onto an iPhone 14 — viewport,
        # user_agent, has_touch, is_mobile, device_scale_factor — so the
        # suite exercises an iPhone-shaped target on Windows (issue #7).
        args = {**args, **playwright.devices[_IPHONE_DEVICE]}
    return args


def _seed_token_init_script(token: str) -> str:
    # Seeded *before* the first navigation so the SPA reads it on boot
    # rather than going through the ?token=… URL strip dance.
    safe = json.dumps(token)
    safe_key = json.dumps(_TOKEN_KEY)
    return f"window.localStorage.setItem({safe_key}, {safe});"


@pytest.fixture
def authed_page(
    context: BrowserContext, base_url: str, auth_token: str
) -> Iterator[Page]:
    if auth_token:
        context.add_init_script(_seed_token_init_script(auth_token))
    page = context.new_page()
    try:
        yield page
    finally:
        page.close()
