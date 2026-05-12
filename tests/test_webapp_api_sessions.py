"""Tests for the session-CRUD endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import create_app
from src.archive import SessionArchive


@pytest.fixture
def client(tmp_path: Path):
    app = create_app()
    app.state.archive = SessionArchive(root=tmp_path / "archive")
    with TestClient(app) as c:
        yield c


def test_create_session(client: TestClient) -> None:
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert body["incognito"] is False


def test_upload_photos(client: TestClient, jpeg_bytes: bytes) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    files = [
        ("files", ("a.jpg", jpeg_bytes, "image/jpeg")),
        ("files", ("b.jpg", jpeg_bytes, "image/jpeg")),
    ]
    r = client.post(f"/api/sessions/{sid}/photos", files=files)
    assert r.status_code == 200
    body = r.json()
    assert len(body["photos"]) == 2
    assert body["photos"][0]["sequence_index"] == 1
    assert body["photos"][1]["sequence_index"] == 2


def test_upload_rejects_bad_content_type(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    files = [("files", ("a.txt", b"not an image", "text/plain"))]
    r = client.post(f"/api/sessions/{sid}/photos", files=files)
    assert r.status_code == 400


def test_remove_photo_renumbers(client: TestClient, jpeg_bytes: bytes) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[
            ("files", ("a.jpg", jpeg_bytes, "image/jpeg")),
            ("files", ("b.jpg", jpeg_bytes, "image/jpeg")),
        ],
    )
    r = client.delete(f"/api/sessions/{sid}/photos/1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["photos"]) == 1
    assert body["photos"][0]["sequence_index"] == 1


def test_list_sessions_pagination(client: TestClient) -> None:
    for _ in range(3):
        client.post("/api/sessions", json={})
    r = client.get("/api/sessions?limit=2&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sessions"]) == 2
    assert body["total"] == 3


def test_delete_session(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200
    r2 = client.delete(f"/api/sessions/{sid}")
    assert r2.status_code == 404


def test_extract_400_when_empty(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.post(f"/api/sessions/{sid}/extract", json={})
    assert r.status_code == 400


def test_extract_unknown_model_is_400(client: TestClient, jpeg_bytes: bytes) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    r = client.post(
        f"/api/sessions/{sid}/extract",
        json={"model": "definitely_not_a_model"},
    )
    assert r.status_code == 400
