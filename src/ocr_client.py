"""Local-LLM-hub client for OCR extraction.

Sends 1..N photos to `local-llm-hub` (Anthropic-shaped `/v1/messages`
endpoint) with image content blocks. The hub routes to whichever
vision-capable model the caller picked. Clients address the model via
a stable alias the hub maps to the current display_name:
`gemini_flash` / `gemini_pro` / `gemini_lite` for the Google AI Pro
path, and `claude_haiku` / `claude_sonnet` / `claude_opus` for the
Anthropic path. When the vendor ships a new version, only the hub's
`display_name` needs updating — these aliases stay the same.

The hub itself lives in `E:\\automation\\local-llm-hub\\` and binds to
`http://127.0.0.1:8000` by default. The base URL is configurable via
`config/webapp_config.json` so it can also point at a remote hub.
"""

from __future__ import annotations

# Standard library imports
import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

# Third-party imports
import requests

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


DEFAULT_TIMEOUT = 180.0
# Vision models need a generous budget for long documents — voice
# polish needed 16k for reasoning-heavy paths; OCR can produce equally
# long output for a 20-photo email screenshot sequence.
DEFAULT_MAX_TOKENS = 16384


class OcrError(Exception):
    """Raised when the LLM hub is unreachable or returns an error."""


@dataclass
class OcrResult:
    extracted_text: str
    model: str
    request_payload: dict
    response_payload: dict


class OcrClient:
    """Thin wrapper around `local-llm-hub`'s `/v1/messages` endpoint
    for vision (image) inputs."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def is_reachable(self) -> bool:
        """Quick liveness check — the hub answers `GET /v1/models` on success."""
        try:
            r = self._session.get(self.base_url + "/v1/models", timeout=2.0)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def extract(
        self,
        image_paths: List[Path],
        model: str,
        system: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> OcrResult:
        """Send ``image_paths`` through the hub for OCR. Returns the extracted
        text plus the raw request/response payloads for archival.

        The user message contains ONLY images, no text instruction — the
        system prompt carries all the rules. This prevents the
        user-content channel from being interpreted as instructions by
        accident.
        """
        if not image_paths:
            raise OcrError("no images to extract from")

        content_blocks = []
        for p in image_paths:
            try:
                raw = p.read_bytes()
            except OSError as exc:
                raise OcrError(f"could not read {p}: {exc}") from exc
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(raw).decode("ascii"),
                    },
                }
            )

        url = self.base_url + "/v1/messages"
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": content_blocks,
                }
            ],
        }

        logger.info(
            f"🔍 POST {url} model={model} photos={len(image_paths)}"
        )
        try:
            response = self._session.post(
                url,
                json=payload,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        except requests.RequestException as exc:
            raise OcrError(
                f"could not reach LLM hub at {url}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise OcrError(
                f"hub returned {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise OcrError(f"hub returned non-JSON: {exc}") from exc

        extracted = _extract_text(body)
        stop_reason = body.get("stop_reason")
        if _OPEN_THINK_RE.search(extracted) or (
            not extracted and stop_reason == "max_tokens"
        ):
            raise OcrError(
                f"model exhausted its token budget while reasoning "
                f"(model={model}, stop_reason={stop_reason}). Try a "
                f"non-thinking vision model like gemini_flash."
            )

        # Empty output is a valid outcome — the prompt asks the model to
        # emit nothing when no readable text is present. Don't error.
        return OcrResult(
            extracted_text=extracted,
            model=model,
            request_payload=_payload_for_archive(payload, image_paths),
            response_payload=body,
        )


def _extract_text(body: dict) -> str:
    """Pull the assistant's text out of an Anthropic-shaped response.

    Strips any complete ``<think>...</think>`` blocks the hub didn't catch
    (defence in depth — the hub already does this, but a future shape
    change shouldn't leak reasoning into the OCR output). Unterminated
    ``<think>`` blocks are left intact so the caller can detect the
    mid-reasoning truncation case and surface a clearer error.
    """
    content = body.get("content")
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return _THINK_BLOCK_RE.sub("", "".join(parts)).strip()


def _payload_for_archive(payload: dict, image_paths: List[Path]) -> dict:
    """Return an archive-safe copy of the request payload.

    The wire payload contains base64-encoded images (potentially many
    MB each); storing that on disk would inflate the archive far past
    the photos themselves. Replace the image content blocks with a
    pointer list — the actual JPEGs are right next to the JSON.
    """
    return {
        "model": payload.get("model"),
        "max_tokens": payload.get("max_tokens"),
        "system": payload.get("system"),
        "images": [p.name for p in image_paths],
    }
