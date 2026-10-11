"""Tests for the session-CRUD endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_session(client: TestClient) -> None:
    r = client.post("/api/sessions", json={})
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert body["incognito"] is False
    # The PWA is this endpoint's dominant caller → default source.
    assert body["source"] == "webapp"


def test_create_session_custom_source(client: TestClient) -> None:
    r = client.post("/api/sessions", json={"source": "app-launcher"})
    assert r.status_code == 200
    assert r.json()["source"] == "app-launcher"


def test_session_summary_surfaces_source(client: TestClient) -> None:
    sid = client.post(
        "/api/sessions", json={"source": "app-launcher"}
    ).json()["session_id"]
    listed = client.get("/api/sessions").json()["sessions"]
    match = next(s for s in listed if s["session_id"] == sid)
    assert match["source"] == "app-launcher"


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


def test_mixed_upload_failure_rolls_back_batch(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    files = [
        ("files", ("a.jpg", jpeg_bytes, "image/jpeg")),
        ("files", ("bad.txt", b"not an image", "text/plain")),
    ]
    r = client.post(f"/api/sessions/{sid}/photos", files=files)
    assert r.status_code == 400

    session = client.app.state.archive.get(sid)
    assert session is not None
    assert session.meta.photos == []
    assert not list(session.folder.glob("*.jpg"))


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


def test_reorder_photos_persists_server_order(
    client: TestClient, jpeg_bytes: bytes, png_bytes: bytes
) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[
            ("files", ("a.jpg", jpeg_bytes, "image/jpeg")),
            ("files", ("b.png", png_bytes, "image/png")),
        ],
    )

    r = client.post(
        f"/api/sessions/{sid}/photos/reorder",
        json={"order": [2, 1]},
    )

    assert r.status_code == 200
    body = r.json()
    assert [p["sequence_index"] for p in body["photos"]] == [1, 2]
    assert [p["path"] for p in body["photos"]] == ["01.jpg", "02.jpg"]
    assert [p["width"] for p in body["photos"]] == [64, 128]

    session = client.app.state.archive.get(sid)
    assert session is not None
    assert [p.width for p in session.meta.photos] == [64, 128]
    assert [p.path for p in session.meta.photos] == ["01.jpg", "02.jpg"]


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


def _saved_take_with_text(client: TestClient, text: str) -> str:
    """A saved take whose extracted text is in the archive and the index."""
    archive = client.app.state.archive
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    session = archive.get(sid)
    session.write_extracted(text, "gemini_flash", {}, {}, "verbatim-merge")
    archive.index_session(archive.get(sid))
    return sid


def _search_ids(client: TestClient, q: str) -> set:
    results = client.get("/api/search", params={"q": q}).json()["results"]
    return {r["session_id"] for r in results}


def test_patch_incognito_moves_a_take_out_of_and_back_into_history(
    client: TestClient,
) -> None:
    # The PWA creates the session on the first upload, before its Save to
    # History switch is on screen, so the switch flips it afterwards.
    sid = _saved_take_with_text(client, "zebra invented text")
    assert sid in _search_ids(client, "zebra")

    r = client.patch(f"/api/sessions/{sid}", json={"incognito": True})
    assert r.status_code == 200
    assert r.json() == {"session_id": sid, "incognito": True}
    listed = {s["session_id"] for s in client.get("/api/sessions").json()["sessions"]}
    assert sid not in listed
    assert sid not in _search_ids(client, "zebra")

    client.patch(f"/api/sessions/{sid}", json={"incognito": False})
    listed = {s["session_id"] for s in client.get("/api/sessions").json()["sessions"]}
    assert sid in listed
    assert sid in _search_ids(client, "zebra")


def test_patch_session_rejects_bad_requests(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    assert client.patch(f"/api/sessions/{sid}", json={}).status_code == 400
    assert client.patch("/api/sessions/nope", json={"incognito": True}).status_code == 404


def test_patch_incognito_refused_while_a_job_runs(client: TestClient) -> None:
    # A running job rewrites the meta and could restore the old value.
    from src.extract_job import set_extract_progress

    archive = client.app.state.archive
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    set_extract_progress(archive.get(sid), phase="running", chunks_total=2, chunks_done=0)
    r = client.patch(f"/api/sessions/{sid}", json={"incognito": True})
    assert r.status_code == 409
    assert archive.get(sid).meta.incognito is False
