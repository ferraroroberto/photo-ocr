"""Tests for the extract / redo endpoints — mocked hub."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_extract_success(client: TestClient, jpeg_bytes: bytes) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    fake_result = OcrResult(
        extracted_text="hello world",
        model="gemini_flash",
        request_payload={"model": "gemini_flash", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": "hello world"}]},
    )
    with patch.object(
        client.app.state.ocr_client,
        "extract",
        return_value=fake_result,
    ) as mock_extract:
        r = client.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})
    assert r.status_code == 200
    body = r.json()
    assert body["extracted"] == "hello world"
    assert body["model"] == "gemini_flash"
    assert body["reused"] is False
    mock_extract.assert_called_once()


def test_extract_returns_424_on_hub_error(client: TestClient, jpeg_bytes: bytes) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    with patch.object(
        client.app.state.ocr_client,
        "extract",
        side_effect=OcrError("hub down"),
    ):
        r = client.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})
    assert r.status_code == 424
    assert "hub down" in r.json()["detail"]


def test_redo_re_runs_even_if_already_extracted(client: TestClient, jpeg_bytes: bytes) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    fake_result = OcrResult(
        extracted_text="first run",
        model="gemini_flash",
        request_payload={"model": "gemini_flash", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": "first run"}]},
    )
    with patch.object(
        client.app.state.ocr_client,
        "extract",
        return_value=fake_result,
    ):
        client.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})

    # /extract again would short-circuit (reused: True).
    fake_again = OcrResult(
        extracted_text="extract reused",
        model="gemini_flash",
        request_payload={"model": "gemini_flash", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": "extract reused"}]},
    )
    with patch.object(
        client.app.state.ocr_client,
        "extract",
        return_value=fake_again,
    ) as mock_again:
        r = client.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})
        assert r.json()["reused"] is True
        mock_again.assert_not_called()

    # /redo always re-runs.
    fake_redo = OcrResult(
        extracted_text="second run",
        model="gemini_pro",
        request_payload={"model": "gemini_pro", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": "second run"}]},
    )
    with patch.object(
        client.app.state.ocr_client,
        "extract",
        return_value=fake_redo,
    ) as mock_redo:
        r = client.post(f"/api/sessions/{sid}/redo", json={"model": "gemini_pro"})
        assert r.status_code == 200
        assert r.json()["extracted"] == "second run"
        assert r.json()["reused"] is False
        mock_redo.assert_called_once()
