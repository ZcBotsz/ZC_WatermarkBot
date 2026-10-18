"""Inline-keyboard screens for the settings UI. Pure functions: (settings) → (text, buttons).

Buttons are plain `(label, callback_data)` tuples so this module has no Telegram dependency and can be
unit-tested; `tgio.markup()` converts them to pyrogram objects.
"""
from __future__ import annotations

from . import config
from .settings import (COLOR_ICONS, COLORS, FIELD_MAP, FIELDS, GROUPS, POSITION_ICONS, POSITIONS, STYLE_PRESETS, WMSettings, Field)
from .utils import esc

Btn = tuple[str, str]
Screen = tuple[str, list[list[Btn]]]


# ── value formatting ─────────────────────────────────────────────────────────────────────
def choice_label(f: Field, value: str) -> str:
    for v, label in f.choices:
        if v == value:
            return label
    return str(value)


def fmt_value(f: Field, s: WMSettings) -> str:
    v = getattr(s, f.key)
    if f.kind == "bool":
        return "✅ ON" if v else "⬜ OFF"
    if f.kind in ("num", "int"):
        return f"{v:g}{f.unit}"
    if f.kind == "choice":
        if f.key == "position":
            return f"{POSITION_ICONS.get(v, '')} {v}"
        return choice_label(f, v)
    if f.kind == "color":
        name = next((n for n, h in COLORS.items() if h == v), None)
        return f"{COLOR_ICONS.get(name, '🎨')} {name or v}"
    if f.kind == "text":
        t = str(v).replace("\n", " ⏎ ")
        return (t[:22] + "…") if len(t) > 22 else (t or "—")
    return str(v)


def _short(label: str, value: str) -> str:
    return f"{label}: {value}"


# ── screens ──────────────────────────────────────────────────────────────────────────────
def summary(s: WMSettings, has_logo: bool) -> str:
    text = (s.text.replace("\n", " ⏎ ")[:40] or "—") if s.text_enabled else "off"
    mode = dict(FIELD_MAP["mode"].choices)[s.mode]
    box = []
    if s.bg_enabled:
        box.append("box")
    if s.border_width > 0:
        box.append("border")
    if s.stroke_width > 0:
        box.append("outline")
    if s.shadow:
        box.append("shadow")
    logo = ("on" if s.logo_enabled else "off") if has_logo else "none uploaded"
    return (
        f"⚙️ <b>Watermark settings</b>\n\n"
        f"📝 Text: <code>{esc(text)}</code>\n"
        f"🔤 {choice_label(FIELD_MAP['font'], s.font)} · {s.font_size:g}% · {s.text_opacity}% opacity\n"
        f"🎨 {', '.join(box) or 'plain'}\n"
        f"🖼 Logo: {logo}\n"
        f"📍 {mode} · {s.position}\n"
        f"🎬 CRF {s.crf} · {s.preset} · {choice_label(FIELD_MAP['codec'], s.codec).split(' ')[0]} · "
        f"{choice_label(FIELD_MAP['resolution'], s.resolution)}\n"
        f"⚡ Auto-start: {'ON' if s.auto_start else 'OFF'}"
    )


