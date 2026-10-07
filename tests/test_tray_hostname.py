"""``_tailscale_hostname`` feeds the tray's "Copy Tailscale URL": the host must
be the full ``.ts.net`` name, the only one the ``tailscale cert`` leaf covers."""

from __future__ import annotations

import json
import subprocess

import pytest

from app.tray.tray import _tailscale_hostname


def _fake_status(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    monkeypatch.setattr(
        "app.tray.tray.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout=json.dumps(payload), stderr=""
        ),
    )


def test_returns_the_full_dns_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_status(monkeypatch, {"Self": {"DNSName": "pc.tail1234.ts.net."}})
    assert _tailscale_hostname() == "pc.tail1234.ts.net"


def test_none_without_a_dns_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_status(monkeypatch, {"Self": {}})
    assert _tailscale_hostname() is None
