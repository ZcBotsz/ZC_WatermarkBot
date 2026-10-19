"""User settings model.

`WMSettings` is the typed container; `FIELDS` is a declarative registry used by BOTH the validator
and the Telegram settings menus — add one `Field(...)` line and the control appears in the bot.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, asdict
from typing import Any

from . import config

# ──────────────────────────────────────────────────────────────────────────────────────────
# Choices
# ──────────────────────────────────────────────────────────────────────────────────────────
POSITIONS = ["tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br"]
POSITION_ICONS = {"tl": "↖", "tc": "⬆", "tr": "↗", "ml": "⬅", "c": "⏺", "mr": "➡", "bl": "↙", "bc": "⬇", "br": "↘"}

MODES = [
    ("static", "📌 Static"),
    ("scroll", "📜 Scrolling ticker"),
    ("bounce", "🏓 Bouncing"),
    ("hop", "🦘 Corner hop"),
    ("tile", "🔲 Tiled pattern"),
]
PRESETS_X264 = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"]
RESOLUTIONS = [("orig", "Original"), ("1080", "1080p"), ("720", "720p"), ("540", "540p"), ("480", "480p"), ("360", "360p")]

COLORS = {
    "white": "#FFFFFF", "black": "#000000", "red": "#FF3B30", "orange": "#FF9500", "yellow": "#FFD60A",
    "green": "#34C759", "cyan": "#32D4DE", "blue": "#0A84FF", "purple": "#BF5AF2", "pink": "#FF2D92",
    "gray": "#8E8E93",
}
COLOR_ICONS = {"white": "⚪", "black": "⚫", "red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢",
               "cyan": "🔵", "blue": "🔷", "purple": "🟣", "pink": "🌸", "gray": "🔘"}

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_HEX3 = re.compile(r"^#?([0-9a-fA-F]{3})$")


def parse_color(value: str) -> str | None:
    """Accept '#RRGGBB', 'RRGGBB', '#RGB' or a palette name. Returns '#RRGGBB' (upper) or None."""
    v = str(value).strip().lower()
    if v in COLORS:
        return COLORS[v]
    m = _HEX.match(v)
    if m:
        return "#" + m.group(1).upper()
    m = _HEX3.match(v)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1)).upper()
    return None


# ──────────────────────────────────────────────────────────────────────────────────────────
# Settings dataclass
# ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class WMSettings:
    # ── text ──
    text_enabled: bool = True
    text: str = field(default_factory=lambda: config.DEFAULT_WATERMARK_TEXT)
    font: str = "poppins-bold"
    font_size: float = 4.5            # % of the SHORT side of the frame
    text_color: str = "#FFFFFF"
    text_opacity: int = 90            # %
    uppercase: bool = False
    stroke_width: float = 0.0         # % of font size
    stroke_color: str = "#000000"
    shadow: bool = True
    shadow_color: str = "#000000"
    # ── box + border ──
    bg_enabled: bool = False
    bg_color: str = "#000000"
    bg_opacity: int = 55
    bg_padding: float = 45.0          # % of font size
    bg_radius: float = 30.0           # % of box height (50 = pill)
    border_width: float = 0.0         # % of font size (0 = no border)
    border_color: str = "#FFFFFF"
    border_opacity: int = 90
    # ── logo ──
    logo_enabled: bool = False        # needs an uploaded logo
    logo_size: float = 7.0            # % of SHORT side (logo height)
    logo_opacity: int = 90
    logo_side: str = "left"           # left/right/top/bottom of text
    # ── layout ──
    mode: str = "static"
    position: str = "br"
    margin: float = 2.5               # % of short side
    rotation: float = 0.0             # degrees (tile: angle of the whole pattern)
    speed: float = 8.0                # scroll/bounce speed, % of frame width per second
    hop_every: float = 5.0            # seconds between corner hops
    tile_gap: float = 60.0            # % of badge size
    show_every: float = 0.0           # seconds; 0 = always visible
    show_for: float = 5.0             # seconds visible per cycle when show_every > 0
    # ── video output ──
    crf: int = 23
    preset: str = field(default_factory=lambda: config.DEFAULT_PRESET)
    codec: str = "h264"
    resolution: str = "orig"
    audio: str = "copy"               # copy | aac128 | aac192
    container: str = "mp4"            # mp4 | mkv | same
    hw: str = "auto"                  # auto | off
    send_as: str = "auto"             # auto | video | document
    # ── pdf ──
    pdf_pages: str = "all"
    pdf_layer: str = "over"           # over | under
    pdf_flatten: bool = False
    pdf_strip_meta: bool = False
    pdf_auto_color: bool = True       # light text on white paper is invisible → darken automatically
    # ── general ──
    auto_start: bool = True
    caption_mode: str = "keep"        # keep | custom | none
    caption_text: str = ""
    filename_mode: str = "keep"       # keep | suffix | prefix
    filename_text: str = "_wm"

    # -- serialisation --------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WMSettings":
        s = cls()
        for k, v in (data or {}).items():
            if k in FIELD_MAP:
                s.set(k, v)
        return s

    def set(self, key: str, value: Any) -> bool:
        """Validate + clamp + assign. Returns True when accepted."""
        f = FIELD_MAP.get(key)
        if f is None:
            return False
        ok, v = f.coerce(value)
        if ok:
            setattr(self, key, v)
        return ok

    def copy(self) -> "WMSettings":
        return WMSettings.from_dict(self.to_dict())

    @property
    def has_visible_content(self) -> bool:
        return bool(self.text_enabled and self.text.strip()) or self.logo_enabled


# ──────────────────────────────────────────────────────────────────────────────────────────
# Field registry (drives validation AND the menus)
# ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str                       # bool | num | int | choice | color | text
    group: str
    choices: tuple[tuple[str, str], ...] = ()
    lo: float = 0
    hi: float = 100
    step: float = 1
    unit: str = ""
    help: str = ""
    maxlen: int = 200

    def coerce(self, value: Any) -> tuple[bool, Any]:
        k = self.kind
        try:
            if k == "bool":
                if isinstance(value, str):
                    return True, value.strip().lower() in {"1", "true", "yes", "on"}
                return True, bool(value)
            if k in ("num", "int"):
                v = float(value)
                v = max(self.lo, min(self.hi, v))
                return True, (int(round(v)) if k == "int" else round(v, 2))
            if k == "choice":
                valid = {c[0] for c in self.choices}
                v = str(value)
                return (v in valid), v
            if k == "color":
                c = parse_color(str(value))
                return (c is not None), c
            if k == "text":
                v = str(value).replace("\r", "")[: self.maxlen]
                return True, v
        except (TypeError, ValueError):
            pass
        return False, None


def _ch(*items: str | tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((i, i) if isinstance(i, str) else i for i in items)


def _fonts_choices() -> tuple[tuple[str, str], ...]:
    from .fonts import FONT_CHOICES  # late import to avoid cycles
    return tuple(FONT_CHOICES)


GROUPS = [
    ("text", "📝 Text"),
    ("style", "🎨 Text style"),
    ("box", "📦 Box & border"),
    ("logo", "🖼 Logo"),
    ("layout", "📍 Position & motion"),
    ("video", "🎬 Video output"),
    ("pdf", "📄 PDF options"),
    ("general", "⚙️ General"),
]

FIELDS: list[Field] = [
    # text
    Field("text_enabled", "Show text", "bool", "text"),
    Field("text", "Watermark text", "text", "text", help="Supports multiple lines, Hindi/Indic/Arabic scripts and emoji.", maxlen=300),
    Field("font", "Font", "choice", "text", choices=()),   # filled below
    Field("font_size", "Font size", "num", "text", lo=1, hi=30, step=0.5, unit="%", help="Relative to the short side of the video/page, so it looks the same at every resolution."),
    Field("text_color", "Text colour", "color", "text"),
    Field("text_opacity", "Text opacity", "int", "text", lo=5, hi=100, step=5, unit="%"),
    Field("uppercase", "UPPERCASE", "bool", "text"),
    # style
    Field("stroke_width", "Outline width", "num", "style", lo=0, hi=25, step=1, unit="%", help="Outline around the letters (% of font size)."),
    Field("stroke_color", "Outline colour", "color", "style"),
    Field("shadow", "Drop shadow", "bool", "style"),
    Field("shadow_color", "Shadow colour", "color", "style"),
    # box
    Field("bg_enabled", "Background box", "bool", "box"),
    Field("bg_color", "Box colour", "color", "box"),
    Field("bg_opacity", "Box opacity", "int", "box", lo=0, hi=100, step=5, unit="%"),
    Field("bg_padding", "Box padding", "num", "box", lo=0, hi=200, step=5, unit="%"),
    Field("bg_radius", "Corner radius", "num", "box", lo=0, hi=50, step=5, unit="%", help="50% makes a pill shape."),
    Field("border_width", "Border width", "num", "box", lo=0, hi=30, step=1, unit="%", help="Frame around the box (% of font size). 0 = no border."),
    Field("border_color", "Border colour", "color", "box"),
    Field("border_opacity", "Border opacity", "int", "box", lo=0, hi=100, step=5, unit="%"),
    # logo
    Field("logo_enabled", "Use logo", "bool", "logo", help="Upload a logo first (🖼 Upload logo)."),
    Field("logo_size", "Logo size", "num", "logo", lo=1, hi=60, step=1, unit="%", help="Logo height, % of the short side."),
    Field("logo_opacity", "Logo opacity", "int", "logo", lo=5, hi=100, step=5, unit="%"),
    Field("logo_side", "Logo placement", "choice", "logo", choices=_ch(("left", "◀ Left of text"), ("right", "▶ Right of text"), ("top", "▲ Above text"), ("bottom", "▼ Below text"))),
    # layout
    Field("mode", "Mode", "choice", "layout", choices=tuple(MODES)),
    Field("position", "Position", "choice", "layout", choices=_ch(*POSITIONS)),
    Field("margin", "Margin", "num", "layout", lo=0, hi=30, step=0.5, unit="%"),
    Field("rotation", "Rotation", "num", "layout", lo=-180, hi=180, step=5, unit="°", help="In Tiled mode this is the angle of the whole pattern (e.g. -30)."),
    Field("speed", "Motion speed", "num", "layout", lo=1, hi=60, step=1, unit="%/s", help="Scrolling / bouncing speed (% of frame width per second)."),
    Field("hop_every", "Hop interval", "num", "layout", lo=1, hi=120, step=1, unit="s"),
    Field("tile_gap", "Tile spacing", "num", "layout", lo=0, hi=400, step=10, unit="%"),
    Field("show_every", "Show every", "num", "layout", lo=0, hi=3600, step=5, unit="s", help="0 = always visible. Otherwise the watermark appears periodically (video only)."),
    Field("show_for", "Visible for", "num", "layout", lo=1, hi=600, step=1, unit="s"),
    # video
    Field("crf", "Quality (CRF)", "int", "video", lo=14, hi=36, step=1, help="Lower = better quality, bigger file. 18≈visually lossless, 23 default, 28 small."),
    Field("preset", "Speed preset", "choice", "video", choices=_ch(*PRESETS_X264), help="ultrafast = fastest & biggest file; slow = smallest file."),
    Field("codec", "Codec", "choice", "video", choices=_ch(("h264", "H.264 (fast, compatible)"), ("h265", "H.265 / HEVC (smaller, slower)"))),
    Field("resolution", "Resolution", "choice", "video", choices=tuple(RESOLUTIONS), help="Never upscales."),
    Field("audio", "Audio", "choice", "video", choices=_ch(("copy", "Keep original"), ("aac128", "AAC 128k"), ("aac192", "AAC 192k"))),
    Field("container", "Container", "choice", "video", choices=_ch(("mp4", "MP4 (streamable)"), ("mkv", "MKV (keep all tracks)"), ("same", "Same as input"))),
    Field("hw", "Hardware encoder", "choice", "video", choices=_ch(("auto", "Auto (GPU if available)"), ("off", "CPU only"))),
    Field("send_as", "Send result as", "choice", "video", choices=_ch(("auto", "Same as input"), ("video", "Video"), ("document", "File"))),
    # pdf
    Field("pdf_pages", "Pages", "text", "pdf", help="'all' or a range like 1-3,5,8-", maxlen=60),
    Field("pdf_layer", "Layer", "choice", "pdf", choices=_ch(("over", "Above content"), ("under", "Behind content"))),
    Field("pdf_flatten", "Flatten (anti-removal)", "bool", "pdf", help="Renders pages as images so the watermark can't be stripped. Larger file, text not selectable."),
    Field("pdf_strip_meta", "Strip metadata", "bool", "pdf"),
    Field("pdf_auto_color", "Auto-contrast text", "bool", "pdf", help="Light text on white pages is invisible — darkens it automatically for PDFs."),
    # general
    Field("auto_start", "Auto-start on upload", "bool", "general", help="ON: files are processed instantly. OFF: you confirm each file first."),
    Field("caption_mode", "Caption", "choice", "general", choices=_ch(("keep", "Keep original"), ("custom", "Custom text"), ("none", "No caption"))),
    Field("caption_text", "Custom caption", "text", "general", maxlen=900),
    Field("filename_mode", "File name", "choice", "general", choices=_ch(("keep", "Keep original"), ("suffix", "Add suffix"), ("prefix", "Add prefix"))),
    Field("filename_text", "Prefix / suffix text", "text", "general", maxlen=40),
]

# the font list is filled lazily (needs filesystem scan)
def _bind_font_choices() -> None:
    global FIELDS, FIELD_MAP
    out = []
    for f in FIELDS:
        if f.key == "font":
            f = Field(**{**f.__dict__, "choices": _fonts_choices()})
        out.append(f)
    FIELDS = out
    FIELD_MAP = {f.key: f for f in FIELDS}


FIELD_MAP: dict[str, Field] = {f.key: f for f in FIELDS}
_bind_font_choices()

# sanity: every dataclass field must be declared (guards against typos when adding settings)
assert {f.name for f in fields(WMSettings)} == set(FIELD_MAP), (
    {f.name for f in fields(WMSettings)} ^ set(FIELD_MAP))


# ──────────────────────────────────────────────────────────────────────────────────────────
# One-tap style presets
# ──────────────────────────────────────────────────────────────────────────────────────────
STYLE_PRESETS: dict[str, tuple[str, dict[str, Any]]] = {
    "classic": ("✨ Classic corner", dict(mode="static", position="br", font="poppins-bold", font_size=4.5, text_opacity=85,
                                          shadow=True, bg_enabled=False, border_width=0, stroke_width=0, rotation=0)),
    "label": ("🏷 Boxed label", dict(mode="static", position="tr", font="poppins-bold", font_size=4, text_color="#FFFFFF",
                                      bg_enabled=True, bg_color="#000000", bg_opacity=60, bg_padding=50, bg_radius=50,
                                      border_width=0, shadow=False, rotation=0)),
    "framed": ("🖼 Framed badge", dict(mode="static", position="bl", font="montserrat", font_size=4, bg_enabled=True,
                                       bg_color="#101010", bg_opacity=70, bg_radius=20, border_width=6, border_color="#FFD60A",
                                       border_opacity=100, text_color="#FFD60A", shadow=False, rotation=0)),
    "ghost": ("👻 Ghost centre", dict(mode="static", position="c", font="poppins-bold", font_size=10, text_opacity=25,
                                      shadow=False, bg_enabled=False, border_width=0, stroke_width=0, rotation=0)),
    "tiles": ("🔲 Diagonal tiles", dict(mode="tile", font="poppins-medium", font_size=3.5, text_opacity=22, rotation=-30,
                                        tile_gap=90, shadow=False, bg_enabled=False, border_width=0, stroke_width=0)),
    "ticker": ("📜 News ticker", dict(mode="scroll", position="bc", font="roboto", font_size=4, bg_enabled=True, bg_color="#B00020",
                                      bg_opacity=85, bg_radius=0, bg_padding=35, border_width=0, speed=9, shadow=False, rotation=0)),
    "bounce": ("🏓 Bouncing logo", dict(mode="bounce", font="poppins-bold", font_size=4, text_opacity=70, speed=7, shadow=True,
                                        bg_enabled=False, border_width=0, rotation=0)),
}


def apply_style_preset(s: WMSettings, key: str) -> bool:
    p = STYLE_PRESETS.get(key)
    if not p:
        return False
    for k, v in p[1].items():
        s.set(k, v)
    return True
