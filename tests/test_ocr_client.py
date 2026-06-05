"""Tests for src/ocr_client.py — mocked hub responses."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ocr_client import OcrClient, OcrError, _extract_text
from src.ocr_client import _chunk_paths, _join_chunk_texts


def _mock_response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_body)
    resp.json.return_value = json_body
    return resp


def test_extract_returns_text(tmp_path: Path, jpeg_bytes: bytes) -> None:
    photo = tmp_path / "01.jpg"
    photo.write_bytes(jpeg_bytes)

    client = OcrClient(base_url="http://127.0.0.1:8000")
    with patch.object(
        client._session,
        "post",
        return_value=_mock_response(
            {"content": [{"type": "text", "text": "Hello world"}]}
        ),
    ) as mock_post:
        result = client.extract(
            image_paths=[photo],
            model="gemini_flash",
            system="dummy system",
        )
    assert result.extracted_text == "Hello world"
    assert result.model == "gemini_flash"
    mock_post.assert_called_once()
    # Payload uses Anthropic shape with image content blocks.
    sent = mock_post.call_args.kwargs["json"]
    assert sent["model"] == "gemini_flash"
    assert sent["system"] == "dummy system"
    assert sent["messages"][0]["content"][0]["type"] == "image"
    # Archive payload strips base64 image data and stores filenames only.
    assert "images" in result.request_payload
    assert result.request_payload["images"] == ["01.jpg"]
    assert "data" not in str(result.request_payload)


def test_chunk_paths_uses_one_photo_overlap() -> None:
    photos = [Path(f"{i:02d}.jpg") for i in range(1, 51)]
    assert _chunk_paths(photos[:1], chunk_size=4) == [photos[:1]]
    assert _chunk_paths(photos[:4], chunk_size=4) == [photos[:4]]
    assert _chunk_paths(photos[:5], chunk_size=4) == [photos[:4], photos[3:5]]
    assert _chunk_paths(photos[:14], chunk_size=4) == [
        photos[0:4],
        photos[3:7],
        photos[6:10],
        photos[9:13],
        photos[12:14],
    ]
    assert len(_chunk_paths(photos, chunk_size=4)) == 17


def test_extract_chunks_large_take_and_dedups_seam(
    tmp_path: Path, jpeg_bytes: bytes
) -> None:
    photos = []
    for idx in range(1, 6):
        photo = tmp_path / f"{idx:02d}.jpg"
        photo.write_bytes(jpeg_bytes)
        photos.append(photo)

    client = OcrClient(base_url="http://127.0.0.1:8000")
    responses = [
        _mock_response(
            {"content": [{"type": "text", "text": "alpha\nshared line"}]}
        ),
        _mock_response(
            {"content": [{"type": "text", "text": "shared line\nomega"}]}
        ),
    ]
    progress = []
    with patch.object(client._session, "post", side_effect=responses) as mock_post:
        result = client.extract(
            image_paths=photos,
            model="gemini_flash",
            system="dummy system",
            chunk_size=4,
            progress_callback=lambda done, total: progress.append((done, total)),
        )

    assert result.extracted_text == "alpha\nshared line\nomega"
    assert mock_post.call_count == 2
    first_payload = mock_post.call_args_list[0].kwargs["json"]
    second_payload = mock_post.call_args_list[1].kwargs["json"]
    assert len(first_payload["messages"][0]["content"]) == 4
    assert len(second_payload["messages"][0]["content"]) == 2
    assert result.request_payload["chunks"] == [
        {"index": 1, "images": ["01.jpg", "02.jpg", "03.jpg", "04.jpg"]},
        {"index": 2, "images": ["04.jpg", "05.jpg"]},
    ]
    assert result.response_payload["merge"]["llm_stitch_call"] is False
    assert progress == [(1, 2), (2, 2)]


def test_join_chunk_texts_fuzzy_dedups_boundary() -> None:
    assert (
        _join_chunk_texts(
            [
                "first line\nInvoice total: EUR 12.50",
                "Invoice total:  EUR 12.50\nlast line",
            ]
        )
        == "first line\nInvoice total: EUR 12.50\nlast line"
    )


def test_extract_hub_unreachable_wraps_error(tmp_path: Path, jpeg_bytes: bytes) -> None:
    photo = tmp_path / "01.jpg"
    photo.write_bytes(jpeg_bytes)
    client = OcrClient(base_url="http://127.0.0.1:8000")
    with patch.object(
        client._session,
        "post",
        side_effect=requests.ConnectionError("nope"),
    ):
        with pytest.raises(OcrError, match="could not reach LLM hub"):
            client.extract([photo], model="gemini_flash", system="x")


def test_extract_non_200_wraps_error(tmp_path: Path, jpeg_bytes: bytes) -> None:
    photo = tmp_path / "01.jpg"
    photo.write_bytes(jpeg_bytes)
    client = OcrClient(base_url="http://127.0.0.1:8000")
    bad = _mock_response({"detail": "bad model"}, status_code=400)
    with patch.object(client._session, "post", return_value=bad):
        with pytest.raises(OcrError, match="hub returned 400"):
            client.extract([photo], model="bogus", system="x")


def test_extract_rejects_empty_image_list() -> None:
    client = OcrClient()
    with pytest.raises(OcrError, match="no images"):
        client.extract([], model="gemini_flash", system="x")


def test_extract_detects_truncated_reasoning(tmp_path: Path, jpeg_bytes: bytes) -> None:
    photo = tmp_path / "01.jpg"
    photo.write_bytes(jpeg_bytes)
    client = OcrClient()
    body = {
        "content": [{"type": "text", "text": "<think>still thinking"}],
        "stop_reason": "max_tokens",
    }
    with patch.object(client._session, "post", return_value=_mock_response(body)):
        with pytest.raises(OcrError, match="token budget"):
            client.extract([photo], model="thinky_model", system="x")


def test_extract_text_strips_think_blocks() -> None:
    body = {
        "content": [
            {"type": "text", "text": "<think>noise</think>real answer"}
        ]
    }
    assert _extract_text(body) == "real answer"


def test_extract_text_handles_no_content() -> None:
    assert _extract_text({}) == ""
    assert _extract_text({"content": None}) == ""
    assert _extract_text({"content": []}) == ""


def test_is_reachable_returns_false_on_failure() -> None:
    client = OcrClient()
    with patch.object(
        client._session, "get", side_effect=requests.ConnectionError()
    ):
        assert client.is_reachable() is False


def test_is_reachable_returns_true_on_200() -> None:
    client = OcrClient()
    ok = MagicMock()
    ok.status_code = 200
    with patch.object(client._session, "get", return_value=ok):
        assert client.is_reachable() is True
