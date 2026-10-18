"""Font registry + script-aware text runs.

* Bundled OFL fonts live in assets/fonts (see assets/fonts/README.md for licences).
* Text is split into *runs* (Latin / Devanagari / Tamil / Arabic / CJK / emoji …) so a mixed string
  such as "Join @channel • नमस्ते 🎬" is drawn with a font that really has each glyph.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from . import config

# key → (label, file name, variable-font weight or None)
_BUNDLED: dict[str, tuple[str, str, int | None]] = {
    "poppins-bold": ("Poppins Bold", "Poppins-Bold.ttf", None),
    "poppins-medium": ("Poppins Medium", "Poppins-Medium.ttf", None),
    "montserrat": ("Montserrat Bold", "Montserrat-Var.ttf", 700),
    "roboto": ("Roboto Bold", "Roboto-Var.ttf", 700),
    "noto-sans": ("Noto Sans Bold", "NotoSans-Var.ttf", 700),
    "anton": ("Anton (condensed)", "Anton-Regular.ttf", None),
    "bebas": ("Bebas Neue", "BebasNeue-Regular.ttf", None),
    "orbitron": ("Orbitron (tech)", "Orbitron-Var.ttf", 700),
    "pacifico": ("Pacifico (script)", "Pacifico-Regular.ttf", None),
}

_SYSTEM_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

# script → bundled file
_SCRIPT_FILES: dict[str, str] = {
    "deva": "NotoSansDevanagari-Var.ttf", "beng": "NotoSansBengali-Var.ttf", "guru": "NotoSansGurmukhi-Var.ttf",
    "gujr": "NotoSansGujarati-Var.ttf", "taml": "NotoSansTamil-Var.ttf", "telu": "NotoSansTelugu-Var.ttf",
    "knda": "NotoSansKannada-Var.ttf", "mlym": "NotoSansMalayalam-Var.ttf", "arab": "NotoSansArabic-Var.ttf",
}
_CJK_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
]
_EMOJI_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf", "/usr/share/fonts/truetype/NotoColorEmoji.ttf",
]
EMOJI_BITMAP_SIZE = 109      # the only size CBDT colour-emoji fonts can be loaded at


def _find_bundled(name: str) -> Path | None:
    for d in config.FONT_DIRS[:2]:
        p = Path(d) / name
        if p.exists():
            return p
    return None


def _first_existing(paths) -> Path | None:
    for p in paths:
        if Path(p).exists():
            return Path(p)
    return None


def _custom_fonts() -> dict[str, Path]:
    """User-supplied fonts: any .ttf/.otf in DATA_DIR/fonts → key "my-<name>"."""
    out: dict[str, Path] = {}
    d = Path(config.DATA_DIR) / "fonts"
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf") and len(out) < 20:
                out["my-" + re.sub(r"[^a-z0-9]+", "-", f.stem.lower()).strip("-")[:30]] = f
    return out


CUSTOM_FONTS = _custom_fonts()


def _build_choices() -> list[tuple[str, str]]:
    out = [(k, v[0]) for k, v in _BUNDLED.items() if _find_bundled(v[1])]
    if not out and _first_existing(_SYSTEM_FALLBACKS):
        out.append(("system", "System Sans Bold"))
    if not out:
        out.append(("builtin", "Built-in"))
    out += [(k, "★ " + p.stem[:24]) for k, p in CUSTOM_FONTS.items()]
    return out


FONT_CHOICES: list[tuple[str, str]] = _build_choices()


def default_font_key() -> str:
    return FONT_CHOICES[0][0]


# ── loading ──────────────────────────────────────────────────────────────────────────────
def _apply_weight(font: ImageFont.FreeTypeFont, weight: int | None) -> ImageFont.FreeTypeFont:
    if weight is None:
        return font
    try:
        axes = font.get_variation_axes()
        vals = []
        for a in axes:
            name = a["name"].decode() if isinstance(a["name"], bytes) else str(a["name"])
            if name.lower().startswith("weight"):
                vals.append(max(a["minimum"], min(a["maximum"], weight)))
            else:
                vals.append(a["default"])
        font.set_variation_by_axes(vals)
    except Exception:  # not a variable font / FreeType without MM support
        pass
    return font


@functools.lru_cache(maxsize=256)
def _load(path: str, px: int, weight: int | None, index: int = 0) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, px, index=index)
    return _apply_weight(f, weight)


def get_font(key: str, px: int, script: str = "base") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Font object for a font key + script run kind."""
    px = max(6, int(px))
    if script in _SCRIPT_FILES:
        p = _find_bundled(_SCRIPT_FILES[script])
        if p:
            return _load(str(p), px, 700)
    if script == "cjk":
        p = _first_existing(_CJK_CANDIDATES)
        if p:
            return _load(str(p), px, None)
    if key in CUSTOM_FONTS:
        try:
            return _load(str(CUSTOM_FONTS[key]), px, None)
        except OSError:
            pass
    if key in _BUNDLED:
        label, fname, weight = _BUNDLED[key]
        p = _find_bundled(fname)
        if p:
            return _load(str(p), px, weight)
    p = _first_existing(_SYSTEM_FALLBACKS)
    if p:
        return _load(str(p), px, None)
    try:
        return ImageFont.load_default(px)  # Pillow ≥ 10.1
    except TypeError:                      # pragma: no cover
        return ImageFont.load_default()


