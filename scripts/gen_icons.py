"""Generate PWA icons: solid white camera silhouette on pure-black background.

Matches the voice-transcriber icon style (solid-white-on-black, flat, no outlines).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BG = (10, 10, 10)
FG = (240, 240, 240)

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static"


def draw_camera(size: int, inset: float) -> Image.Image:
    """Render a camera silhouette centered on a black square.

    inset is the fraction of the canvas reserved as padding around the icon
    (used to produce a 'maskable' variant with safe margins).
    """
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)

    pad = int(size * inset)
    content = size - 2 * pad

    body_w = int(content * 0.86)
    body_h = int(content * 0.62)
    body_x = (size - body_w) // 2
    body_y = pad + int(content * 0.30)
    radius = int(min(body_w, body_h) * 0.14)
    d.rounded_rectangle(
        [body_x, body_y, body_x + body_w, body_y + body_h],
        radius=radius,
        fill=FG,
    )

    vf_w = int(body_w * 0.30)
    vf_h = int(body_h * 0.18)
    vf_x = (size - vf_w) // 2
    vf_y = body_y - int(vf_h * 0.85)
    vf_r = int(vf_h * 0.35)
    d.rounded_rectangle(
        [vf_x, vf_y, vf_x + vf_w, vf_y + vf_h],
        radius=vf_r,
        fill=FG,
    )

    cx = size // 2
    cy = body_y + body_h // 2
    outer_r = int(body_h * 0.38)
    inner_r = int(outer_r * 0.62)
    d.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r], fill=BG)
    d.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r], fill=FG)
    dot_r = int(inner_r * 0.45)
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=BG)

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    draw_camera(512, inset=0.06).save(OUT_DIR / "icon-512.png", "PNG")
    draw_camera(512, inset=0.20).save(OUT_DIR / "icon-512-maskable.png", "PNG")
    draw_camera(180, inset=0.06).save(OUT_DIR / "icon-180.png", "PNG")

    print(f"wrote icons to {OUT_DIR}")


if __name__ == "__main__":
    main()
