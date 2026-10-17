"""Tests for src/webapp_config.py."""

from __future__ import annotations

import json
from dataclasses import fields
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
        history_retention_days=30,
    )
    save_webapp_config(cfg, path=path)
    reloaded = load_webapp_config(path)
    assert reloaded.ocr_model_default == "gemini_flash"
    assert reloaded.ocr_models_available == ["gemini_flash", "gemini_pro"]
    assert reloaded.history_retention_days == 30


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


def test_save_writes_every_field_and_round_trips(tmp_path: Path) -> None:
    """A new ``WebappConfig`` field must reach disk without a second list to
    update: the saved file carries exactly the dataclass's fields."""
    cfg = WebappConfig(auth_token="tok", quality_gate_enabled=False)
    path = tmp_path / "webapp_config.json"
    save_webapp_config(cfg, path=path)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        f.name for f in fields(WebappConfig)
    }
    assert load_webapp_config(path) == cfg


def test_single_shot_cap_follows_the_smaller_max_photos() -> None:
    cfg = WebappConfig(max_photos_per_session=5, single_shot_max_photos=8)
    assert cfg.effective_single_shot_max_photos == 5
    cfg = WebappConfig(max_photos_per_session=50, single_shot_max_photos=8)
    assert cfg.effective_single_shot_max_photos == 8


def test_small_max_photos_is_not_rejected_by_the_hidden_single_shot_cap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text(
        json.dumps(
            {
                "ocr_model_default": "gemini_flash",
                "ocr_models_available": ["gemini_flash"],
                "max_photos_per_session": 3,
                "single_shot_max_photos": 8,
            }
        )
    )
    assert load_webapp_config(path).effective_single_shot_max_photos == 3


def test_single_shot_cap_below_one_is_still_rejected(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text(
        json.dumps(
            {
                "ocr_model_default": "gemini_flash",
                "ocr_models_available": ["gemini_flash"],
                "single_shot_max_photos": 0,
            }
        )
    )
    with pytest.raises(ValueError):
        load_webapp_config(path)


# --- load_webapp_config characterization (#213) ----------------------------
# Pins the loader's per-field behaviour so it can be restructured without
# changing what any key does.

_SAMPLE_MODELS = ["m_a", "m_b"]


def _write(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "webapp_config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_load_empty_config_equals_dataclass_defaults(tmp_path: Path) -> None:
    assert load_webapp_config(_write(tmp_path, {})) == WebappConfig()


def test_load_full_config_reads_every_field(tmp_path: Path) -> None:
    raw = {
        "ocr_model_default": "m_b",
        "ocr_models_available": _SAMPLE_MODELS,
        "ocr_prompt_default": "other-prompt",
        "llm_hub_url": "http://hub.example:9",
        "history_retention_days": 7,
        "max_photos_per_session": 12,
        "max_photo_dimension_px": 1024,
        "extract_chunk_size": 3,
        "extract_request_timeout_s": 60.5,
        "extract_run_budget_s": 120,
        "extract_concurrency": 2,
        "extract_fallback_model": "m_a",
        "single_shot_max_photos": 4,
        "search_enabled": False,
        "quality_gate_enabled": False,
        "auth_token": "tok",
        "auth_password": "pw",
    }
    cfg = load_webapp_config(_write(tmp_path, raw))
    assert {f.name: getattr(cfg, f.name) for f in fields(WebappConfig)} == raw
    assert isinstance(cfg.extract_run_budget_s, float)


def test_load_ignores_unknown_keys(tmp_path: Path) -> None:
    cfg = load_webapp_config(_write(tmp_path, {"_comment": "x", "nope": 1}))
    assert cfg == WebappConfig()


def test_load_coerces_values_by_field_type(tmp_path: Path) -> None:
    cfg = load_webapp_config(
        _write(
            tmp_path,
            {
                "history_retention_days": "12",
                "extract_request_timeout_s": "45",
                "extract_run_budget_s": "90",
                "search_enabled": 0,
                "quality_gate_enabled": "false",
                "llm_hub_url": 5,
            },
        )
    )
    assert cfg.history_retention_days == 12
    assert cfg.extract_request_timeout_s == 45.0
    assert isinstance(cfg.extract_request_timeout_s, float)
    assert cfg.search_enabled is False
    # bool("false") is True: truthiness, not parsing.
    assert cfg.quality_gate_enabled is True
    assert cfg.llm_hub_url == "5"


@pytest.mark.parametrize(
    ("key", "value", "exc"),
    [
        ("history_retention_days", "abc", ValueError),
        ("max_photos_per_session", None, TypeError),
        ("extract_request_timeout_s", "slow", ValueError),
        ("extract_concurrency", [1], TypeError),
    ],
)
def test_load_wrong_typed_numbers_raise(
    tmp_path: Path, key: str, value: object, exc: type
) -> None:
    with pytest.raises(exc):
        load_webapp_config(_write(tmp_path, {key: value}))


def test_load_empty_model_keys_fall_back_to_sample_file(tmp_path: Path) -> None:
    from src.webapp_config import _sample_ocr_defaults

    sample_default, sample_available = _sample_ocr_defaults()
    assert sample_default and sample_available
    for blank in ({}, {"ocr_model_default": None, "ocr_models_available": None},
                  {"ocr_model_default": "", "ocr_models_available": []}):
        cfg = load_webapp_config(_write(tmp_path, blank))
        assert cfg.ocr_model_default == sample_default
        assert cfg.ocr_models_available == sample_available


def test_load_models_list_is_copied_not_aliased(tmp_path: Path) -> None:
    cfg = load_webapp_config(
        _write(
            tmp_path,
            {"ocr_model_default": "m_a", "ocr_models_available": _SAMPLE_MODELS},
        )
    )
    assert cfg.ocr_models_available == _SAMPLE_MODELS
    assert cfg.ocr_models_available is not _SAMPLE_MODELS


def test_load_null_fallback_model_means_empty(tmp_path: Path) -> None:
    cfg = load_webapp_config(_write(tmp_path, {"extract_fallback_model": None}))
    assert cfg.extract_fallback_model == ""
    assert cfg.extract_policy().fallback_model is None


def test_load_unreadable_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_webapp_config(path) == WebappConfig()