def emoji_font_path() -> Path | None:
    p = _find_bundled("NotoColorEmoji.ttf") or _first_existing(_EMOJI_CANDIDATES)
    if not p:
        return None
    try:   # verify it is a bitmap (CBDT) font Pillow can really draw
        ImageFont.truetype(str(p), EMOJI_BITMAP_SIZE)
        return p
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def emoji_font() -> ImageFont.FreeTypeFont | None:
    p = emoji_font_path()
    return ImageFont.truetype(str(p), EMOJI_BITMAP_SIZE) if p else None


# ── script detection ────────────────────────────────────────────────────────────────────
_RANGES: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "deva"), (0xA8E0, 0xA8FF, "deva"), (0x0980, 0x09FF, "beng"), (0x0A00, 0x0A7F, "guru"),
    (0x0A80, 0x0AFF, "gujr"), (0x0B80, 0x0BFF, "taml"), (0x0C00, 0x0C7F, "telu"), (0x0C80, 0x0CFF, "knda"),
    (0x0D00, 0x0D7F, "mlym"), (0x0600, 0x06FF, "arab"), (0x0750, 0x077F, "arab"), (0xFB50, 0xFDFF, "arab"),
    (0xFE70, 0xFEFF, "arab"),
    (0x2E80, 0x2FDF, "cjk"), (0x3000, 0x30FF, "cjk"), (0x3100, 0x312F, "cjk"), (0x3400, 0x4DBF, "cjk"),
    (0x4E00, 0x9FFF, "cjk"), (0xAC00, 0xD7AF, "cjk"), (0xFF00, 0xFFEF, "cjk"),
]
_EMOJI_RANGES = [
    (0x1F300, 0x1FAFF), (0x1F000, 0x1F2FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0x2300, 0x23FF),
    (0x1F1E6, 0x1F1FF), (0x2190, 0x21FF), (0x2934, 0x2935), (0x3030, 0x3030), (0x303D, 0x303D),
    (0x3297, 0x3299), (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049),
    (0x2122, 0x2122), (0x2139, 0x2139),
]
_JOINERS = {0x200D, 0xFE0F, 0xFE0E, 0x20E3}
_ZW_NONPRINT = {0x200C, 0x200D}


def _kind(ch: str) -> str | None:
    """Script/emoji kind of a character, or None for neutral (space, digits, punctuation, marks)."""
    cp = ord(ch)
    if cp in _JOINERS or 0x1F3FB <= cp <= 0x1F3FF:
        return "joiner"
    for lo, hi in _EMOJI_RANGES:
        if lo <= cp <= hi:
            return "emoji"
    for lo, hi, name in _RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isalpha():
        return "base"
    return None


@dataclass
class Run:
    text: str
    kind: str      # base | deva | … | emoji


def split_runs(text: str) -> list[Run]:
    runs: list[Run] = []
    cur_kind: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf and cur_kind:
            runs.append(Run("".join(buf), cur_kind))
        buf = []

    for ch in text:
        k = _kind(ch)
        if k == "joiner":
            k = cur_kind if cur_kind == "emoji" else None
            if k is None and ord(ch) in (0x200C, 0x200D):      # joiners inside Indic conjuncts
                k = cur_kind
        if k is None:                       # neutral: stay in the current run
            if cur_kind is None:
                cur_kind = "base"
            buf.append(ch)
            continue
        if cur_kind is None:
            cur_kind = k
        if k != cur_kind:
            # neutral characters trailing at the end of a run (e.g. spaces) belong to the old run
            flush()
            cur_kind = k
        buf.append(ch)
    flush()
    return runs or [Run("", "base")]


def has_emoji(text: str) -> bool:
    return any(_kind(c) == "emoji" for c in text)
