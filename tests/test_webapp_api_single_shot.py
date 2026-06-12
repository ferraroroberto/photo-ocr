"""Tests for the single-shot POST /api/extract consumable endpoint —
mocked hub. See docs/consuming-the-session-api.md."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import create_app
from src.archive import SessionArchive
from src.ocr_client import OcrError, OcrResult


@pytest.fixture
def client(tmp_path: Path):
    app = create_app()
    app.state.archive = SessionArchive(root=tmp_path / "archive")
    with TestClient(app) as c:
        yield c


def _ok_result(text: str = "hello world", model: str = "gemini_flash") -> OcrResult:
    return OcrResult(
        extracted_text=text,
        model=model,
        request_payload={"model": model, "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": text}]},
    )


def test_single_shot_success_returns_text(client: TestClient, jpeg_bytes: bytes) -> None:
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_ok_result()
    ) as mock_extract:
        r = client.post(
            "/api/extract",
            params={"model": "gemini_flash"},
            files=[("files", ("shot.jpg", jpeg_bytes, "image/jpeg"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "hello world"
    assert body["model"] == "gemini_flash"
    assert body["chars"] == len("hello world")
    assert body["incognito"] is False
    mock_extract.assert_called_once()

    # The take is kept in History (recoverable) like a PWA session.
    listed = client.get("/api/sessions").json()["sessions"]
    assert any(s["session_id"] == body["session_id"] for s in listed)


def test_single_shot_missing_files_is_422(client: TestClient) -> None:
    r = client.post("/api/extract", params={"model": "gemini_flash"})
    assert r.status_code == 422


def test_single_shot_over_cap_returns_413(client: TestClient, jpeg_bytes: bytes) -> None:
    client.app.state.webapp_config.single_shot_max_photos = 1
    r = client.post(
        "/api/extract",
        files=[
            ("files", ("a.jpg", jpeg_bytes, "image/jpeg")),
            ("files", ("b.jpg", jpeg_bytes, "image/jpeg")),
        ],
    )
    assert r.status_code == 413
    assert "single_shot_max_photos" not in r.json()["detail"]  # human message
    assert "async session flow" in r.json()["detail"]


def test_single_shot_unknown_model_returns_400(client: TestClient, jpeg_bytes: bytes) -> None:
    r = client.post(
        "/api/extract",
        params={"model": "no_such_model"},
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    assert r.status_code == 400


def test_single_shot_hub_error_returns_502(client: TestClient, jpeg_bytes: bytes) -> None:
    with patch.object(
        client.app.state.ocr_client, "extract", side_effect=OcrError("hub down")
    ):
        r = client.post(
            "/api/extract",
            params={"model": "gemini_flash"},
            files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
        )
    assert r.status_code == 502
    assert "hub down" in r.json()["detail"]


def test_single_shot_source_defaults_to_api(client: TestClient, jpeg_bytes: bytes) -> None:
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_ok_result()
    ):
        r = client.post(
            "/api/extract",
            params={"model": "gemini_flash"},
            files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "api"
    # Attributable in the History listing too.
    listed = client.get("/api/sessions").json()["sessions"]
    match = next(s for s in listed if s["session_id"] == body["session_id"])
    assert match["source"] == "api"


def test_single_shot_source_label_recorded_and_searchable(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_ok_result()
    ):
        r = client.post(
            "/api/extract",
            params={"model": "gemini_flash", "source": "app-launcher"},
            files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
        )
    assert r.status_code == 200
    assert r.json()["source"] == "app-launcher"
    # "find the app-launcher OCRs" works via full-text search.
    results = client.get("/api/search", params={"q": "app-launcher"}).json()["results"]
    assert any(hit["source"] == "app-launcher" for hit in results)


def test_single_shot_incognito_excluded_from_history(client: TestClient, jpeg_bytes: bytes) -> None:
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_ok_result()
    ):
        r = client.post(
            "/api/extract",
            params={"model": "gemini_flash", "incognito": "true"},
            files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["incognito"] is True
    listed = client.get("/api/sessions").json()["sessions"]
    assert all(s["session_id"] != body["session_id"] for s in listed)
