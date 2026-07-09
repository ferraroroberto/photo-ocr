"""`extract` subcommand — one-shot CLI OCR for local files (no webapp).

Useful for scripting / smoke tests:

    python launcher.py extract photo1.jpg photo2.jpg --model gemini_flash
"""

from __future__ import annotations

# Standard library imports
import argparse
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

from src.app_config import load_app_config
from src.image_utils import ImageValidationError, validate_and_persist
from src.ocr_client import OcrClient, OcrError
from src.ocr_prompts import apply_language_hint, get_prompt
from src.webapp_config import load_webapp_config

from .base import BaseCommand

logger = logging.getLogger(__name__)


class ExtractCommand(BaseCommand):
    @classmethod
    def add_parser(cls, subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser(
            "extract",
            help="One-shot OCR of N image files; prints extracted text to stdout",
        )
        p.add_argument("photos", nargs="+", type=Path, help="1..N image paths")
        p.add_argument("--model", default=None, help="Override the default OCR model alias")
        p.add_argument("--prompt-id", default=None, help="Override the default OCR prompt id")

    def execute(self, args: argparse.Namespace) -> int:
        cfg = load_webapp_config()
        app_cfg = load_app_config()
        model = args.model or cfg.ocr_model_default
        prompt = get_prompt(args.prompt_id or cfg.ocr_prompt_default)
        system = apply_language_hint(prompt.system, app_cfg.default_language_hint)

        for p in args.photos:
            if not p.exists() or not p.is_file():
                logger.error(f"❌ not a file: {p}")
                return 1

        client = OcrClient(cfg.llm_hub_url)
        if not client.is_reachable():
            logger.error(f"❌ LLM hub not reachable at {cfg.llm_hub_url}")
            return 2

        # Persist re-encoded JPEGs into a temp dir mirroring the
        # webapp's normalisation, then hand the temp paths to the
        # client. Clean up on exit.
        tmpdir = Path(tempfile.mkdtemp(prefix="photo_ocr_cli_"))
        try:
            persisted: list[Path] = []
            for idx, src in enumerate(args.photos, start=1):
                raw = src.read_bytes()
                content_type = _guess_content_type(src)
                try:
                    p = validate_and_persist(
                        raw=raw,
                        content_type=content_type,
                        dest_folder=tmpdir,
                        sequence_index=idx,
                        max_dim_px=cfg.max_photo_dimension_px,
                    )
                except ImageValidationError as exc:
                    logger.error(f"❌ {src}: {exc}")
                    return 1
                persisted.append(p.path)
                logger.info(f"📷 prepared {src.name} ({p.width}x{p.height})")

            t0 = time.monotonic()
            try:
                result = client.extract(
                    image_paths=persisted,
                    model=model,
                    system=system,
                    chunk_size=cfg.extract_chunk_size,
                )
            except OcrError as exc:
                logger.error(f"❌ OCR failed: {exc}")
                return 3

            elapsed = time.monotonic() - t0
            logger.info(
                f"⏱️  {elapsed:.2f}s · {len(result.extracted_text)} chars · model={model}"
            )
            sys.stdout.write(result.extracted_text)
            if not result.extracted_text.endswith("\n"):
                sys.stdout.write("\n")
            return 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _guess_content_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }.get(ext, "image/jpeg")
