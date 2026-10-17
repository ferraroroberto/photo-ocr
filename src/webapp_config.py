"""Webapp-specific configuration loader.

Lives separately from `app_config.py` because these settings are
authored from the web UI ("Save defaults" button) and persist across
runs. The CLI also reads this file so both surfaces share one source
of truth.

Bind address and port are *not* here — they live only in
`config/config.json`'s `webapp` section (`app.webapp.manager.load_config`),
read by both the tray and `launcher.py webapp`, so a port change is one
edit instead of two config files that can disagree.
"""

from __future__ import annotations

# Standard library imports
import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import List, Optional, get_origin, get_type_hints
from urllib.parse import urlencode, urlparse, urlunparse

# Local imports
from src.ocr_client import ExtractPolicy

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "webapp_config.json"
)
SAMPLE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "webapp_config.sample.json"
)

# Keys whose null/empty value means "use the default" rather than "coerce it".
_BLANK_MEANS_DEFAULT = frozenset(
    {"ocr_model_default", "ocr_models_available", "extract_fallback_model"}
)


def _sample_ocr_defaults() -> tuple[str, List[str]]:
    """Read the committed sample config to get the first-run OCR-model
    defaults. Keeps Python free of model-name literals so the list can
    evolve in JSON alone."""
    try:
        raw = json.loads(SAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            f"⚠️  Could not read sample config {SAMPLE_CONFIG_PATH} "
            f"({exc}); ocr defaults will be empty"
        )
        return "", []
    return (
        str(raw.get("ocr_model_default") or ""),
        list(raw.get("ocr_models_available") or []),
    )


@dataclass
class WebappConfig:
    """User-authored, persisted webapp settings."""

    ocr_model_default: str = field(
        default_factory=lambda: _sample_ocr_defaults()[0]
    )
    ocr_models_available: List[str] = field(
        default_factory=lambda: _sample_ocr_defaults()[1]
    )
    ocr_prompt_default: str = "verbatim-merge"
    llm_hub_url: str = "http://127.0.0.1:8000"
    history_retention_days: int = 30
    max_photos_per_session: int = 50
    max_photo_dimension_px: int = 2048
    extract_chunk_size: int = 1
    # Per-request hub timeout, whole-run budget, parallel hub calls and the
    # optional retry model for one extraction (see src/ocr_client.py).
    extract_request_timeout_s: float = 420.0
    extract_run_budget_s: float = 900.0
    extract_concurrency: int = 4
    # Model a failed unit retries on. Empty = retry on the same model.
    extract_fallback_model: str = ""
    # Cap on photos accepted by the synchronous single-shot POST /api/extract
    # endpoint. A screenshot is 1–2 images; keep this small so a one-call
    # consumer can't block on a 50-photo hub round-trip. Larger takes use the
    # async create → photos → extract → poll session flow instead.
    single_shot_max_photos: int = 8
    # Feature flag: full-text search over the session archive (FTS5).
    # Off → no index file, /api/search short-circuits, search UI hides.
    search_enabled: bool = True
    # Feature flag: on-device pre-flight image quality gate. Off →
    # the PWA skips client-side blur/glare scoring entirely.
    quality_gate_enabled: bool = True
    # Bearer token enforced when the request did NOT come from a
    # loopback IP. Empty string disables enforcement entirely.
    auth_token: str = ""
    # Optional password gate that hands the bearer token back to the
    # browser when the user types it correctly. Lets a fresh device
    # bootstrap without copy-pasting a tokenised URL.
    auth_password: str = ""

    @property
    def effective_single_shot_max_photos(self) -> int:
        """The single-shot cap in force: never above the take's own max, so a
        small Max photos per take lowers it instead of failing the save."""
        return min(self.single_shot_max_photos, self.max_photos_per_session)

    def extract_policy(self) -> ExtractPolicy:
        """The OCR client's timeout/parallelism/retry policy from these settings."""
        return ExtractPolicy(
            request_timeout_s=self.extract_request_timeout_s,
            run_budget_s=self.extract_run_budget_s,
            concurrency=self.extract_concurrency,
            fallback_model=self.extract_fallback_model or None,
        )


