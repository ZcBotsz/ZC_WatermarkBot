"""PDF watermarking (PyMuPDF).

The watermark badge is rendered once per distinct page size (Pillow, ~3 px per PDF point so it stays
crisp when zoomed) and inserted as ONE shared image object — every page just references it, so the
output file barely grows and processing is I/O-bound (hundreds of pages per second).
"""
from __future__ import annotations

import io
import logging
import re
import threading
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image

from . import config
from .render import pos_xy, render_badge, render_tile_layer
from .settings import WMSettings

log = logging.getLogger(__name__)

SCALE = 3.0           # raster px per PDF point for badges
TILE_SCALE = 2.5
FLATTEN_DPI = 150


class PDFError(Exception):
    pass


def parse_pages(spec: str, total: int) -> list[int]:
    """'all' | '1-3,5,8-' | '-4'  →  sorted unique zero-based page indexes."""
    spec = (spec or "all").strip().lower()
    if spec in ("", "all", "*"):
        return list(range(total))
    pages: set[int] = set()
    for tok in re.split(r"[,\s;]+", spec):
        if not tok:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)", tok)
        if m and (m.group(1) or m.group(2)):
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else total
            if a > b:
                a, b = b, a
            pages.update(range(max(1, a), min(total, b) + 1))
        elif tok.isdigit():
            if 1 <= int(tok) <= total:
                pages.add(int(tok))
        else:
            raise PDFError(f"Can't understand page range '{tok}'. Use e.g. all, 1-3,5 or 4-")
    if not pages:
        raise PDFError(f"The page selection '{spec}' matches no page (document has {total}).")
    return sorted(p - 1 for p in pages)


def _luma(hexcolor: str) -> float:
    r, g, b = (int(hexcolor.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _pdf_settings(s: WMSettings) -> WMSettings:
    """Pages are usually white: darken near-white text (unless it sits on its own box)."""
    s = s.copy()
    if s.pdf_auto_color and s.pdf_layer != "under" and not s.bg_enabled and _luma(s.text_color) > 0.78:
        s.text_color, s.shadow = "#1F1F1F", False
        if s.stroke_width == 0:
            s.stroke_color = "#FFFFFF"
    return s


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def watermark_pdf(src: Path, dst: Path, s: WMSettings,
                  cancel: threading.Event | None = None,
                  progress: Callable[[int, int], None] | None = None) -> dict:
    """Blocking; call through asyncio.to_thread. Returns {'pages': n_marked, 'total': n_pages}."""
    s = _pdf_settings(s)
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise PDFError(f"Can't open this PDF ({e}).")
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise PDFError("This PDF is password-protected — remove the password first.")
        total = doc.page_count
        if total == 0:
            raise PDFError("The PDF has no pages.")
        if total > config.MAX_PDF_PAGES:
            raise PDFError(f"Too many pages ({total}); limit is {config.MAX_PDF_PAGES}.")
        targets = parse_pages(s.pdf_pages, total)
        overlay = s.pdf_layer != "under"
        cache: dict[tuple[int, int], tuple] = {}

        for n, idx in enumerate(targets):
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            page = doc[idx]
            vis = page.rect                       # as displayed (rotation applied)
            w, h = vis.width, vis.height
            key = (round(w), round(h))
            if s.mode == "tile":
                if key not in cache:
                    layer = render_tile_layer(s, int(w * TILE_SCALE), int(h * TILE_SCALE))
                    if layer is None:
                        raise PDFError("Nothing to draw — set a text watermark first.")
                    cache[key] = (_png(layer), None)
                data, _ = cache[key]
                rect = pymupdf.Rect(0, 0, w, h)
            else:
                if key not in cache:
                    badge = render_badge(s, int(min(w, h) * SCALE))
                    if badge is None:
                        raise PDFError("Nothing to draw — set a text watermark first.")
                    cache[key] = (_png(badge), badge.size)
                data, bsize = cache[key]
                bw, bh = bsize[0] / SCALE, bsize[1] / SCALE
                k = min(1.0, w * 0.98 / bw, h * 0.98 / bh)
                bw, bh = bw * k, bh * k
                m = s.margin / 100.0 * min(w, h)
                x, y = pos_xy(s.position, int(w), int(h), int(bw), int(bh), int(m))
                rect = pymupdf.Rect(x, y, x + bw, y + bh)

            if page.rotation:
                rect = rect * page.derotation_matrix
                rect.normalize()
            xref_key = (key, "xref")
            kwargs = dict(overlay=overlay, rotate=page.rotation if page.rotation else 0, keep_proportion=False)
            if xref_key in cache:
                page.insert_image(rect, xref=cache[xref_key], **kwargs)
            else:
                cache[xref_key] = page.insert_image(rect, stream=data, **kwargs)
            if progress and (n % 5 == 0 or n == len(targets) - 1):
                progress(n + 1, len(targets))

        if s.pdf_strip_meta:
            doc.set_metadata({})
            try:
                doc.del_xml_metadata()
            except Exception:
                pass

        if s.pdf_flatten:
            out = pymupdf.open()
            for i, page in enumerate(doc):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError
                pix = page.get_pixmap(dpi=FLATTEN_DPI, alpha=False)
                np_ = out.new_page(width=page.rect.width, height=page.rect.height)
                np_.insert_image(np_.rect, stream=pix.tobytes("jpeg", jpg_quality=88))
                if progress and i % 5 == 0:
                    progress(i + 1, total)
            out.save(dst, garbage=3, deflate=True)
            out.close()
        else:
            doc.save(dst, garbage=2, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
        return {"pages": len(targets), "total": total}
    finally:
        doc.close()
