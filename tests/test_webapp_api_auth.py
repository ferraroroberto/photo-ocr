"""Tests for the bearer-token middleware + /api/login.

Note: starlette's TestClient uses ``"testclient"`` as ``request.client.host``,
not ``127.0.0.1``. That means TestClient-driven calls *never* hit the
middleware's loopback bypass — perfect for verifying the gate kicks in,
but the loopback bypass itself is tested by monkey-patching the loopback
set so the TestClient host counts as loopback for one assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp import middleware as middleware_module
from app.webapp.server import create_app
from src.archive import SessionArchive
from src.webapp_config import WebappConfig


def _set_token(app, *, token: str = "", password: str = "") -> None:
    cfg = app.state.webapp_config
    new_cfg = WebappConfig(
        ocr_model_default=cfg.ocr_model_default,
        ocr_models_available=cfg.ocr_models_available,
        ocr_prompt_default=cfg.ocr_prompt_default,
        llm_hub_url=cfg.llm_hub_url,
        host=cfg.host,
        port=cfg.port,
        history_retention_days=cfg.history_retention_days,
        max_photos_per_session=cfg.max_photos_per_session,
        max_photo_dimension_px=cfg.max_photo_dimension_px,
        auth_token=token,
        auth_password=password,
    )
    app.state.webapp_config = new_cfg


@pytest.fixture
def app_with_archive(tmp_path: Path):
    app = create_app()
    app.state.archive = SessionArchive(root=tmp_path / "archive")
    return app


def test_no_token_no_gate(app_with_archive) -> None:
    with TestClient(app_with_archive) as client:
        r = client.get("/api/config")
        assert r.status_code == 200


def test_loopback_bypasses_token(app_with_archive, monkeypatch) -> None:
    """Treat the TestClient host as loopback to exercise the bypass branch."""
    _set_token(app_with_archive, token="secret")
    monkeypatch.setattr(
        middleware_module,
        "_LOOPBACK_HOSTS",
        frozenset({"127.0.0.1", "::1", "localhost", "testclient"}),
    )
    with TestClient(app_with_archive) as client:
        r = client.get("/api/config")
        assert r.status_code == 200


def test_non_loopback_requires_token(app_with_archive) -> None:
    """TestClient host is non-loopback by default → 401 without token."""
    _set_token(app_with_archive, token="secret")
    with TestClient(app_with_archive) as client:
        r = client.get("/api/config")
        assert r.status_code == 401
        assert "missing or invalid" in r.json()["detail"].lower()


def test_non_loopback_with_valid_token(app_with_archive) -> None:
    _set_token(app_with_archive, token="secret")
    with TestClient(app_with_archive) as client:
        r = client.get(
            "/api/config", headers={"Authorization": "Bearer secret"}
        )
        assert r.status_code == 200


def test_non_loopback_token_via_query_param(app_with_archive) -> None:
    _set_token(app_with_archive, token="secret")
    with TestClient(app_with_archive) as client:
        r = client.get("/api/config?token=secret")
        assert r.status_code == 200


def test_healthz_always_exempt(app_with_archive) -> None:
    _set_token(app_with_archive, token="secret")
    with TestClient(app_with_archive) as client:
        r = client.get("/healthz")
        assert r.status_code == 200


def test_login_503_when_no_password_configured(app_with_archive) -> None:
    _set_token(app_with_archive, token="secret", password="")
    with TestClient(app_with_archive) as client:
        r = client.post("/api/login", json={"password": "anything"})
        assert r.status_code == 503


def test_login_401_on_wrong_password(app_with_archive) -> None:
    _set_token(app_with_archive, token="secret", password="hunter2")
    with TestClient(app_with_archive) as client:
        r = client.post("/api/login", json={"password": "wrong"})
        assert r.status_code == 401


def test_login_200_returns_token(app_with_archive) -> None:
    _set_token(app_with_archive, token="secret", password="hunter2")
    with TestClient(app_with_archive) as client:
        r = client.post("/api/login", json={"password": "hunter2"})
        assert r.status_code == 200
        assert r.json()["token"] == "secret"