def load_webapp_config(path: Optional[Path] = None) -> WebappConfig:
    """Load the webapp config, falling back to defaults if the file is missing.

    A missing file is not an error — first-run is expected. The webapp
    creates the file on the first "Save defaults" tap.
    """
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        logger.info(
            f"📂 webapp_config not found at {target}, using defaults "
            f"(file will be created when settings change)"
        )
        return WebappConfig()

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            f"⚠️  Could not read {target} ({exc}); falling back to defaults"
        )
        return WebappConfig()

    # Each present key is coerced by its field's type; an absent key keeps the
    # dataclass default. Keys in _BLANK_MEANS_DEFAULT also treat null/empty as
    # absent (for the model keys that default is the sample-file one).
    hints = get_type_hints(WebappConfig)
    values = {}
    for f in fields(WebappConfig):
        value = raw.get(f.name)
        if f.name not in raw or (f.name in _BLANK_MEANS_DEFAULT and not value):
            continue
        values[f.name] = (get_origin(hints[f.name]) or hints[f.name])(value)
    cfg = WebappConfig(**values)
    _validate(cfg)
    return cfg


def save_webapp_config(cfg: WebappConfig, path: Optional[Path] = None) -> Path:
    """Atomically write the config back to disk."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = asdict(cfg)

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    logger.info(f"💾 Saved webapp_config to {target}")
    return target


def update_webapp_config(**fields) -> WebappConfig:
    """Read, patch, save — convenience for the API endpoint."""
    current = load_webapp_config()
    patched = replace(current, **fields)
    _validate(patched)
    save_webapp_config(patched)
    return patched


def append_auth_token(url: str, token: Optional[str]) -> str:
    """Return ``url`` with ``?token=<token>`` appended when ``token`` is set."""
    if not token:
        return url
    parsed = urlparse(url)
    existing = parsed.query
    extra = urlencode({"token": token})
    new_query = f"{existing}&{extra}" if existing else extra
    return urlunparse(parsed._replace(query=new_query))


def _validate(cfg: WebappConfig) -> None:
    if cfg.ocr_models_available and cfg.ocr_model_default not in cfg.ocr_models_available:
        raise ValueError(
            f"ocr_model_default {cfg.ocr_model_default!r} not in "
            f"ocr_models_available {cfg.ocr_models_available!r}"
        )
    if cfg.history_retention_days < 1:
        raise ValueError("history_retention_days must be >= 1")
    if cfg.max_photos_per_session < 1 or cfg.max_photos_per_session > 200:
        raise ValueError("max_photos_per_session must be between 1 and 200")
    if cfg.max_photo_dimension_px < 256 or cfg.max_photo_dimension_px > 8192:
        raise ValueError("max_photo_dimension_px must be between 256 and 8192")
    if cfg.extract_chunk_size < 1 or cfg.extract_chunk_size > cfg.max_photos_per_session:
        raise ValueError(
            "extract_chunk_size must be between 1 and max_photos_per_session"
        )
    if cfg.extract_request_timeout_s < 10:
        raise ValueError("extract_request_timeout_s must be >= 10")
    if cfg.extract_run_budget_s < cfg.extract_request_timeout_s:
        raise ValueError(
            "extract_run_budget_s must be >= extract_request_timeout_s"
        )
    if cfg.extract_concurrency < 1 or cfg.extract_concurrency > 16:
        raise ValueError("extract_concurrency must be between 1 and 16")
    if (
        cfg.extract_fallback_model
        and cfg.ocr_models_available
        and cfg.extract_fallback_model not in cfg.ocr_models_available
    ):
        raise ValueError(
            f"extract_fallback_model {cfg.extract_fallback_model!r} not in "
            f"ocr_models_available {cfg.ocr_models_available!r}"
        )
    if cfg.single_shot_max_photos < 1:
        raise ValueError("single_shot_max_photos must be >= 1")
