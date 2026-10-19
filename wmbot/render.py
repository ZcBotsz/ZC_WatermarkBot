"""Text-only watermark renderer (Pillow).

The text, outline, shadow, background box, border, rotation and tiling are drawn into a transparent
RGBA badge. It is reused by FFmpeg for video, PyMuPDF for PDFs and Pillow for previews.
"""
from __future__ import annotations

import math
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from . import fonts
from .settings import WMSettings

SUPERSAMPLE = 4
TEXT_SUPERSAMPLE = 2


def hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _pct(v: float) -> float:
    return max(0.0, min(100.0, v)) / 100.0


# ──────────────────────────────────────────────────────────────────────────────────────────
# Text
# ──────────────────────────────────────────────────────────────────────────────────────────
def _emoji_cluster_image(text: str, target_h: float) -> Image.Image | None:
    ef = fonts.emoji_font()
    if ef is None:
        return None
    w = max(160, int(ef.getlength(text)) + 40)
    tmp = Image.new("RGBA", (w, 180), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((10, 10), text, font=ef, embedded_color=True)
    bbox = tmp.getbbox()
    if not bbox:
        return None
    tmp = tmp.crop(bbox)
    scale = target_h / 128.0
    return tmp.resize((max(1, int(tmp.width * scale)), max(1, int(tmp.height * scale))), Image.LANCZOS)


def _render_text_layer_native(s: WMSettings, font_px: int) -> Image.Image | None:
    """Rasterize at the requested working resolution; called at 2× for normal-size text."""
    raw = s.text.upper() if s.uppercase else s.text
    raw = raw.strip("\n")
    if not raw.strip():
        return None

    sw = int(round(_pct(s.stroke_width) * font_px)) if s.stroke_width > 0 else 0
    text_rgb = hex_rgb(s.text_color)
    stroke_rgb = hex_rgb(s.stroke_color)
    spacing = int(font_px * 0.22)

    # 1. measure every line: list of (items, width, ascent, descent); item = (kind, text|image, width)
    lines = []
    for line in raw.split("\n"):
        items, width, asc, desc = [], 0.0, 0.0, 0.0
        for run in fonts.split_runs(line):
            if not run.text:
                continue
            if run.kind == "emoji":
                img = _emoji_cluster_image(run.text, font_px * 1.15)
                if img is None:      # no emoji font installed → drop emojis silently
                    continue
                items.append(("img", img, img.width + font_px * 0.08))
                width += img.width + font_px * 0.08
                asc = max(asc, font_px * 0.33 + img.height / 2)
                desc = max(desc, img.height / 2 - font_px * 0.33)
            else:
                f = fonts.get_font(s.font, font_px, run.kind)
                w = f.getlength(run.text)
                a, d = f.getmetrics()
                items.append(("txt", (run.text, f), w))
                width += w
                asc, desc = max(asc, a), max(desc, d)
        if not items:                 # blank line keeps its height
            f = fonts.get_font(s.font, font_px)
            a, d = f.getmetrics()
            asc, desc = a, d
        lines.append((items, width, asc, desc))

    if not any(it for it, *_ in lines):
        return None

    pad = sw + 2
    max_w = max(w for _, w, _, _ in lines)
    total_h = sum(a + d for _, _, a, d in lines) + spacing * (len(lines) - 1)
    W, H = int(math.ceil(max_w)) + 2 * pad, int(math.ceil(total_h)) + 2 * pad

    layer = Image.new("RGBA", (W, H), (*text_rgb, 0))
    draw = ImageDraw.Draw(layer)
    y = pad
    fill = (*text_rgb, 255)
    stroke_fill = (*stroke_rgb, 255)
    for items, width, asc, desc in lines:
        x = pad + (max_w - width) / 2           # centre-aligned lines
        base_y = y + asc
        for kind, payload, w in items:
            if kind == "txt":
                txt, f = payload
                draw.text((x, base_y), txt, font=f, fill=fill, anchor="ls",
                          stroke_width=sw, stroke_fill=stroke_fill)
            else:
                layer.alpha_composite(payload, (int(x), int(base_y - font_px * 0.33 - payload.height / 2)))
            x += w
        y += asc + desc + spacing

    # 2. shadow (from the text alpha, before opacity)
    if s.shadow:
        off = max(1, int(round(font_px * 0.07)))
        blur = max(1, int(round(font_px * 0.05)))
        margin = off + blur * 3
        big = Image.new("RGBA", (W + 2 * margin, H + 2 * margin), (0, 0, 0, 0))
        alpha = Image.new("L", big.size, 0)
        alpha.paste(layer.getchannel("A"), (margin + off, margin + off))
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur)).point(lambda a: int(a * 0.8))
        sh = Image.new("RGBA", big.size, (*hex_rgb(s.shadow_color), 0))
        sh.putalpha(alpha)
        big.alpha_composite(sh)
        big.alpha_composite(layer, (margin, margin))
        layer = big

    # 3. opacity
    op = _pct(s.text_opacity)
    if op < 1:
        a = layer.getchannel("A").point(lambda v: int(v * op))
        layer.putalpha(a)
    return layer


def render_text_layer(s: WMSettings, font_px: int) -> Image.Image | None:
    """Supersample text edges, then downsample once for cleaner video/PDF overlays."""
    scale = TEXT_SUPERSAMPLE if font_px <= 96 else 1
    layer = _render_text_layer_native(s, font_px * scale)
    if layer is None or scale == 1:
        return layer
    target = (max(1, round(layer.width / scale)), max(1, round(layer.height / scale)))
    return layer.resize(target, Image.Resampling.LANCZOS)


