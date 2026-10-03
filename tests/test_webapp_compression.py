"""Response compression — the entry document, JS/CSS and JSON go out gzipped.

Issue #128 (perf-review): the entry document was served identity-encoded.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_GZIP = {"Accept-Encoding": "gzip"}


def test_entry_document_is_gzipped(client: TestClient) -> None:
    r = client.get("/", headers=_GZIP)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "<html" in r.text  # the client decoded it back to the page


def test_static_css_is_gzipped(client: TestClient) -> None:
    r = client.get("/static/styles.css", headers=_GZIP)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"


def test_json_endpoint_is_gzipped(client: TestClient) -> None:
    # /api/config carries the prompt catalogue — comfortably over the
    # minimum size.
    r = client.get("/api/config", headers=_GZIP)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"


def test_small_body_is_left_alone(client: TestClient) -> None:
    r = client.get("/healthz", headers=_GZIP)
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


def test_no_gzip_without_accept_encoding(client: TestClient) -> None:
    r = client.get("/", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers


def test_stored_photo_is_not_recompressed(
    client: TestClient, big_jpeg_bytes: bytes
) -> None:
    """A JPEG is already compressed; gzip would burn CPU for ~0 gain."""
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    files = [("files", ("a.jpg", big_jpeg_bytes, "image/jpeg"))]
    assert client.post(f"/api/sessions/{sid}/photos", files=files).status_code == 200
    r = client.get(f"/api/sessions/{sid}/photo/1", headers=_GZIP)
    assert r.status_code == 200
    assert len(r.content) > 1000  # large enough that gzip *would* kick in
    assert r.headers.get("content-encoding") != "gzip"
