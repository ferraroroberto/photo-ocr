"""Selector event-loop shim (issue #82) — root cause of the ":8444 wedge".

asyncio's default Windows proactor event loop closes its listening socket
on any aborted client connection (WinError 64); the selector loop's accept
path doesn't. These tests cover the wiring (every uvicorn spawn of
``app.webapp.server:app`` picks the shim) and the actual accept-loop
resilience the shim buys.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
from pathlib import Path

import pytest

from app.webapp.event_loop import LOOP_FACTORY, selector_loop_factory
from app.webapp.manager import WebappManager, WebappManagerConfig

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_selector_loop_factory_returns_selector_instance_on_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    sentinel = object()
    monkeypatch.setattr(asyncio, "SelectorEventLoop", lambda: sentinel)
    assert selector_loop_factory() is sentinel


def test_selector_loop_factory_defers_on_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sentinel = object()
    monkeypatch.setattr(asyncio, "new_event_loop", lambda: sentinel)
    assert selector_loop_factory() is sentinel


def test_selector_loop_factory_is_zero_arg_and_returns_an_instance():
    """Regression pin: uvicorn imports a *custom* --loop target and calls
    it as a bare Callable[[], AbstractEventLoop] -- no use_subprocess kwarg,
    and it must return an instantiated loop, not a loop class (original
    bug: returning the class left Runner calling unbound methods)."""
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()


def test_manager_build_command_passes_loop_factory():
    mgr = WebappManager(WebappManagerConfig(port=18444))
    cmd = mgr._build_command()
    assert "--loop" in cmd
    assert cmd[cmd.index("--loop") + 1] == LOOP_FACTORY


def test_webapp_cmd_passes_loop_factory(monkeypatch):
    import argparse

    from app.cli.commands.webapp_cmd import WebappCommand
    from src import AppConfig

    captured = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(
        "app.webapp.manager.cert_paths", lambda *a, **k: None
    )

    config = AppConfig(webapp={"host": "127.0.0.1", "port": 18444})
    WebappCommand(config=config).execute(argparse.Namespace(host=None, port=None))
    assert captured.get("loop") == LOOP_FACTORY


def test_disposable_webapp_reuses_manager_command(monkeypatch):
    """The e2e autoboot and the design-review synthetic run both boot through
    scripts/disposable_webapp.py, which must take its uvicorn argv (loop
    factory, cert flags) from WebappManager rather than a second copy."""
    from scripts import disposable_webapp

    captured = {}

    class FakeProc:
        def poll(self):
            return None

    def fake_spawn(cmd, log, data_dir):
        captured["cmd"] = cmd
        captured["data_dir"] = data_dir
        return FakeProc()

    monkeypatch.setattr(disposable_webapp, "_spawn", fake_spawn)
    monkeypatch.setattr(disposable_webapp, "wait_healthz", lambda *a, **k: True)
    monkeypatch.setattr(disposable_webapp, "terminate", lambda proc: None)

    with disposable_webapp.disposable_webapp(startup_timeout=1.0) as base:
        assert base.startswith(("http://127.0.0.1:", "https://127.0.0.1:"))

    cmd = captured["cmd"]
    assert cmd[cmd.index("--loop") + 1] == LOOP_FACTORY
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert not captured["data_dir"].exists(), "temp data dir must be removed on exit"


def test_disposable_webapp_boot_failure_names_the_log(monkeypatch, tmp_path):
    from scripts import disposable_webapp

    class DeadProc:
        def poll(self):
            return 1

    def fake_spawn(cmd, log, data_dir):
        log.write("boom: port in use\n")
        return DeadProc()

    monkeypatch.setattr(disposable_webapp, "_spawn", fake_spawn)
    log_path = tmp_path / "boot.log"
    with pytest.raises(disposable_webapp.WebappBootError) as exc:
        with disposable_webapp.disposable_webapp(startup_timeout=1.0, log_path=log_path):
            pass
    assert "exited early" in str(exc.value)
    assert "boom: port in use" in str(exc.value)


def test_webapp_bat_delegates_to_webapp_subcommand():
    """webapp.bat must not carry its own uvicorn argv: the bind, certs and loop
    factory live in the `webapp` subcommand (covered above)."""
    src = (_REPO_ROOT / "webapp.bat").read_text(encoding="utf-8")
    assert "launcher.py" in src and "webapp" in src
    assert "uvicorn" not in src.replace("REM", "")


def test_run_named_tunnel_wires_loop_factory(monkeypatch):
    """scripts/run_named_tunnel.py backs webapp_tunnel_named.bat (headless/
    no-tray use) — the one uvicorn entrypoint the proactor-loop fix
    (commit 5750f6c) didn't originally reach. It now reuses
    WebappManager._build_command (covered by
    test_manager_build_command_passes_loop_factory above) instead of a
    second hand-rolled argv, so pin that it actually gets wired through."""
    from scripts import run_named_tunnel

    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd

    monkeypatch.setattr(run_named_tunnel.subprocess, "Popen", FakePopen)
    run_named_tunnel._spawn_uvicorn(18444)

    cmd = captured["cmd"]
    assert "--loop" in cmd
    assert cmd[cmd.index("--loop") + 1] == LOOP_FACTORY
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--port") + 1] == "18444"


async def _noop_handler(reader, writer):
    writer.close()


def _abort_connect_sync(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
    except OSError:
        pass
    finally:
        s.close()  # SO_LINGER(1, 0) forces an RST instead of a FIN


async def _still_accepting(port: int) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _bombard_with_aborts(rounds: int, burst: int) -> None:
    server = await asyncio.start_server(_noop_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for _ in range(rounds):
            threads = [
                threading.Thread(target=_abort_connect_sync, args=(port,))
                for _ in range(burst)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            await asyncio.sleep(0.02)
            assert await _still_accepting(
                port
            ), "listener died on an aborted client connection (issue #82)"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.skipif(sys.platform != "win32", reason="proactor-loop bug is Windows-only")
def test_selector_loop_survives_aborted_connections():
    asyncio.run(_bombard_with_aborts(rounds=10, burst=20), loop_factory=asyncio.SelectorEventLoop)


@pytest.mark.skipif(sys.platform != "win32", reason="proactor-loop bug is Windows-only")
def test_proactor_loop_dies_on_aborted_connections():
    """Documents the bug this issue fixes — the shim exists because this
    fails. If a future CPython/uvicorn release fixes the proactor loop
    itself, this test (not the shim) is what should be revisited."""
    with pytest.raises(AssertionError):
        asyncio.run(
            _bombard_with_aborts(rounds=10, burst=20), loop_factory=asyncio.ProactorEventLoop
        )


def test_uvicorn_argv_has_one_home():
    """Only WebappManager._build_command may spell out `python -m uvicorn`.

    A launcher that hand-builds the argv (the old webapp.bat and the synthetic
    design-review boot) drifts from the manager's bind, loop factory and cert
    flags; every other launch path goes through the manager or the `webapp`
    subcommand."""
    import re

    pattern = re.compile(r"""-m\W+uvicorn""")
    allowed = {
        _REPO_ROOT / "app" / "webapp" / "manager.py",
        Path(__file__).resolve(),
    }
    roots = [_REPO_ROOT / "app", _REPO_ROOT / "scripts", _REPO_ROOT / "src", _REPO_ROOT / "tests"]
    candidates = [p for r in roots for p in r.rglob("*") if p.suffix in {".py", ".ps1", ".bat"}]
    candidates += [p for p in _REPO_ROOT.glob("*.bat")] + [_REPO_ROOT / "launcher.py"]
    offenders = [
        str(p.relative_to(_REPO_ROOT))
        for p in candidates
        if p not in allowed and pattern.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not offenders, f"hand-built uvicorn argv outside the manager: {offenders}"
