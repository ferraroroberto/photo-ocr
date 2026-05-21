"""Configuration + status routes: read/patch webapp config, llm_hub
reachability."""

from __future__ import annotations

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Request

# Local imports
from src.ocr_client import OcrClient
from src.ocr_prompts import load_ocr_prompts
from src.webapp_config import WebappConfig, update_webapp_config

router = APIRouter()


def _config_dict(cfg: WebappConfig) -> Dict[str, Any]:
    return {
        "ocr_model_default": cfg.ocr_model_default,
        "ocr_models_available": cfg.ocr_models_available,
        "ocr_prompt_default": cfg.ocr_prompt_default,
        "history_retention_days": cfg.history_retention_days,
        "max_photos_per_session": cfg.max_photos_per_session,
        "max_photo_dimension_px": cfg.max_photo_dimension_px,
    }


@router.get("/api/config")
async def get_config(request: Request) -> Dict[str, Any]:
    cfg: WebappConfig = request.app.state.webapp_config
    prompts = load_ocr_prompts()
    return {
        "ocr_model_default": cfg.ocr_model_default,
        "ocr_models_available": cfg.ocr_models_available,
        "ocr_prompt_default": cfg.ocr_prompt_default,
        "ocr_prompts": [
            {
                "id": p.id,
                "label": p.label,
                "description": p.description,
                "system": p.system,
            }
            for p in prompts
        ],
        "history_retention_days": cfg.history_retention_days,
        "max_photos_per_session": cfg.max_photos_per_session,
        "max_photo_dimension_px": cfg.max_photo_dimension_px,
        "auth_password_set": bool(cfg.auth_password),
    }


@router.post("/api/config")
async def patch_config(request: Request) -> Dict[str, Any]:
    body = await request.json()
    allowed = {
        "ocr_model_default",
        "ocr_prompt_default",
        "history_retention_days",
        "max_photos_per_session",
    }
    patch = {k: v for k, v in body.items() if k in allowed}
    try:
        new_cfg = update_webapp_config(**patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    request.app.state.webapp_config = new_cfg
    return {"ok": True, "config": _config_dict(new_cfg)}


@router.get("/api/status")
async def status(request: Request) -> Dict[str, Any]:
    ocr: OcrClient = request.app.state.ocr_client
    return {
        "llm_hub": {
            "reachable": ocr.is_reachable(),
            "base_url": ocr.base_url,
        },
    }
