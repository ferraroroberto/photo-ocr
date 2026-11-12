"""Tests for the extract / redo endpoints — mocked hub."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.extract_job import set_extract_progress, settle_interrupted_extracts
from src.ocr_client import OcrError, OcrResult


def _wait_for_phase(
    client: TestClient, session_id: str, phase: str, timeout_s: float = 2.0
) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        r = client.get(f"/api/sessions/{session_id}/extract/status")
        assert r.status_code == 200
        last = r.json()
        if last["phase"] == phase:
            return last
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {phase}; last={last!r}")


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
        body = _wait_for_phase(client, sid, "succeeded")
    assert r.status_code == 200
    assert r.json()["phase"] in {"queued", "running", "succeeded"}
    assert body["extracted"] == "hello world"
    assert body["model"] == "gemini_flash"
    assert body["reused"] is False
    mock_extract.assert_called_once()


def test_extract_applies_default_language_hint(client: TestClient, jpeg_bytes: bytes) -> None:
    client.app.state.app_config.default_language_hint = "Spanish"
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    fake_result = OcrResult(
        extracted_text="hola mundo",
        model="gemini_flash",
        request_payload={"model": "gemini_flash", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": "hola mundo"}]},
    )
    with patch.object(
        client.app.state.ocr_client,
        "extract",
        return_value=fake_result,
    ) as mock_extract:
        client.post(f"/api/sessions/{sid}/extract", json={"model": "gemini_flash"})
        _wait_for_phase(client, sid, "succeeded")
    called_system = mock_extract.call_args.kwargs["system"]
    assert called_system.startswith("These photos are likely in Spanish.\n\n")


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
        body = _wait_for_phase(client, sid, "failed")
    assert r.status_code == 200
    assert "hub down" in body["error"]


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
        _wait_for_phase(client, sid, "succeeded")

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
        body = r.json()
        assert body["phase"] == "succeeded"
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
        body = _wait_for_phase(client, sid, "succeeded")
        assert r.status_code == 200
        assert body["extracted"] == "second run"
        assert body["reused"] is False
        mock_redo.assert_called_once()


def _fake_read(text: str) -> OcrResult:
    return OcrResult(
        extracted_text=text,
        model="gemini_flash",
        request_payload={"model": "gemini_flash", "images": ["01.jpg"]},
        response_payload={"content": [{"type": "text", "text": text}]},
    )


def test_extract_during_an_inflight_redo_leaves_the_phase_active(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    """/extract over a take whose earlier text is still current must not stamp
    it succeeded while a redo is still reading."""
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_fake_read("first run")
    ):
        client.post(f"/api/sessions/{sid}/extract", json={})
        _wait_for_phase(client, sid, "succeeded")

    started = threading.Event()
    release = threading.Event()

    def _blocked_read(**_kwargs: object) -> OcrResult:
        started.set()
        assert release.wait(timeout=5), "test never released the redo"
        return _fake_read("second run")

    with patch.object(client.app.state.ocr_client, "extract", side_effect=_blocked_read):
        try:
            client.post(f"/api/sessions/{sid}/redo", json={})
            assert started.wait(timeout=5), "the redo never reached the hub call"
            mid = client.get(f"/api/sessions/{sid}/extract/status").json()
            assert mid["in_flight"] is True

            r = client.post(f"/api/sessions/{sid}/extract", json={})
            assert r.status_code == 200
            assert r.json()["in_flight"] is True
            assert r.json()["phase"] in {"queued", "running", "merging"}
            after = client.get(f"/api/sessions/{sid}/extract/status").json()
            assert after["in_flight"] is True
            assert after["phase"] in {"queued", "running", "merging"}
        finally:
            release.set()
        body = _wait_for_phase(client, sid, "succeeded")
    assert body["extracted"] == "second run"
    assert body["reused"] is False


def test_extract_re_reads_when_a_photo_was_added_after_the_read(
    client: TestClient, jpeg_bytes: bytes, big_jpeg_bytes: bytes
) -> None:
    """A photo added to an already-extracted take must be read, not answered
    with the cached text that predates it."""
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_fake_read("one photo")
    ):
        client.post(f"/api/sessions/{sid}/extract", json={})
        _wait_for_phase(client, sid, "succeeded")

    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("b.jpg", big_jpeg_bytes, "image/jpeg"))],
    )
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_fake_read("two photos")
    ) as mock_extract:
        client.post(f"/api/sessions/{sid}/extract", json={})
        body = _wait_for_phase(client, sid, "succeeded")
    mock_extract.assert_called_once()
    assert len(mock_extract.call_args.kwargs["image_paths"]) == 2
    assert body["extracted"] == "two photos"
    assert body["reused"] is False


def test_crashed_extract_job_settles_the_take_as_failed(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    """A job that raises something other than OcrError must not leave the
    take queued/running forever (it would block /extract and /retry-missing)."""
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    with patch.object(
        client.app.state.ocr_client, "extract", side_effect=RuntimeError("boom")
    ):
        client.post(f"/api/sessions/{sid}/extract", json={})
        body = _wait_for_phase(client, sid, "failed")
    assert "boom" in body["error"]

    # And the take is startable again.
    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_fake_read("recovered")
    ):
        client.post(f"/api/sessions/{sid}/extract", json={})
        body = _wait_for_phase(client, sid, "succeeded")
    assert body["extracted"] == "recovered"


def test_boot_sweep_settles_takes_interrupted_mid_extract(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    """No job survives a restart: a persisted queued/running/merging phase is
    settled on boot — failed when there is no text, succeeded (text intact)
    when a redo or retry died over an earlier good read."""
    archive = client.app.state.archive
    never_read = client.post("/api/sessions", json={}).json()["session_id"]
    redo_died = client.post("/api/sessions", json={}).json()["session_id"]
    set_extract_progress(archive.get(never_read), phase="running", model="gemini_flash")
    done = archive.get(redo_died)
    done.write_extracted(
        "good text", model="gemini_flash", request_payload={},
        response_payload={}, prompt_id=None,
    )
    set_extract_progress(done, phase="merging")

    assert settle_interrupted_extracts(archive) == 2

    failed = client.get(f"/api/sessions/{never_read}/extract/status").json()
    assert failed["phase"] == "failed"
    assert failed["extract_succeeded"] is False
    assert "restart" in failed["error"]
    kept = client.get(f"/api/sessions/{redo_died}/extract/status").json()
    assert kept["phase"] == "succeeded"
    assert kept["extracted"] == "good text"
    assert "restart" in kept["retry_error"]
    assert settle_interrupted_extracts(archive) == 0


def test_photo_added_mid_read_leaves_the_text_stale(
    client: TestClient, jpeg_bytes: bytes
) -> None:
    """The fingerprint covers the photos the run read: one that slipped in
    while it ran must not make the old text read as current."""
    from src.archive import PhotoMeta

    sid = client.post("/api/sessions", json={}).json()["session_id"]
    client.post(
        f"/api/sessions/{sid}/photos",
        files=[("files", ("a.jpg", jpeg_bytes, "image/jpeg"))],
    )
    started = threading.Event()
    release = threading.Event()

    def _read_then_gain_a_photo(**_kwargs: object) -> OcrResult:
        started.set()
        assert release.wait(timeout=5), "test never released the read"
        return _fake_read("one photo")

    with patch.object(
        client.app.state.ocr_client, "extract", side_effect=_read_then_gain_a_photo
    ):
        try:
            client.post(f"/api/sessions/{sid}/extract", json={})
            assert started.wait(timeout=5), "the read never reached the hub call"
            # The routes refuse this now; write the meta directly to cover a
            # change that gets past them.
            session = client.app.state.archive.get(sid)
            session.record_photo(
                PhotoMeta(
                    sequence_index=2, path="02.jpg", width=10, height=10, bytes_on_disk=5
                )
            )
            session.write_meta()
        finally:
            release.set()
        _wait_for_phase(client, sid, "succeeded")

    with patch.object(
        client.app.state.ocr_client, "extract", return_value=_fake_read("two photos")
    ) as mock_extract:
        r = client.post(f"/api/sessions/{sid}/extract", json={})
        assert r.json()["reused"] is False
        _wait_for_phase(client, sid, "succeeded")
    mock_extract.assert_called_once()
