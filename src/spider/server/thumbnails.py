"""Render small PNG thumbnails of images.

This is the only module that imports Pillow. Decoding untrusted images is the
risky part of the server, so every failure -- truncated data, a decompression
bomb, an unsupported variant -- collapses into "no thumbnail" rather than an
exception that could turn into a 500.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image, ImageOps

THUMB_SIZE = 96
MAX_SOURCE_BYTES = 50 * 1024**2
MAX_PIXELS = 50_000_000
SUPPORTED_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"})


def render(source: Path, target: Path) -> bool:
    """Write a PNG thumbnail of `source` to `target`; return False on any failure."""
    temporary = target.with_suffix(".tmp")
    try:
        # Process-global, so set it on every call rather than trusting import order.
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with warnings.catch_warnings():
            # Pillow only warns between 1x and 2x the limit; treat that as a bomb too.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                image.seek(0)  # animated formats: first frame only
                thumb = image.convert("RGBA")
        thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
        target.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(temporary, "PNG")
        temporary.replace(target)
        return True
    except Exception:
        temporary.unlink(missing_ok=True)
        return False
