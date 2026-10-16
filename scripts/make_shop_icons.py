"""Generate placeholder PWA icons for the shop app.

Why a script (not committed binaries from elsewhere): the icons are a
simple branded placeholder (indigo tile + white "B"). Regenerate with a
proper logo whenever the shop supplies artwork — same paths, no code change.

Usage: uv run python scripts/make_shop_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent.parent / "static" / "shop" / "icons"
BASE.mkdir(parents=True, exist_ok=True)

SIZES = [192, 512]
BG = (30, 27, 75, 255)  # indigo-950-ish, matches theme-color
FG = (255, 255, 255, 255)


def make_icon(size):
    """Build one rounded-tile icon with a centered letter."""
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)
    radius = size // 5
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)
    text = "B"
    font_size = size // 2
    try:
        from PIL import ImageFont

        font = ImageFont.load_default(size=font_size)
    except TypeError:  # Pillow < 10.1 has no size parameter
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), text, font=font, fill=FG)
    return img


for size in SIZES:
    path = BASE / f"icon-{size}.png"
    make_icon(size).save(path)
    print(f"wrote {path}")
