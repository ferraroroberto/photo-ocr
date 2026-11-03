"""The password route: lock-out after repeated wrong passwords, the owner alert,
and the PC-itself exemption. A fake clock and a fake notifier — no sleeping,
no real Telegram."""

from __future__ import annotations

import threading
from typing import List

import pytest
from fastapi.testclient import TestClient

from app.webapp import middleware as middleware_module
from app.webapp.login_alert import APP_NAME, make_alert_owner
from app.webapp.server import create_app
from src.login_lockout import LoginLockout
from src.webapp_config import WebappConfig

PASSWORD = "correct-horse"
TOKEN = "bearer-token-value"
FREE_WRONG = 6  # five free attempts, then the sixth is the first to earn a wait


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeNotifier:
    """Records messages and the thread that sent them; never touches the network."""

    def __init__(self) -> None:
        self.texts: List[str] = []
        self.threads: List[int] = []
        self.sent = threading.Event()

    def send_text(self, text: str) -> None:
        self.texts.append(text)
        self.threads.append(threading.get_ident())
        self.sent.set()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def app(clock: FakeClock, notifier: FakeNotifier):
    app = create_app()
    app.state.webapp_config = WebappConfig(auth_token=TOKEN, auth_password=PASSWORD)
    app.state.login_lockout = LoginLockout(
        on_alert=make_alert_owner(notifier), clock=clock
    )
    return app


def _wrong(client: TestClient, **kwargs):
    return client.post("/api/login", json={"password": "nope"}, **kwargs)


def test_wrong_passwords_then_429_with_retry_after(app, clock) -> None:
    with TestClient(app) as client:
        assert [_wrong(client).status_code for _ in range(FREE_WRONG)] == [401] * FREE_WRONG

        locked = _wrong(client)
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) >= 1

        # A refused attempt neither reads the password nor adds to the count.
        right_while_locked = client.post("/api/login", json={"password": PASSWORD})
        assert right_while_locked.status_code == 429

        clock.now += 3600
        ok = client.post("/api/login", json={"password": PASSWORD})
        assert ok.status_code == 200
        assert ok.json() == {"token": TOKEN}


def test_correct_password_clears_the_streak(app) -> None:
    with TestClient(app) as client:
        for _ in range(FREE_WRONG - 1):
            assert _wrong(client).status_code == 401
        assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
        # The count restarted, so another run of free attempts is allowed.
        assert [_wrong(client).status_code for _ in range(FREE_WRONG)] == [401] * FREE_WRONG


def test_three_wrong_passwords_alert_once(app, notifier) -> None:
    with TestClient(app) as client:
        for _ in range(2):
            _wrong(client)
        assert not notifier.sent.is_set()

        _wrong(client)
        assert notifier.sent.wait(timeout=5)

        for _ in range(2):  # the rest of the streak raises nothing more
            _wrong(client)

    assert len(notifier.texts) == 1
    text = notifier.texts[0]
    assert APP_NAME in text
    assert "testclient" in text
    assert "3" in text
    assert PASSWORD not in text and TOKEN not in text and "nope" not in text
    # The login never waits on the send: it ran on another thread.
    assert notifier.threads[0] != threading.get_ident()


def test_pc_itself_is_never_locked(app, monkeypatch) -> None:
    """The TestClient host counts as loopback, with no tunnel header: the PC."""
    monkeypatch.setattr(
        middleware_module,
        "_LOOPBACK_HOSTS",
        frozenset({"127.0.0.1", "::1", "localhost", "testclient"}),
    )
    with TestClient(app) as client:
        assert [_wrong(client).status_code for _ in range(FREE_WRONG + 4)] == [401] * (
            FREE_WRONG + 4
        )


def test_tunnel_callers_are_keyed_on_the_tunnel_header(app, monkeypatch) -> None:
    """Every public request reaches the app over loopback; the header tells callers apart."""
    monkeypatch.setattr(
        middleware_module,
        "_LOOPBACK_HOSTS",
        frozenset({"127.0.0.1", "::1", "localhost", "testclient"}),
    )
    guesser = {"Cf-Connecting-Ip": "203.0.113.7"}
    bystander = {"Cf-Connecting-Ip": "203.0.113.8"}
    with TestClient(app) as client:
        for _ in range(FREE_WRONG):
            assert _wrong(client, headers=guesser).status_code == 401
        # Not exempt as the PC, and locked on its own address.
        assert _wrong(client, headers=guesser).status_code == 429
        # Another public caller, and the PC, are unaffected.
        assert _wrong(client, headers=bystander).status_code == 401
        assert _wrong(client).status_code == 401


def test_tunnel_header_is_ignored_from_a_non_loopback_peer(app) -> None:
    """A remote caller cannot dodge the lock by rotating a header it chose."""
    with TestClient(app) as client:
        statuses = [
            _wrong(client, headers={"Cf-Connecting-Ip": f"198.51.100.{i}"}).status_code
            for i in range(FREE_WRONG + 1)
        ]
    assert statuses == [401] * FREE_WRONG + [429]


def test_unparseable_tunnel_header_is_not_exempt(app, monkeypatch) -> None:
    monkeypatch.setattr(
        middleware_module,
        "_LOOPBACK_HOSTS",
        frozenset({"127.0.0.1", "::1", "localhost", "testclient"}),
    )
    with TestClient(app) as client:
        statuses = [
            _wrong(client, headers={"Cf-Connecting-Ip": "not-an-ip"}).status_code
            for _ in range(FREE_WRONG + 1)
        ]
    assert statuses == [401] * FREE_WRONG + [429]
