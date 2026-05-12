"""Tests for the FastAPI app — /healthz, /api/config, /api/status."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import create_app
from src.archive import SessionArchive


@pytest.fixture
def app_factory(tmp_path: Path, monkeypatch):
    """Build a fresh app with archive rooted at tmp_path."""

    def factory():
        # Patch the archive root so each test gets a clean dir.
        monkeypatch.setenv("PHOTO_OCR_TEST_ROOT", str(tmp_path))
        app = create_app()
        # Replace archive with a tmp_path-rooted one.
        app.state.archive = SessionArchive(root=tmp_path / "archive")
        return app

    return factory


def test_healthz_returns_ok(app_factory) -> None:
    with TestClient(app_factory()) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["service"] == "photo-ocr-webapp"


def test_get_config_returns_prompts_and_models(app_factory) -> None:
    with TestClient(app_factory()) as client:
        r = client.get("/api/config")
        assert r.status_code == 200
        body = r.json()
        assert "ocr_model_default" in body
        assert "ocr_models_available" in body
        assert isinstance(body["ocr_prompts"], list)
        assert any(p["id"] == "verbatim-merge" for p in body["ocr_prompts"])


def test_status_returns_hub_block(app_factory) -> None:
    with TestClient(app_factory()) as client:
        r = client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert "llm_hub" in body
        assert "reachable" in body["llm_hub"]


def test_install_ca_404_when_missing(app_factory, tmp_path: Path) -> None:
    with TestClient(app_factory()) as client:
        r = client.get("/install-ca")
        assert r.status_code == 404
