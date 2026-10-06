"""Tests for POST /api/sessions/{id}/retry-missing — a stubbed hub that fails
one photo, then succeeds on retry. Synthetic images only (#168)."""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Dict, List, Set, Tuple
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient
from PIL import Image


def _jpeg(shade: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (shade, 90, 40)).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _hub_reply(text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = text
    resp.json.return_value = {"content": [{"type": "text", "text": text}]}
    return resp


class _StubHub:
    """Answers each unit with its photos' sequence numbers; a photo in
    ``failing`` raises a read timeout."""

    def __init__(self, photo_paths: List[Any]) -> None:
        self._by_data = {
            base64.b64encode(p.read_bytes()).decode("ascii"): n
            for n, p in enumerate(photo_paths, start=1)
        }
        self.failing: Set[int] = set()
        self.calls: List[List[int]] = []

    def __call__(self, url, json, timeout, headers):  # noqa: A002
        numbers = [
            self._by_data[b["source"]["data"]]
            for b in json["messages"][0]["content"]
        ]
        self.calls.append(numbers)
        if any(n in self.failing for n in numbers):
            raise requests.ReadTimeout("read timed out")
        return _hub_reply("\n".join(f"page {n}" for n in numbers))


def _wait_for(client: TestClient, sid: str, phase: str) -> Dict[str, Any]:
    deadline = time.time() + 5
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get(f"/api/sessions/{sid}/extract/status").json()
        if last["phase"] == phase:
            return last
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {phase}; last={last!r}")


def _partial_session(client: TestClient) -> Tuple[str, _StubHub]:
    """Upload 3 photos and extract with photo 2 failing."""
    client.app.state.webapp_config.extract_chunk_size = 1
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[
            ("files", (f"{n}.jpg", _jpeg(60 * n), "image/jpeg")) for n in (1, 2, 3)
        ],
    )
    hub = _StubHub(client.app.state.archive.get(sid).photo_paths())
    hub.failing = {2}
    with patch.object(client.app.state.ocr_client._session, "post", side_effect=hub):
        client.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})
        done = _wait_for(client, sid, "succeeded")
    assert done["missing_photos"] == ["02.jpg"]
    hub.calls.clear()
    return sid, hub


def test_retry_missing_reads_only_the_missing_photo_and_recollates(
    client: TestClient,
) -> None:
    sid, hub = _partial_session(client)
    hub.failing = set()
    with patch.object(client.app.state.ocr_client._session, "post", side_effect=hub):
        r = client.post(f"/api/sessions/{sid}/retry-missing")
        assert r.status_code == 200
        # The route leaves the take `queued` before it answers, so the next
        # `succeeded` is the retry's own.
        assert r.json()["phase"] in {"queued", "running", "succeeded"}
        body = _wait_for(client, sid, "succeeded")

    assert hub.calls == [[2]]
    assert body["missing_photos"] == []
    assert body["extracted"] == "page 1\npage 2\npage 3"
    assert body["retry_error"] is None
    assert client.get(f"/api/sessions/{sid}/text").json()["extracted"] == (
        "page 1\npage 2\npage 3"
    )


def test_retry_missing_failing_again_keeps_the_partial_text(
    client: TestClient,
) -> None:
    sid, hub = _partial_session(client)
    with patch.object(client.app.state.ocr_client._session, "post", side_effect=hub):
        r = client.post(f"/api/sessions/{sid}/retry-missing", params={"wait": "true"})
    assert r.status_code == 502
    assert "still working" in r.json()["detail"]

    status = client.get(f"/api/sessions/{sid}/extract/status").json()
    assert status["missing_photos"] == ["02.jpg"]
    assert "[missing: photo 2" in status["extracted"]
    assert "still working" in status["retry_error"]
    # The take is still a success, not flipped to failed.
    assert status["extract_succeeded"] is True
    assert status["phase"] == "succeeded"


def test_retry_missing_wait_returns_the_single_shot_shape(client: TestClient) -> None:
    sid, hub = _partial_session(client)
    hub.failing = set()
    with patch.object(client.app.state.ocr_client._session, "post", side_effect=hub):
        r = client.post(f"/api/sessions/{sid}/retry-missing", params={"wait": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["text"] == "page 1\npage 2\npage 3"
    assert body["missing_photos"] == []
    assert hub.calls == [[2]]


def test_retry_missing_nothing_missing_is_400(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    r = client.post(f"/api/sessions/{sid}/retry-missing")
    assert r.status_code == 400
    assert "nothing to retry" in r.json()["detail"]


def test_retry_missing_unknown_session_is_404(client: TestClient) -> None:
    assert client.post("/api/sessions/nope/retry-missing").status_code == 404