def main_menu(s: WMSettings, has_logo: bool) -> Screen:
    rows: list[list[Btn]] = []
    pair: list[Btn] = []
    for key, label in GROUPS:
        pair.append((label, f"g:{key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([("🎨 Quick styles", "p"), ("👁 Preview", "pv")])
    rows.append([("🖼 Logo", "lg"), ("♻️ Reset", "rs")])
    rows.append([("✖ Close", "x")])
    return summary(s, has_logo), rows


def group_menu(s: WMSettings, gkey: str) -> Screen:
    title = dict(GROUPS)[gkey]
    rows: list[list[Btn]] = []
    for f in FIELDS:
        if f.group != gkey:
            continue
        label = _short(f.label, fmt_value(f, s))
        rows.append([(label, _field_cb(f))])
    rows.append([("⬅️ Back", "m"), ("👁 Preview", "pv")])
    return f"<b>{title}</b>\nTap an option to change it.", rows


def _field_cb(f: Field) -> str:
    return {"bool": "b", "num": "n", "int": "n", "choice": "c", "color": "k", "text": "t"}[f.kind] + ":" + f.key


def num_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    v = getattr(s, key)
    txt = f"<b>{f.label}</b>\nCurrent: <b>{v:g}{f.unit}</b>   <i>(range {f.lo:g}–{f.hi:g})</i>"
    if f.help:
        txt += f"\n\n{esc(f.help)}"
    st = f.step
    rows = [[(f"−{st * 5:g}", f"ns:{key}:-5"), (f"−{st:g}", f"ns:{key}:-1"), (f"+{st:g}", f"ns:{key}:1"), (f"+{st * 5:g}", f"ns:{key}:5")],
            [("✍️ Type a value", f"ni:{key}")],
            [("⬅️ Back", f"g:{f.group}"), ("👁 Preview", "pv")]]
    return txt, rows


def choice_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    cur = getattr(s, key)
    txt = f"<b>{f.label}</b>"
    if f.help:
        txt += f"\n{esc(f.help)}"
    rows: list[list[Btn]] = []
    if key == "position":
        grid = [["tl", "tc", "tr"], ["ml", "c", "mr"], ["bl", "bc", "br"]]
        for r in grid:
            rows.append([((("✅ " if p == cur else "") + POSITION_ICONS[p]), f"cs:{key}:{POSITIONS.index(p)}") for p in r])
    else:
        per_row = 1 if max(len(label) for _, label in f.choices) > 20 else 2
        row: list[Btn] = []
        for i, (v, label) in enumerate(f.choices):
            row.append((("✅ " if v == cur else "") + label, f"cs:{key}:{i}"))
            if len(row) == per_row:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([("⬅️ Back", f"g:{f.group}"), ("👁 Preview", "pv")])
    return txt, rows


def color_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    cur = getattr(s, key)
    txt = f"<b>{f.label}</b>\nCurrent: <code>{cur}</code>\nPick a colour or send your own HEX (e.g. <code>#FF8800</code>)."
    names = list(COLORS)
    rows, row = [], []
    for n in names:
        mark = "✅" if COLORS[n] == cur else COLOR_ICONS[n]
        row.append((f"{mark} {n}", f"ks:{key}:{n}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("✍️ Custom HEX", f"ki:{key}")])
    rows.append([("⬅️ Back", f"g:{f.group}"), ("👁 Preview", "pv")])
    return txt, rows


def presets_menu() -> Screen:
    rows = [[(label, f"ps:{k}")] for k, (label, _) in STYLE_PRESETS.items()]
    rows.append([("⬅️ Back", "m"), ("👁 Preview", "pv")])
    return ("🎨 <b>Quick styles</b>\nOne tap sets the look (your text, logo and video quality stay as they are).", rows)


def logo_menu(s: WMSettings, has_logo: bool) -> Screen:
    txt = "🖼 <b>Logo</b>\n" + ("A logo is uploaded." if has_logo else "No logo uploaded yet.") + \
          "\nSend a transparent PNG for the best result."
    rows = [[("📤 Upload / replace logo", "lgu")]]
    if has_logo:
        rows.append([(f"Use logo: {'✅ ON' if s.logo_enabled else '⬜ OFF'}", "b:logo_enabled")])
        rows.append([(f"Size: {s.logo_size:g}%", "n:logo_size"), (f"Opacity: {s.logo_opacity}%", "n:logo_opacity")])
        rows.append([(f"Placement: {choice_label(FIELD_MAP['logo_side'], s.logo_side)}", "c:logo_side")])
        rows.append([("🗑 Remove logo", "lgd")])
    rows.append([("⬅️ Back", "m"), ("👁 Preview", "pv")])
    return txt, rows


def confirm_reset() -> Screen:
    return "♻️ <b>Reset all settings to defaults?</b>", [[("Yes, reset", "rsy"), ("Cancel", "m")]]


def confirm_job_card(job_name: str, kind: str, size_text: str) -> Screen:
    return (f"{ {'video': '🎬', 'pdf': '📄', 'image': '🖼'}.get(kind, '📦') } <b>{esc(job_name[:60])}</b>\n<code>{size_text}</code>\n\n"
            "Ready to watermark with your current settings."), []
