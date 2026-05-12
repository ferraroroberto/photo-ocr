"""Generate icon-180.png, icon-512.png, icon-512-maskable.png for the PWA.

Simple programmatic icon: dark background with a centered camera glyph.
No designer required, but the file structure is what matters — the
icons just need to exist for the manifest to validate and for iOS to
have something to show on the Home Screen.
"""

from __future__ import annotations

# Standard library imports
import logging
from pathlib import Path

# Third-party imports
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static"

BG = (10, 10, 10, 255)              # dark
FG = (74, 138, 243, 255)            # accent blue
ACCENT_DARK = (24, 24, 24, 255)


def _camera_icon(size: int, maskable: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Maskable icons need a safe zone — Android may crop the corners.
    # Shrink the camera glyph to ~70% of the canvas for maskable.
    scale = 0.7 if maskable else 0.84
    pad = int(size * (1 - scale) / 2)
    inner = size - pad * 2

    # Rounded rect "body"
    body_top = pad + int(inner * 0.22)
    body_bottom = pad + inner
    body_left = pad
    body_right = pad + inner
    radius = int(inner * 0.12)
    draw.rounded_rectangle(
        [body_left, body_top, body_right, body_bottom],
        radius=radius,
        fill=ACCENT_DARK,
        outline=FG,
        width=max(2, size // 64),
    )

    # Viewfinder "hump"
    hump_w = int(inner * 0.32)
    hump_h = int(inner * 0.12)
    hump_left = (size - hump_w) // 2
    hump_top = body_top - hump_h + 2
    hump_right = hump_left + hump_w
    hump_bottom = body_top + 2
    draw.rounded_rectangle(
        [hump_left, hump_top, hump_right, hump_bottom],
        radius=max(2, hump_h // 3),
        fill=ACCENT_DARK,
        outline=FG,
        width=max(2, size // 64),
    )

    # Lens
    lens_r = int(inner * 0.22)
    cx = size // 2
    cy = body_top + (body_bottom - body_top) // 2 + int(inner * 0.02)
    draw.ellipse(
        [cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r],
        fill=BG,
        outline=FG,
        width=max(2, size // 48),
    )
    inner_r = int(lens_r * 0.55)
    draw.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=FG,
    )

    return img


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    for size, name, maskable in (
        (180, "icon-180.png", False),
        (512, "icon-512.png", False),
        (512, "icon-512-maskable.png", True),
    ):
        img = _camera_icon(size, maskable=maskable)
        out = STATIC_DIR / name
        img.save(out, format="PNG")
        logger.info(f"💾 wrote {out} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
