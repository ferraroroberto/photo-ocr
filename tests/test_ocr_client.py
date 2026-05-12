"""Tests for src/ocr_client.py — mocked hub responses."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ocr_client import OcrClient, OcrError, _extract_text


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
