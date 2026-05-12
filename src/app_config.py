"""Application-level configuration loader (separate from webapp config).

This file is the source of truth for things that apply across surfaces
(CLI, webapp, tray): log level, optional language hint for the OCR
prompt. Webapp-specific UI prefs live in `src/webapp_config.py`.
"""

from __future__ import annotations

# Standard library imports
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class AppConfig:
    log_level: str = "INFO"
    # Optional ISO-639-1 language hint prepended to the OCR system prompt
    # when set ("These photos are likely in <language>."). None = let the
    # model auto-detect (the default and almost always the right answer).
    default_language_hint: Optional[str] = None
    # Optional webapp section — when missing, the tray spawns the webapp
    # on `:8444` with default settings. Set webapp.enabled:false to opt out.
    webapp: Dict = field(default_factory=dict)


def load_app_config(path: Optional[Path] = None) -> AppConfig:
    """Load `config/config.json` from next to this file (or an override)."""
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "config.json"
    else:
        path = Path(path).resolve()

    if not path.exists():
        logger.warning(f"📂 Config not found at {path}, using defaults")
        return AppConfig()

    raw = json.loads(path.read_text(encoding="utf-8"))
    _validate(raw)
    return AppConfig(
        log_level=raw.get("log_level", "INFO"),
        default_language_hint=raw.get("default_language_hint") or None,
        webapp=raw.get("webapp") or {},
    )


def _validate(raw: Dict) -> None:
    if "log_level" in raw and raw["log_level"] not in VALID_LOG_LEVELS:
        raise ValueError(f"log_level must be one of {VALID_LOG_LEVELS}")
    if "default_language_hint" in raw and raw["default_language_hint"] is not None:
        v = raw["default_language_hint"]
        if not isinstance(v, str) or len(v) > 32:
            raise ValueError(
                "default_language_hint must be a short string (e.g. 'es', 'Spanish') or null"
            )
