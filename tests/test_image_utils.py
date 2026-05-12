"""Tests for src/image_utils.py."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from src.image_utils import (
    HARD_BYTES_CAP,
    ImageValidationError,
    validate_and_persist,
)


def test_persists_jpeg_with_correct_name(tmp_path: Path, jpeg_bytes: bytes) -> None:
    result = validate_and_persist(
        raw=jpeg_bytes,
        content_type="image/jpeg",
        dest_folder=tmp_path,
        sequence_index=1,
    )
    assert result.path.name == "01.jpg"
    assert result.path.exists()
    assert result.sequence_index == 1
    assert result.width == 128
    assert result.height == 96
    assert result.bytes_on_disk > 0


def test_persists_png_as_jpeg(tmp_path: Path, png_bytes: bytes) -> None:
    """PNGs are accepted but the persisted file is always JPEG."""
    result = validate_and_persist(
        raw=png_bytes,
        content_type="image/png",
        dest_folder=tmp_path,
        sequence_index=7,
    )
    assert result.path.name == "07.jpg"
    # Sanity-check it's a valid JPEG on disk.
    with Image.open(result.path) as im:
        assert im.format == "JPEG"


def test_rejects_unsupported_content_type(tmp_path: Path, jpeg_bytes: bytes) -> None:
    with pytest.raises(ImageValidationError):
        validate_and_persist(
            raw=jpeg_bytes,
            content_type="application/pdf",
            dest_folder=tmp_path,
            sequence_index=1,
        )


def test_rejects_empty_upload(tmp_path: Path) -> None:
    with pytest.raises(ImageValidationError):
        validate_and_persist(
            raw=b"",
            content_type="image/jpeg",
            dest_folder=tmp_path,
            sequence_index=1,
        )


def test_rejects_oversized(tmp_path: Path) -> None:
    huge = b"\x00" * (HARD_BYTES_CAP + 1)
    with pytest.raises(ImageValidationError):
        validate_and_persist(
            raw=huge,
            content_type="image/jpeg",
            dest_folder=tmp_path,
            sequence_index=1,
        )


def test_rejects_corrupt_image(tmp_path: Path) -> None:
    with pytest.raises(ImageValidationError):
        validate_and_persist(
            raw=b"not actually a jpeg" * 50,
            content_type="image/jpeg",
            dest_folder=tmp_path,
            sequence_index=1,
        )


def test_downscale_runs(tmp_path: Path, big_jpeg_bytes: bytes) -> None:
    result = validate_and_persist(
        raw=big_jpeg_bytes,
        content_type="image/jpeg",
        dest_folder=tmp_path,
        sequence_index=1,
        max_dim_px=512,
    )
    assert max(result.width, result.height) == 512


def test_exif_orientation_not_required(tmp_path: Path, jpeg_bytes: bytes) -> None:
    # We don't ship a sideways JPEG fixture; just confirm the call
    # doesn't raise on a normal JPEG (the EXIF transpose path is a
    # no-op when there's no orientation tag).
    result = validate_and_persist(
        raw=jpeg_bytes,
        content_type="image/jpeg",
        dest_folder=tmp_path,
        sequence_index=2,
    )
    assert result.path.exists()
