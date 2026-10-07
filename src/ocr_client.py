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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Third-party imports
import requests

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)


# Per-request read timeout. A 4-photo gemini_flash call was observed at
# 343 s on the hub (#166), so the old 180 s threw away answers that were
# still coming. Units are smaller now, but the hub serializes Gemini calls,
# so a queued unit's clock also covers its wait for the lock.
DEFAULT_TIMEOUT = 420.0
# Connect timeout: a hub that is down refuses instantly; one that does not
# accept the TCP connection within this window is treated as down too.
DEFAULT_CONNECT_TIMEOUT = 5.0
# Overall wall-clock budget for one extract() run, across every unit and
# retry. Units still unread when it runs out are reported missing.
DEFAULT_RUN_BUDGET = 900.0
# Hub requests in flight at once for one run.
DEFAULT_CONCURRENCY = 4
# Hub backends that run one call at a time (local-llm-hub README: "Gemini
# calls are serialized"). Parallel units would only queue behind the hub's
# lock with their read timeouts already running, so they go one by one.
SERIALIZED_BACKENDS = frozenset({"gemini"})
DEFAULT_CHUNK_SIZE = 1
DEFAULT_CHUNK_OVERLAP = 1
# Seam dedup between adjacent units: at most this many trailing lines of one
# unit are compared with the head of the next. A stated guess, not measured:
# it must cover the lines one overlapping photo repeats, and a larger window
# risks dropping genuinely repeated lines (table rows, repeated headers).
SEAM_MAX_LINES = 12
# Two seam lines count as the same line at or above this difflib ratio. Also a
# guess, not measured: loose enough to absorb an OCR slip or two between the
# two reads of one line, tight enough that distinct lines are kept (a wrong
# match silently deletes text).
SEAM_LINE_SIMILARITY = 0.92
# Vision models need a generous budget for long documents — voice
# polish needed 16k for reasoning-heavy paths; OCR can produce equally
# long output for a 20-photo email screenshot sequence.
DEFAULT_MAX_TOKENS = 16384


class OcrError(Exception):
    """Raised when the LLM hub is unreachable or returns an error."""


class HubDownError(OcrError):
    """The hub refused or never accepted the connection."""


class HubTimeoutError(OcrError):
    """The hub accepted the request but had not answered within the timeout."""


@dataclass
class ExtractPolicy:
    """Timeouts, parallelism and retry for one extract() run."""

    request_timeout_s: float = DEFAULT_TIMEOUT
    run_budget_s: float = DEFAULT_RUN_BUDGET
    concurrency: int = DEFAULT_CONCURRENCY
    # Model for the one retry of a failed unit. None retries on the same model.
    fallback_model: Optional[str] = None


