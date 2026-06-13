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
import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

# Third-party imports
import requests

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


DEFAULT_TIMEOUT = 180.0
DEFAULT_CHUNK_SIZE = 4
DEFAULT_CHUNK_OVERLAP = 1
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
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> OcrResult:
        """Send ``image_paths`` through the hub for OCR. Returns the extracted
        text plus the raw request/response payloads for archival.

        The user message contains ONLY images, no text instruction — the
        system prompt carries all the rules. This prevents the
        user-content channel from being interpreted as instructions by
        accident.

        Multi-photo takes are chunked into overlapped hub calls. The model
        still does the semantic merge inside each chunk; Python only joins
        adjacent chunk outputs and removes duplicate lines at the known
        overlap boundary. There is intentionally no second LLM stitch pass.
        """
        if not image_paths:
            raise OcrError("no images to extract from")
        if chunk_size < 1:
            raise OcrError("chunk_size must be >= 1")

        chunks = _chunk_paths(image_paths, chunk_size=chunk_size)
        if len(chunks) == 1:
            result = self._extract_single_request(
                image_paths=chunks[0],
                model=model,
                system=system,
                max_tokens=max_tokens,
            )
            if progress_callback is not None:
                progress_callback(1, 1)
            return result

        chunk_results: List[OcrResult] = []
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            logger.info(
                f"🔍 OCR chunk {index}/{total} model={model} photos={len(chunk)}"
            )
            chunk_results.append(
                self._extract_single_request(
                    image_paths=chunk,
                    model=model,
                    system=system,
                    max_tokens=max_tokens,
                )
            )
            if progress_callback is not None:
                progress_callback(index, total)

        extracted = _join_chunk_texts([r.extracted_text for r in chunk_results])
        return OcrResult(
            extracted_text=extracted,
            model=model,
            request_payload={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "chunk_size": chunk_size,
                "chunk_overlap": DEFAULT_CHUNK_OVERLAP if chunk_size > 1 else 0,
                "chunks": [
                    {
                        "index": i,
                        "images": [p.name for p in chunk],
                    }
                    for i, chunk in enumerate(chunks, start=1)
                ],
            },
            response_payload={
                "chunks": [
                    {
                        "index": i,
                        "response": r.response_payload,
                    }
                    for i, r in enumerate(chunk_results, start=1)
                ],
                "merge": {
                    "strategy": "python-overlap-line-dedup",
                    "llm_stitch_call": False,
                },
            },
        )

    def _extract_single_request(
        self,
        image_paths: List[Path],
        model: str,
        system: str,
        max_tokens: int,
    ) -> OcrResult:
        """Send one hub request containing ``image_paths``."""
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


def _chunk_paths(image_paths: List[Path], chunk_size: int) -> List[List[Path]]:
    """Split paths into chunks with a one-photo overlap between chunks."""
    if chunk_size < 1:
        raise OcrError("chunk_size must be >= 1")
    if len(image_paths) <= chunk_size:
        return [list(image_paths)]

    chunks: List[List[Path]] = []
    start = 0
    total = len(image_paths)
    while start < total:
        end = min(start + chunk_size, total)
        chunks.append(list(image_paths[start:end]))
        if end == total:
            break
        start = end - DEFAULT_CHUNK_OVERLAP if chunk_size > 1 else end
    return chunks


def chunk_count(photo_count: int, chunk_size: int) -> int:
    """Predict how many chunks ``_chunk_paths`` will produce for
    ``photo_count`` photos at ``chunk_size``.

    Lives next to ``_chunk_paths`` and reuses the same
    ``DEFAULT_CHUNK_OVERLAP`` constant so the prediction (used by the
    webapp progress bar) can never silently desync from the actual number
    of hub calls. Mirrors ``_chunk_paths``' stepping: the first chunk
    covers ``chunk_size`` photos, each subsequent chunk advances by
    ``chunk_size - DEFAULT_CHUNK_OVERLAP`` (no overlap when ``chunk_size``
    is 1, matching ``_chunk_paths``).
    """
    if photo_count <= 0:
        return 0
    if chunk_size <= 1:
        return photo_count
    if photo_count <= chunk_size:
        return 1
    step = chunk_size - DEFAULT_CHUNK_OVERLAP
    count = 1
    covered = chunk_size
    while covered < photo_count:
        count += 1
        covered += step
    return count


def _join_chunk_texts(texts: List[str]) -> str:
    """Join chunk outputs and remove duplicate lines at adjacent seams."""
    merged = ""
    for text in texts:
        candidate = text.strip()
        if not candidate:
            continue
        if not merged:
            merged = candidate
            continue
        merged = _join_two_chunks(merged, candidate)
    return merged.strip()


def _join_two_chunks(left: str, right: str) -> str:
    left_lines = left.splitlines()
    right_lines = right.splitlines()
    overlap = _find_line_overlap(left_lines, right_lines)
    if overlap:
        right_lines = right_lines[overlap:]
    return "\n".join(left_lines + right_lines).strip()


def _find_line_overlap(left_lines: List[str], right_lines: List[str]) -> int:
    """Return how many leading right lines duplicate trailing left lines."""
    max_window = min(12, len(left_lines), len(right_lines))
    for size in range(max_window, 0, -1):
        left_tail = left_lines[-size:]
        right_head = right_lines[:size]
        if _line_runs_match(left_tail, right_head):
            return size
    return 0


def _line_runs_match(left_lines: List[str], right_lines: List[str]) -> bool:
    return all(
        _lines_match(left, right)
        for left, right in zip(left_lines, right_lines)
    )


def _lines_match(left: str, right: str) -> bool:
    left_norm = _normalise_line(left)
    right_norm = _normalise_line(right)
    if not left_norm or not right_norm:
        return left_norm == right_norm
    if left_norm == right_norm:
        return True
    return difflib.SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.92


def _normalise_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().casefold()


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
