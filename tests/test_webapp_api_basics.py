"""Tests for the FastAPI app — /healthz, /api/config, /api/status."""

from __future__ import annotations

import re
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


def test_install_ca_404_when_missing(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    # Point the route at an empty dir so the check is deterministic — a
    # dev machine that already generated the cert has the real
    # .mobileconfig sitting in app/webapp/static/.
    monkeypatch.setattr("app.webapp.routers.misc.STATIC_DIR", tmp_path)
    with TestClient(app_factory()) as client:
        r = client.get("/install-ca")
        assert r.status_code == 404


class TestBuildVersion:
    """Cache hygiene + build identity — see issue #5."""

    def test_version_endpoint_shape(self, app_factory) -> None:
        with TestClient(app_factory()) as client:
            r = client.get("/api/version")
            assert r.status_code == 200
            body = r.json()
            for key in ("git_sha", "built_at", "asset_hash"):
                assert key in body
                assert isinstance(body[key], str) and body[key]

    def test_index_is_content_hash_stamped(self, app_factory) -> None:
        with TestClient(app_factory()) as client:
            html = client.get("/").text
        # The entry module + stylesheet carry an 8-hex fleet hash
        # computed at startup.
        assert re.search(r"/static/main\.js\?v=[0-9a-f]{8}", html)
        assert re.search(r"/static/styles\.css\?v=[0-9a-f]{8}", html)

    def test_served_js_modules_have_stamped_imports(self, app_factory) -> None:
        # main.js imports the other modules; every relative import in
        # the served body must carry the fleet hash so an edit to any
        # module busts the whole graph.
        static_dir = (
            Path(__file__).resolve().parent.parent
            / "app" / "webapp" / "static"
        )
        on_disk = (static_dir / "main.js").read_text(encoding="utf-8")
        imports = re.findall(r"from\s*['\"]\./([\w\-.]+\.js)", on_disk)
        assert imports, "expected relative imports in main.js"
        with TestClient(app_factory()) as client:
            body = client.get("/static/main.js").text
        for name in imports:
            assert re.search(
                rf"\./{re.escape(name)}\?v=[0-9a-f]{{8}}", body
            ), f"import of {name} not stamped"

    def test_index_revalidates(self, app_factory) -> None:
        with TestClient(app_factory()) as client:
            cc = client.get("/").headers.get("cache-control", "")
        assert "no-cache" in cc

    def test_static_assets_are_long_cached(self, app_factory) -> None:
        with TestClient(app_factory()) as client:
            for asset in ("main.js", "state.js", "styles.css"):
                cc = client.get(f"/static/{asset}").headers.get(
                    "cache-control", ""
                )
                assert "max-age=31536000" in cc and "immutable" in cc

    def test_icons_revalidate_daily(self, app_factory) -> None:
        with TestClient(app_factory()) as client:
            cc = client.get("/static/favicon.ico").headers.get(
                "cache-control", ""
            )
        assert "max-age=86400" in cc