@dataclass
class OcrResult:
    extracted_text: str
    model: str
    request_payload: dict
    response_payload: dict
    # Photo filenames no successful unit covered. Empty on a complete run.
    missing_photos: List[str] = field(default_factory=list)


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
        # Units run on worker threads; size the pool so concurrent hub
        # calls never wait on (or discard) a pooled connection.
        adapter = requests.adapters.HTTPAdapter(pool_maxsize=16)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._backends: Dict[str, str] = {}

    def close(self) -> None:
        self._session.close()

    def _backend(self, model: str) -> Optional[str]:
        """The hub backend serving ``model`` (from `GET /v1/models`), cached.

        Returns None when the hub can't say; the caller then keeps the
        configured concurrency.
        """
        if model not in self._backends:
            try:
                r = self._session.get(self.base_url + "/v1/models", timeout=5.0)
                r.raise_for_status()
                self._backends = {
                    str(m.get("id")): str(m.get("backend") or "")
                    for m in r.json().get("data", [])
                }
            except (requests.RequestException, ValueError, AttributeError) as exc:
                logger.info(f"ℹ️  could not read hub backends ({exc})")
                return None
        return self._backends.get(model) or None

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
        policy: Optional[ExtractPolicy] = None,
    ) -> OcrResult:
        """Send ``image_paths`` through the hub for OCR. Returns the extracted
        text plus the raw request/response payloads for archival.

        The user message contains ONLY images, no text instruction — the
        system prompt carries all the rules. This prevents the
        user-content channel from being interpreted as instructions by
        accident.

        Multi-photo takes are split into units of ``chunk_size`` photos
        (one-photo overlap when ``chunk_size`` > 1) that run up to
        ``policy.concurrency`` at a time. A failed unit is retried once
        (on ``policy.fallback_model`` when set). Python joins the unit
        outputs in sequence order and removes duplicate lines at each
        seam; there is intentionally no second LLM stitch pass.

        If some units still fail, the run returns the rest with a marker
        line where each missing photo belongs and lists them in
        ``OcrResult.missing_photos``. Only a run where every unit fails
        raises, with the first unit's error.
        """
        if not image_paths:
            raise OcrError("no images to extract from")
        if chunk_size < 1:
            raise OcrError("chunk_size must be >= 1")
        if policy is None:
            policy = ExtractPolicy(request_timeout_s=self.timeout)

        units = _chunk_paths(image_paths, chunk_size=chunk_size)
        total = len(units)
        finished, workers = self._run_units(
            list(enumerate(units, start=1)), total, model, system, max_tokens,
            policy, progress_callback,
        )
        if all(o.result is None for o in finished):
            raise finished[0].error
        if total == 1:
            return finished[0].result

        text, missing = _collate(image_paths, finished)

        return OcrResult(
            extracted_text=text,
            model=model,
            request_payload={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "chunk_size": chunk_size,
                "chunk_overlap": DEFAULT_CHUNK_OVERLAP if chunk_size > 1 else 0,
                "concurrency": workers,
                "request_timeout_s": policy.request_timeout_s,
                "run_budget_s": policy.run_budget_s,
                "fallback_model": policy.fallback_model,
                "chunks": [
                    {
                        "index": i,
                        "images": [p.name for p in unit],
                    }
                    for i, unit in enumerate(units, start=1)
                ],
            },
            response_payload={
                "chunks": [o.archive_entry() for o in finished],
                "missing_photos": [p.name for p in missing],
                "merge": {
                    "strategy": "python-overlap-line-dedup",
                    "llm_stitch_call": False,
                },
            },
            missing_photos=[p.name for p in missing],
        )

    def retry_missing(
        self,
        image_paths: List[Path],
        request_payload: dict,
        response_payload: dict,
        system: str,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        policy: Optional[ExtractPolicy] = None,
    ) -> OcrResult:
        """Re-read only the units a finished ``extract()`` left unread.

        ``request_payload`` / ``response_payload`` are the archived ones of
        that run. Units that succeeded keep their archived answer; each
        failed unit holding a missing photo is read again (same model, same
        ``system``), then every unit text is re-joined in sequence order.
        Raises ``OcrError`` when the archive has no per-unit entries (a take
        from before they were kept — use a full redo) or when no retried
        unit could be read, so the caller keeps the text it already has.
        """
        if policy is None:
            policy = ExtractPolicy(request_timeout_s=self.timeout)
        by_name = {p.name: p for p in image_paths}
        unit_specs = request_payload.get("chunks")
        entries = response_payload.get("chunks")
        if (
            not isinstance(unit_specs, list)
            or not isinstance(entries, list)
            or not unit_specs
            or len(unit_specs) != len(entries)
        ):
            raise OcrError(
                "this take has no per-photo archive to retry from; use Redo"
            )
        try:
            units = [[by_name[n] for n in spec["images"]] for spec in unit_specs]
        except (KeyError, TypeError) as exc:
            raise OcrError(
                f"photo {exc} is no longer in this take; use Redo"
            ) from exc

        model = str(request_payload.get("model") or "")
        max_tokens = int(request_payload.get("max_tokens") or DEFAULT_MAX_TOKENS)
        total = len(units)
        missing_before = set(response_payload.get("missing_photos") or [])

        outcomes: Dict[int, _UnitOutcome] = {}
        to_retry = []
        for index, (unit, entry) in enumerate(zip(units, entries), start=1):
            if "response" in entry:
                outcomes[index] = _UnitOutcome(
                    index=index, paths=unit, model=str(entry.get("model") or model),
                    attempts=int(entry.get("attempts") or 1),
                    result=OcrResult(
                        extracted_text=_extract_text(entry["response"]),
                        model=str(entry.get("model") or model),
                        request_payload={},
                        response_payload=entry["response"],
                    ),
                )
            elif any(p.name in missing_before for p in unit):
                to_retry.append((index, unit))
            else:
                outcomes[index] = _failed_from_entry(index, unit, entry, model)
        if not to_retry:
            raise OcrError("nothing to retry in this take")

        retried, _ = self._run_units(
            to_retry, total, model, system, max_tokens, policy, progress_callback,
        )
        if all(o.result is None for o in retried):
            raise retried[0].error
        for o in retried:
            outcomes[o.index] = o

        finished = [outcomes[i] for i in range(1, total + 1)]
        text, missing = _collate(image_paths, finished)
        logger.info(
            f"ℹ️  OCR retry read {sum(o.result is not None for o in retried)} of "
            f"{len(retried)} unit(s); {len(missing)} photo(s) still missing"
        )
        return OcrResult(
            extracted_text=text,
            model=model,
            request_payload=request_payload,
            response_payload={
                **response_payload,
                "chunks": [o.archive_entry() for o in finished],
                "missing_photos": [p.name for p in missing],
            },
            missing_photos=[p.name for p in missing],
        )

    def _run_units(
        self,
        indexed_units: List[tuple],
        total: int,
        model: str,
        system: str,
        max_tokens: int,
        policy: ExtractPolicy,
        progress_callback: Optional[Callable[[int, int], None]],
    ) -> tuple:
        """Run ``(index, paths)`` units under one wall-clock budget.

        Returns ``(outcomes in index order, workers used)``. ``index`` is the
        unit's 1-based place among ``total`` units of the whole take; the
        progress callback counts only the units run here.
        """
        deadline = time.monotonic() + policy.run_budget_s
        count = len(indexed_units)
        workers = max(1, min(policy.concurrency, count))
        if workers > 1 and self._backend(model) in SERIALIZED_BACKENDS:
            workers = 1
        logger.info(
            f"🔍 OCR model={model} units={count}/{total} "
            f"concurrency={workers} request_timeout={policy.request_timeout_s:.0f}s "
            f"budget={policy.run_budget_s:.0f}s"
        )
        outcomes: List[_UnitOutcome] = []
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="ocr-unit"
        ) as pool:
            futures = [
                pool.submit(
                    self._extract_unit,
                    paths, index, total, model, system, max_tokens,
                    policy, deadline,
                )
                for index, paths in indexed_units
            ]
            for done, future in enumerate(as_completed(futures), start=1):
                outcomes.append(future.result())
                if progress_callback is not None:
                    progress_callback(done, count)
        outcomes.sort(key=lambda o: o.index)
        return outcomes, workers

    def _extract_unit(
        self,
        paths: List[Path],
        index: int,
        total: int,
        model: str,
        system: str,
        max_tokens: int,
        policy: ExtractPolicy,
        deadline: float,
    ) -> "_UnitOutcome":
        """Run one unit: first attempt, then one retry, inside the run budget."""
        attempt_models = [model, policy.fallback_model or model]
        error: Optional[OcrError] = None
        for attempt, attempt_model in enumerate(attempt_models, start=1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = OcrError(
                    f"run budget of {policy.run_budget_s:.0f}s ran out before "
                    f"photos {_names(paths)} were read"
                )
                break
            logger.info(
                f"🔍 OCR unit {index}/{total} attempt {attempt} "
                f"model={attempt_model} photos={_names(paths)}"
            )
            try:
                result = self._extract_single_request(
                    image_paths=paths,
                    model=attempt_model,
                    system=system,
                    max_tokens=max_tokens,
                    timeout=min(policy.request_timeout_s, remaining),
                )
            except OcrError as exc:
                error = exc
                logger.warning(
                    f"⚠️  OCR unit {index}/{total} attempt {attempt} failed: {exc}"
                )
                continue
            return _UnitOutcome(
                index=index, paths=paths, model=attempt_model,
                attempts=attempt, result=result,
            )
        return _UnitOutcome(
            index=index, paths=paths, model=model,
            attempts=len(attempt_models), error=error,
        )

    def _extract_single_request(
        self,
        image_paths: List[Path],
        model: str,
        system: str,
        max_tokens: int,
        timeout: Optional[float] = None,
    ) -> OcrResult:
        """Send one hub request containing ``image_paths``."""
        if not image_paths:
            raise OcrError("no images to extract from")
        read_timeout = self.timeout if timeout is None else timeout

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
                timeout=(DEFAULT_CONNECT_TIMEOUT, read_timeout),
                headers={"Content-Type": "application/json"},
            )
        except requests.ReadTimeout as exc:
            raise HubTimeoutError(
                f"LLM hub still working on photos {_names(image_paths)} after "
                f"the {read_timeout:.0f}s request timeout (model={model}); "
                f"answer abandoned"
            ) from exc
        except requests.ConnectionError as exc:
            raise HubDownError(
                f"could not reach LLM hub at {url} — is it running? ({exc})"
            ) from exc
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


