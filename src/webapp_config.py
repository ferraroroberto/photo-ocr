"""Webapp-specific configuration loader.

Lives separately from `app_config.py` because these settings are
authored from the web UI ("Save defaults" button) and persist across
runs. The CLI also reads this file so both surfaces share one source
of truth.
"""

from __future__ import annotations

# Standard library imports
import json
import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "webapp_config.json"
)
SAMPLE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "webapp_config.sample.json"
)

DEFAULT_OCR_PROMPT_ID = "verbatim-merge"
DEFAULT_LLM_HUB_URL = "http://127.0.0.1:8000"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8444
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_PHOTOS = 50
DEFAULT_MAX_DIM_PX = 2048


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
    ocr_prompt_default: str = DEFAULT_OCR_PROMPT_ID
    llm_hub_url: str = DEFAULT_LLM_HUB_URL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    history_retention_days: int = DEFAULT_RETENTION_DAYS
    max_photos_per_session: int = DEFAULT_MAX_PHOTOS
    max_photo_dimension_px: int = DEFAULT_MAX_DIM_PX
    # Bearer token enforced when the request did NOT come from a
    # loopback IP. Empty string disables enforcement entirely.
    auth_token: str = ""
    # Optional password gate that hands the bearer token back to the
    # browser when the user types it correctly. Lets a fresh device
    # bootstrap without copy-pasting a tokenised URL.
    auth_password: str = ""


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

    sample_default, sample_available = _sample_ocr_defaults()
    cfg = WebappConfig(
        ocr_model_default=str(
            raw.get("ocr_model_default") or sample_default
        ),
        ocr_models_available=list(
            raw.get("ocr_models_available") or sample_available
        ),
        ocr_prompt_default=str(
            raw.get("ocr_prompt_default", DEFAULT_OCR_PROMPT_ID)
        ),
        llm_hub_url=str(raw.get("llm_hub_url", DEFAULT_LLM_HUB_URL)),
        host=str(raw.get("host", DEFAULT_HOST)),
        port=int(raw.get("port", DEFAULT_PORT)),
        history_retention_days=int(
            raw.get("history_retention_days", DEFAULT_RETENTION_DAYS)
        ),
        max_photos_per_session=int(
            raw.get("max_photos_per_session", DEFAULT_MAX_PHOTOS)
        ),
        max_photo_dimension_px=int(
            raw.get("max_photo_dimension_px", DEFAULT_MAX_DIM_PX)
        ),
        auth_token=str(raw.get("auth_token", "")),
        auth_password=str(raw.get("auth_password", "")),
    )
    _validate(cfg)
    return cfg


def save_webapp_config(cfg: WebappConfig, path: Optional[Path] = None) -> Path:
    """Atomically write the config back to disk."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "ocr_model_default": cfg.ocr_model_default,
        "ocr_models_available": list(cfg.ocr_models_available),
        "ocr_prompt_default": cfg.ocr_prompt_default,
        "llm_hub_url": cfg.llm_hub_url,
        "host": cfg.host,
        "port": cfg.port,
        "history_retention_days": cfg.history_retention_days,
        "max_photos_per_session": cfg.max_photos_per_session,
        "max_photo_dimension_px": cfg.max_photo_dimension_px,
        "auth_token": cfg.auth_token,
        "auth_password": cfg.auth_password,
    }

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
    if not (1 <= cfg.port <= 65535):
        raise ValueError(f"port out of range: {cfg.port}")
    if cfg.max_photos_per_session < 1 or cfg.max_photos_per_session > 200:
        raise ValueError("max_photos_per_session must be between 1 and 200")
    if cfg.max_photo_dimension_px < 256 or cfg.max_photo_dimension_px > 8192:
        raise ValueError("max_photo_dimension_px must be between 256 and 8192")
