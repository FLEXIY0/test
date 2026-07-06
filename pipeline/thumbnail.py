"""Thumbnail generation: first image + big high-contrast text.

Older viewers respond to large, readable text and warm tones — keep the
thumbnail text to 2-4 words (set via '# THUMBNAIL_TEXT:' in the script).
"""

from __future__ import annotations

import logging

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

logger = logging.getLogger("thumbnail")

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
CANVAS = (1280, 720)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    logger.warning("no TTF font found — falling back to PIL default")
    return ImageFont.load_default()


def make_thumbnail(image_path: str, text: str, out_path: str) -> None:
    img = Image.open(image_path).convert("RGB")
    # Cover-crop to 1280x720.
    scale = max(CANVAS[0] / img.width, CANVAS[1] / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)))
    left = (img.width - CANVAS[0]) // 2
    top = (img.height - CANVAS[1]) // 2
    img = img.crop((left, top, left + CANVAS[0], top + CANVAS[1]))
    img = ImageEnhance.Brightness(img).enhance(0.72)   # darken for text contrast

    if text:
        draw = ImageDraw.Draw(img)
        words = text.upper().split()
        mid = (len(words) + 1) // 2
        lines = [" ".join(words[:mid])] + ([" ".join(words[mid:])] if words[mid:] else [])
        size = 150
        font = _load_font(size)
        while size > 60 and any(
            draw.textlength(line, font=font) > CANVAS[0] - 120 for line in lines
        ):
            size -= 10
            font = _load_font(size)
        line_height = size + 18
        y = CANVAS[1] - line_height * len(lines) - 60
        for line in lines:
            x = (CANVAS[0] - draw.textlength(line, font=font)) // 2
            # Outline for readability on any background.
            for dx, dy in ((-4, -4), (-4, 4), (4, -4), (4, 4)):
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 214, 90))
            y += line_height

    img.save(out_path, "JPEG", quality=90)