# ──────────────────────────────────────────────────────────────────────────────────────────
# Text badge
# ──────────────────────────────────────────────────────────────────────────────────────────
def _box(content: Image.Image, s: WMSettings, unit: float) -> Image.Image:
    pad = int(round(s.bg_padding / 100.0 * unit))
    bw = max(1, int(round(s.border_width / 100.0 * unit))) if s.border_width > 0 else 0
    pad += bw
    W, H = content.width + 2 * pad, content.height + 2 * pad
    ss = SUPERSAMPLE
    radius = int(min(W, H) * (s.bg_radius / 100.0) * ss)
    layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fill = (*hex_rgb(s.bg_color), int(255 * _pct(s.bg_opacity))) if s.bg_enabled else None
    outline = (*hex_rgb(s.border_color), int(255 * _pct(s.border_opacity))) if bw else None
    # avoid transparent-black fringes when downscaling: seed colour channels
    bg_rgb = hex_rgb(s.bg_color if s.bg_enabled else s.border_color)
    layer = Image.new("RGBA", layer.size, (*bg_rgb, 0))
    d = ImageDraw.Draw(layer)
    rect = [0, 0, W * ss - 1, H * ss - 1]
    if radius > 0:
        d.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=bw * ss if bw else 0)
    else:
        d.rectangle(rect, fill=fill, outline=outline, width=bw * ss if bw else 0)
    box = layer.resize((W, H), Image.LANCZOS)
    box.alpha_composite(content, (pad, pad))
    return box


def render_badge(s: WMSettings, base_px: int, rotate: bool = True) -> Image.Image | None:
    """Render the text watermark for a canvas whose SHORT side is `base_px` pixels."""
    font_px = max(8, int(round(s.font_size / 100.0 * base_px)))
    content = render_text_layer(s, font_px) if s.text_enabled else None
    if content is None:
        return None
    unit = font_px

    # trim empty margins so alignment/margins are exact
    bbox = content.getchannel("A").getbbox()
    if bbox:
        content = content.crop(bbox)

    badge = content
    if s.bg_enabled or s.border_width > 0:
        badge = _box(content, s, unit)

    if rotate and s.mode != "tile" and abs(s.rotation) > 0.01:
        badge = badge.rotate(-s.rotation, expand=True, resample=Image.BICUBIC)
        bb = badge.getchannel("A").getbbox()
        if bb:
            badge = badge.crop(bb)
    return badge


# ──────────────────────────────────────────────────────────────────────────────────────────
# Placement
# ──────────────────────────────────────────────────────────────────────────────────────────
def pos_xy(position: str, W: int, H: int, w: int, h: int, margin: int) -> tuple[int, int]:
    if position == "c":
        row, col = "m", "c"
    else:
        row, col = position[0], position[1]
    x = {"l": margin, "c": (W - w) // 2, "r": W - w - margin}[col]
    y = {"t": margin, "m": (H - h) // 2, "b": H - h - margin}[row]
    return max(0, x), max(0, y)


def render_tile_layer(s: WMSettings, W: int, H: int) -> Image.Image | None:
    """Full-frame transparent layer with the text badge repeated on a staggered, rotated grid."""
    base = min(W, H)
    badge = render_badge(s, base, rotate=False)
    if badge is None:
        return None
    angle = -s.rotation                       # positive = clockwise (same convention as static)
    rb = badge.rotate(angle, expand=True, resample=Image.BICUBIC) if abs(angle) > 0.01 else badge
    bw, bh = badge.size
    gap = s.tile_gap / 100.0 * bh * 2
    sx, sy = bw + gap * 2, bh + gap
    diag = math.hypot(W, H)
    cx, cy = W / 2.0, H / 2.0
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    nx, ny = int(diag / sx) + 2, int(diag / sy) + 2
    for j in range(-ny, ny + 1):
        for i in range(-nx, nx + 1):
            ox = i * sx + (sx / 2 if j % 2 else 0)
            oy = j * sy
            # PIL rotates counter-clockwise by `angle`; screen y axis points down
            px = cx + ox * cos_a + oy * sin_a
            py = cy - ox * sin_a + oy * cos_a
            x0, y0 = int(px - rb.width / 2), int(py - rb.height / 2)
            if x0 > W or y0 > H or x0 + rb.width < 0 or y0 + rb.height < 0:
                continue
            _paste_clipped(layer, rb, x0, y0)
    return layer


def _paste_clipped(layer: Image.Image, im: Image.Image, x: int, y: int) -> None:
    W, H = layer.size
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(im.width, W - x), min(im.height, H - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    layer.alpha_composite(im, (x + sx0, y + sy0), (sx0, sy0, sx1, sy1))


def apply_to_image(img: Image.Image, s: WMSettings) -> Image.Image:
    """Composite the text watermark onto a preview image. Moving modes render at their starting position."""
    base = img.convert("RGBA")
    W, H = base.size
    if s.mode == "tile":
        layer = render_tile_layer(s, W, H)
        if layer is not None:
            base.alpha_composite(layer)
        return base
    badge = render_badge(s, min(W, H))
    if badge is None:
        return base
    margin = int(round(s.margin / 100.0 * min(W, H)))
    x, y = pos_xy(s.position, W, H, badge.width, badge.height, margin)
    _paste_clipped(base, badge, x, y)
    return base


def sample_canvas(W: int, H: int) -> Image.Image:
    """Photo-like gradient used when we have no real frame to preview on."""
    W, H = max(64, W), max(64, H)
    small = Image.linear_gradient("L").resize((W, H))
    r = ImageOps.colorize(small, (24, 40, 80), (235, 170, 110))
    noise = ImageChops.add(r, Image.effect_noise((W, H), 12).convert("RGB"), 1, -128)
    return noise.convert("RGB")
