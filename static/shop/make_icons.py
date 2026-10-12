"""Generate placeholder PWA icons for the shop app.

Why a script (not committed binaries from elsewhere): the icons are a
simple branded placeholder (indigo tile + white "B"). Regenerate with a
proper logo whenever the shop supplies artwork — same paths, no code change.
"""

from pathlib import Path

from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent / "icons"
BASE.mkdir(parents=True, exist_ok=True)

SIZES = [192, 512]
BG = (30, 27, 75, 255)  # indigo-950-ish, matches theme-color
FG = (255, 255, 255, 255)


def make_icon(size):
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)
    # Rounded corners.
    radius = size // 5
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)
    # Centered "B" using Pillow's built-in bitmap font, scaled up.
    text = "B"
    # textbbox for centering (bitmap font is small; scale by drawing large).
    font_size = size // 2
    try:
        from PIL import ImageFont

        font = ImageFont.load_default(size=font_size)
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), text, font=font, fill=FG)
    return img


for size in SIZES:
    path = BASE / f"icon-{size}.png"
    make_icon(size).save(path)
    print(f"wrote {path}")
