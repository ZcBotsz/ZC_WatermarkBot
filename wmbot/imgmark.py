"""Photo watermarking (Pillow)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .render import apply_to_image
from .settings import WMSettings

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def watermark_image(src: Path, dst: Path, s: WMSettings, logo: Image.Image | None) -> Path:
    im = Image.open(src)
    fmt = (im.format or "JPEG").upper()
    im = ImageOps.exif_transpose(im)
    out = apply_to_image(im, s, logo)
    if fmt in ("JPEG", "MPO") or dst.suffix.lower() in (".jpg", ".jpeg"):
        out.convert("RGB").save(dst, "JPEG", quality=93, optimize=True, subsampling=0 if max(out.size) < 4000 else 2)
    elif fmt == "WEBP":
        out.save(dst, "WEBP", quality=93, method=4)
    else:
        out.save(dst, "PNG", optimize=False, compress_level=3)
    return dst
