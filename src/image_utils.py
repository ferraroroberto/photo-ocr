"""Server-side image handling — validate, transcode, downscale, persist.

Responsibilities (no UI imports, no FastAPI imports):

- Format whitelist: jpeg / png / webp / heic / heif. Reject everything
  else with a clear ImageValidationError.
- HEIC/HEIF transcode to JPEG via pillow-heif. The on-disk artefact is
  always JPEG so downstream consumers (model, UI) don't deal with HEIC.
- EXIF orientation: apply before persisting so nothing downstream sees
  a sideways image.
- Downscale: if max dimension > max_dim_px, scale to fit. JPEG quality 85.
- Sequence numbering: photos within a session are stored as 01.jpg,
  02.jpg, …, zero-padded to 2 digits. Order = upload order.
"""

from __future__ import annotations

# Standard library imports
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

# Third-party imports — pillow-heif is best-effort. If the wheel isn't
# installed on this platform, HEIC uploads fail with a clear message
# while everything else keeps working.
try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover — runtime env detail
    HEIF_AVAILABLE = False

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

DEFAULT_MAX_DIM_PX = 2048
DEFAULT_JPEG_QUALITY = 85
HARD_BYTES_CAP = 25 * 1024 * 1024  # 25 MB defence-in-depth

# Whitelisted MIME types the webapp accepts on upload.
ACCEPTED_CONTENT_TYPES = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/pjpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
})

HEIC_CONTENT_TYPES = frozenset({"image/heic", "image/heif"})


class ImageValidationError(Exception):
    """Raised when an upload is unsupported, corrupt, or oversized."""


@dataclass(frozen=True)
class PersistedPhoto:
    """Result of a successful validate_and_persist call."""

    path: Path
    sequence_index: int
    width: int
    height: int
    bytes_on_disk: int


def validate_and_persist(
    raw: bytes,
    content_type: str,
    dest_folder: Path,
    sequence_index: int,
    max_dim_px: int = DEFAULT_MAX_DIM_PX,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> PersistedPhoto:
    """Validate, EXIF-rotate, downscale, persist as NN.jpg. Return metadata.

    Raises ImageValidationError on unsupported type, oversized payload,
    or corrupt image data.
    """
    ct = (content_type or "").lower().strip().split(";")[0].strip()
    if ct not in ACCEPTED_CONTENT_TYPES:
        raise ImageValidationError(
            f"unsupported content_type {ct!r}; expected one of "
            f"{sorted(ACCEPTED_CONTENT_TYPES)}"
        )
    if not raw:
        raise ImageValidationError("empty upload")
    if len(raw) > HARD_BYTES_CAP:
        raise ImageValidationError(
            f"upload too large: {len(raw)} bytes > {HARD_BYTES_CAP} cap"
        )
    if ct in HEIC_CONTENT_TYPES and not HEIF_AVAILABLE:
        raise ImageValidationError(
            "HEIC/HEIF uploads require the pillow-heif package which is "
            "not installed on this server. Re-export the photo as JPEG "
            "on the device, or install pillow-heif."
        )

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im = _maybe_downscale(im, max_dim_px)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            jpeg_bytes = buf.getvalue()
            final_size = im.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(
            f"could not decode image (content_type={ct}): {exc}"
        ) from exc

    dest_folder.mkdir(parents=True, exist_ok=True)
    filename = f"{sequence_index:02d}.jpg"
    out_path = dest_folder / filename
    out_path.write_bytes(jpeg_bytes)

    logger.info(
        f"📷 persisted {filename} ({final_size[0]}x{final_size[1]}, "
        f"{len(jpeg_bytes) / 1024:.0f} KB) → {out_path}"
    )
    return PersistedPhoto(
        path=out_path,
        sequence_index=sequence_index,
        width=final_size[0],
        height=final_size[1],
        bytes_on_disk=len(jpeg_bytes),
    )


def _maybe_downscale(im: Image.Image, max_dim_px: int) -> Image.Image:
    w, h = im.size
    long_edge = max(w, h)
    if long_edge <= max_dim_px:
        return im
    scale = max_dim_px / long_edge
    new_size: Tuple[int, int] = (max(1, int(w * scale)), max(1, int(h * scale)))
    return im.resize(new_size, Image.LANCZOS)