@dataclass
class _UnitOutcome:
    """One unit's final state after its attempts."""

    index: int
    paths: List[Path]
    model: str
    attempts: int
    result: Optional[OcrResult] = None
    error: Optional[OcrError] = None

    def archive_entry(self) -> dict:
        entry: dict = {
            "index": self.index,
            "model": self.model,
            "attempts": self.attempts,
        }
        if self.result is not None:
            entry["response"] = self.result.response_payload
        else:
            entry["error"] = str(self.error)
        return entry


def _names(paths: List[Path]) -> str:
    return ", ".join(p.name for p in paths)


def _failed_from_entry(
    index: int, paths: List[Path], entry: dict, model: str
) -> "_UnitOutcome":
    """Rebuild a failed unit's outcome from its archived entry."""
    return _UnitOutcome(
        index=index, paths=paths, model=str(entry.get("model") or model),
        attempts=int(entry.get("attempts") or 1),
        error=OcrError(str(entry.get("error") or "unit could not be read")),
    )


def _collate(
    image_paths: List[Path], outcomes: List["_UnitOutcome"]
) -> "tuple[str, List[Path]]":
    """Join unit outcomes in sequence order into the take's text.

    A failed unit leaves a marker line where its unread photos belong (photos
    another unit's overlap covered are not unread). Returns the joined text
    and the photos no successful unit covered.
    """
    positions = {p: n for n, p in enumerate(image_paths, start=1)}
    covered = {p for o in outcomes if o.result is not None for p in o.paths}
    missing = [p for p in image_paths if p not in covered]
    segments: List[str] = []
    for o in outcomes:
        if o.result is not None:
            segments.append(o.result.extracted_text)
            continue
        gap = [p for p in o.paths if p not in covered]
        if gap:
            segments.append(_missing_marker(gap, positions))
    if missing:
        logger.warning(
            f"⚠️  OCR returned partial text: {len(missing)} of "
            f"{len(image_paths)} photo(s) missing "
            f"({', '.join(p.name for p in missing)})"
        )
    return _join_chunk_texts(segments), missing


def _missing_marker(gap: List[Path], positions: dict) -> str:
    """The line that stands in for photos no unit could read."""
    numbers = ", ".join(str(positions[p]) for p in gap)
    label = "photo" if len(gap) == 1 else "photos"
    return f"[missing: {label} {numbers} ({_names(gap)}) could not be read]"


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
    """How many chunks ``_chunk_paths`` produces for ``photo_count`` photos at
    ``chunk_size`` (the webapp progress bar's total). Asks the real chunker, so
    the prediction cannot desync from the number of hub calls."""
    if photo_count <= 0:
        return 0
    return len(_chunk_paths(list(range(photo_count)), chunk_size))


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
    max_window = min(SEAM_MAX_LINES, len(left_lines), len(right_lines))
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
    ratio = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    return ratio >= SEAM_LINE_SIMILARITY


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
