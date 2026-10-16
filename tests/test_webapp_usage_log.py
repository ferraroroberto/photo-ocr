"""The vendored usage log is mounted behind the app's auth, stores into the
runtime-data directory only, and the client side holds its privacy contract."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import create_app
from src.archive import SessionArchive
from src.webapp_config import WebappConfig

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static"
# The bounded id pattern the vendored server and client both enforce.
TRACK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

ENVELOPE = {
    "session_id": "0f0f0f0f-0000-4000-8000-000000000000",
    "device_class": "phone",
    "os_class": "ios",
    "events": [
        {"kind": "session_start"},
        {"kind": "view", "id": "capture"},
        {"kind": "tap", "id": "capture.extract", "view": "capture"},
    ],
}


def _app(tmp_path: Path, *, token: str = ""):
    app = create_app()
    app.state.archive = SessionArchive(root=tmp_path / "archive")
    cfg = app.state.webapp_config
    app.state.webapp_config = WebappConfig(
        ocr_model_default=cfg.ocr_model_default,
        ocr_models_available=cfg.ocr_models_available,
        ocr_prompt_default=cfg.ocr_prompt_default,
        llm_hub_url=cfg.llm_hub_url,
        history_retention_days=cfg.history_retention_days,
        max_photos_per_session=cfg.max_photos_per_session,
        max_photo_dimension_px=cfg.max_photo_dimension_px,
        auth_token=token,
        auth_password="",
    )
    return app


def _rows(monkeypatch_dir: str) -> list[tuple]:
    db = Path(monkeypatch_dir) / "usage.sqlite3"
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT kind, view_id, control_id, device_class, os_class FROM events"
            " ORDER BY id"
        ).fetchall()


def test_events_land_in_the_runtime_data_dir(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PHOTO_OCR_DATA_DIR", str(data_dir))
    app = _app(tmp_path, token="secret")
    with TestClient(app) as client:
        r = client.post(
            "/api/usage/events",
            json=ENVELOPE,
            headers={"Authorization": "Bearer secret"},
        )
        assert r.status_code in (200, 202, 204)
    assert _rows(str(data_dir)) == [
        ("session_start", None, None, "phone", "ios"),
        ("view", "capture", None, "phone", "ios"),
        ("tap", "capture", "capture.extract", "phone", "ios"),
    ]


def test_usage_endpoints_sit_behind_the_token(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PHOTO_OCR_DATA_DIR", str(data_dir))
    app = _app(tmp_path, token="secret")
    with TestClient(app) as client:
        assert client.post("/api/usage/events", json=ENVELOPE).status_code == 401
        assert client.get("/api/usage/summary").status_code == 401
    assert _rows(str(data_dir)) == []


def test_an_off_pattern_control_id_is_dropped(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PHOTO_OCR_DATA_DIR", str(data_dir))
    app = _app(tmp_path)
    bad = dict(ENVELOPE, events=[{"kind": "tap", "id": "some text with spaces/x"}])
    with TestClient(app) as client:
        client.post("/api/usage/events", json=bad)
    assert _rows(str(data_dir)) == []


@pytest.fixture(scope="module")
def tracked_ids() -> list[str]:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(STATIC_DIR.glob("*.js"))
    )
    return re.findall(r'data-track="([^"]*)"', html) + re.findall(
        r"dataset\.track\s*=\s*'([^']*)'", js
    )


def test_control_ids_are_constants_in_the_bounded_pattern(tracked_ids) -> None:
    assert len(set(tracked_ids)) >= 20
    assert [i for i in tracked_ids if not TRACK_ID.match(i)] == []


def test_no_control_id_is_derived_from_content() -> None:
    # An id built from text, a template or a variable would leak content.
    js = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(STATIC_DIR.glob("*.js"))
    )
    for m in re.finditer(r"dataset\.track\s*=\s*([^;]+);", js):
        assert re.fullmatch(r"'[^']*'", m.group(1).strip()), m.group(0)
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'data-track="[^"]*[{$]', html)


def test_the_client_is_never_pointed_off_origin() -> None:
    main = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    call = re.search(r"initUsageLog\((.*?)\n\}\);", main, re.S)
    assert call, "initUsageLog call not found"
    assert "endpoint" not in call.group(1)
