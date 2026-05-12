"""Tests for src/ocr_prompts.py."""

from __future__ import annotations

import json
from pathlib import Path

from src.ocr_prompts import (
    DEFAULT_PROMPT_ID,
    OcrPrompt,
    get_prompt,
    load_ocr_prompts,
)


def test_load_missing_falls_back_to_builtin(tmp_path: Path) -> None:
    prompts = load_ocr_prompts(tmp_path / "nope.json")
    assert len(prompts) == 1
    assert prompts[0].id == DEFAULT_PROMPT_ID


def test_load_committed_library_has_verbatim_merge() -> None:
    # No path override → loads config/ocr_prompts.json, the committed
    # library.
    prompts = load_ocr_prompts()
    ids = [p.id for p in prompts]
    assert "verbatim-merge" in ids


def test_load_dedupes_and_filters_bad_entries(tmp_path: Path) -> None:
    raw = [
        {"id": "x", "label": "X", "description": "", "system": "sys1"},
        {"id": "x", "label": "X dup", "description": "", "system": "sys2"},
        {"id": "", "label": "no id", "description": "", "system": "sys3"},
        {"id": "y", "label": "Y", "description": "", "system": ""},
        {"id": "z", "label": "Z", "description": "ok", "system": "sysZ"},
    ]
    path = tmp_path / "ocr_prompts.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    prompts = load_ocr_prompts(path)
    ids = [p.id for p in prompts]
    assert ids == ["x", "z"]


def test_get_prompt_falls_back_to_first(tmp_path: Path) -> None:
    raw = [
        {"id": "alpha", "label": "A", "description": "", "system": "a"},
        {"id": "beta", "label": "B", "description": "", "system": "b"},
    ]
    path = tmp_path / "ocr_prompts.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    prompts = load_ocr_prompts(path)
    assert get_prompt("beta", prompts).id == "beta"
    # Unknown id → fall back to first.
    assert get_prompt("ghost", prompts).id == "alpha"
    # Empty id → fall back to first.
    assert get_prompt(None, prompts).id == "alpha"


def test_load_invalid_json_uses_builtin(tmp_path: Path) -> None:
    path = tmp_path / "ocr_prompts.json"
    path.write_text("not json", encoding="utf-8")
    prompts = load_ocr_prompts(path)
    assert prompts[0].id == DEFAULT_PROMPT_ID
