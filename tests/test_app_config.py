"""Tests for src/app_config.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app_config import AppConfig, load_app_config


def test_load_app_config_missing_returns_defaults(tmp_path: Path) -> None:
    cfg = load_app_config(tmp_path / "nonexistent.json")
    assert isinstance(cfg, AppConfig)
    assert cfg.log_level == "INFO"
    assert cfg.default_language_hint is None


def test_load_app_config_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"log_level": "DEBUG", "default_language_hint": "es"})
    )
    cfg = load_app_config(path)
    assert cfg.log_level == "DEBUG"
    assert cfg.default_language_hint == "es"


def test_load_app_config_invalid_log_level_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"log_level": "BOGUS"}))
    with pytest.raises(ValueError):
        load_app_config(path)


def test_load_app_config_invalid_language_hint(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"default_language_hint": 42}))
    with pytest.raises(ValueError):
        load_app_config(path)
