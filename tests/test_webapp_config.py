"""Tests for src/webapp_config.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.webapp_config import (
    WebappConfig,
    append_auth_token,
    load_webapp_config,
    save_webapp_config,
)


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    cfg = load_webapp_config(tmp_path / "nope.json")
    assert isinstance(cfg, WebappConfig)
    # Defaults pulled from the sample JSON committed alongside src/.
    assert cfg.port == 8444
    assert cfg.history_retention_days == 30
    assert cfg.extract_chunk_size == 1
    assert cfg.extract_policy().fallback_model is None
    assert cfg.ocr_prompt_default == "verbatim-merge"


def test_save_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    cfg = WebappConfig(
        ocr_model_default="gemini_flash",
        ocr_models_available=["gemini_flash", "gemini_pro"],
        ocr_prompt_default="verbatim-merge",
        llm_hub_url="http://127.0.0.1:8000",
        host="0.0.0.0",
        port=8444,
        history_retention_days=30,
    )
    save_webapp_config(cfg, path=path)
    reloaded = load_webapp_config(path)
    assert reloaded.ocr_model_default == "gemini_flash"
    assert reloaded.ocr_models_available == ["gemini_flash", "gemini_pro"]
    assert reloaded.history_retention_days == 30


def test_load_invalid_port_raises(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text(
        json.dumps(
            {
                "ocr_model_default": "gemini_flash",
                "ocr_models_available": ["gemini_flash"],
                "port": 999999,
            }
        )
    )
    with pytest.raises(ValueError):
        load_webapp_config(path)


def test_load_invalid_default_model_raises(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text(
        json.dumps(
            {
                "ocr_model_default": "not_in_list",
                "ocr_models_available": ["gemini_flash"],
            }
        )
    )
    with pytest.raises(ValueError):
        load_webapp_config(path)


def test_load_invalid_extract_chunk_size_raises(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text(
        json.dumps(
            {
                "ocr_model_default": "gemini_flash",
                "ocr_models_available": ["gemini_flash"],
                "max_photos_per_session": 10,
                "extract_chunk_size": 11,
            }
        )
    )
    with pytest.raises(ValueError):
        load_webapp_config(path)


def test_extract_policy_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    cfg = WebappConfig(
        ocr_model_default="claude_opus",
        ocr_models_available=["claude_opus", "claude_sonnet"],
        extract_request_timeout_s=300,
        extract_run_budget_s=600,
        extract_concurrency=2,
        extract_fallback_model="claude_sonnet",
    )
    save_webapp_config(cfg, path=path)
    policy = load_webapp_config(path).extract_policy()
    assert policy.request_timeout_s == 300
    assert policy.run_budget_s == 600
    assert policy.concurrency == 2
    assert policy.fallback_model == "claude_sonnet"


@pytest.mark.parametrize(
    "fields",
    [
        {"extract_request_timeout_s": 5},
        {"extract_request_timeout_s": 600, "extract_run_budget_s": 300},
        {"extract_concurrency": 0},
        {"extract_concurrency": 17},
        {"extract_fallback_model": "not_in_list"},
    ],
)
def test_load_invalid_extract_policy_raises(tmp_path: Path, fields: dict) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text(
        json.dumps(
            {
                "ocr_model_default": "gemini_flash",
                "ocr_models_available": ["gemini_flash"],
                **fields,
            }
        )
    )
    with pytest.raises(ValueError):
        load_webapp_config(path)


def test_append_auth_token() -> None:
    assert append_auth_token("https://x.example/", "abc") == "https://x.example/?token=abc"
    # Empty token = pass through
    assert append_auth_token("https://x.example/", "") == "https://x.example/"
    # Pre-existing query string is preserved
    out = append_auth_token("https://x.example/?foo=bar", "abc")
    assert "foo=bar" in out and "token=abc" in out
