"""Inline-keyboard screens for the user's watermark settings flow.

Labels preserve the bot's emoji accents; ``tgio.markup`` applies Telegram-supported button styles.
"""
from __future__ import annotations

import re

from .settings import (
    COLOR_ICONS, COLORS, FIELD_MAP, FIELDS, GROUPS, POSITION_ICONS, POSITIONS, WMSettings, Field,
    refresh_font_field_choices,
)
from .utils import esc

Btn = tuple[str, str] | tuple[str, str, str]
Screen = tuple[str, list[list[Btn]]]

GROUP_ICONS = {
    "text": "✍️",
    "style": "✨",
    "box": "🎨",
    "layout": "📍",
    "video": "🎬",
    "pdf": "📄",
    "general": "⚙️",
}
KIND_ICONS = {
    "bool": "🔘",
    "text": "✏️",
    "num": "🎚️",
    "int": "🎚️",
    "choice": "🔽",
    "color": "🎨",
}


def plain_label(value: str) -> str:
    """Normalize whitespace without removing deliberately supplied emojis."""
    return re.sub(r"\s+", " ", str(value)).strip()


def choice_label(f: Field, value: str) -> str:
    for v, label in f.choices:
        if v == value:
            return plain_label(label)
    return str(value)


def fmt_value(f: Field, s: WMSettings) -> str:
    v = getattr(s, f.key)
    if f.kind == "bool":
        return "ON" if v else "OFF"
    if f.kind in ("num", "int"):
        return f"{v:g}{f.unit}"
    if f.kind == "choice":
        if f.key in {"position", "pdf_position"}:
            return POSITION_NAMES.get(v, v)
        return choice_label(f, v)
    if f.kind == "color":
        return next((n.title() for n, h in COLORS.items() if h == v), v)
    if f.kind == "text":
        t = str(v).replace("\n", " / ")
        if f.key == "pdf_text" and not t.strip():
            return "Not set"
        return (t[:22] + "…") if len(t) > 22 else (t or "—")
    return str(v)


POSITION_NAMES = {
    "tl": "Top left", "tc": "Top", "tr": "Top right",
    "ml": "Left", "c": "Center", "mr": "Right",
    "bl": "Bottom left", "bc": "Bottom", "br": "Bottom right",
}


def summary(s: WMSettings) -> str:
    text = (s.text.replace("\n", " / ")[:48] or "—") if s.text_enabled else "Off"
    mode = choice_label(FIELD_MAP["mode"], s.mode)
    effects = []
    if s.bg_enabled:
        effects.append("box")
    if s.border_width > 0:
        effects.append("border")
    if s.stroke_width > 0:
        effects.append("outline")
    if s.shadow:
        effects.append("shadow")
    pdf_effects = []
    if s.pdf_bg_enabled:
        pdf_effects.append("box")
    if s.pdf_border_width > 0:
        pdf_effects.append("border")
    if s.pdf_stroke_width > 0:
        pdf_effects.append("outline")
    if s.pdf_shadow:
        pdf_effects.append("shadow")
    pages = s.pdf_pages or "all"
    return (
        "⚙️ <b>Watermark settings</b>\n\n"
        f"Video text: <code>{esc(text)}</code>\n"
        f"Video font/style: {esc(choice_label(FIELD_MAP['font'], s.font))} · {s.font_size:g}% · {s.text_opacity}% · {esc(', '.join(effects) or 'plain')}\n"
        f"Video position: {esc(mode)} · {esc(POSITION_NAMES.get(s.position, s.position))}\n\n"
        f"PDF text: <code>{esc(fmt_value(FIELD_MAP['pdf_text'], s))}</code>\n"
        f"PDF font/style: {esc(choice_label(FIELD_MAP['pdf_font'], s.pdf_font))} · {s.pdf_font_size:g}% · {s.pdf_text_opacity}% · {esc(', '.join(pdf_effects) or 'plain')}\n"
        f"PDF position: {esc(choice_label(FIELD_MAP['pdf_mode'], s.pdf_mode))} · {esc(POSITION_NAMES.get(s.pdf_position, s.pdf_position))}\n"
        f"Video output: CRF {s.crf} · {esc(s.preset)} · {esc(choice_label(FIELD_MAP['resolution'], s.resolution))}\n"
        f"PDF pages: {esc(pages)}"
    )


def main_menu(s: WMSettings) -> Screen:
    refresh_font_field_choices()
    refresh_font_field_choices("pdf_font")
    rows: list[list[Btn]] = []
    pair: list[Btn] = []
    for key, label in GROUPS:
        pair.append((f"{GROUP_ICONS.get(key, '⚙️')} {plain_label(label)}", f"g:{key}", "primary"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([("👁️ Preview", "pv", "primary"), ("♻️ Reset settings", "rs", "danger")])
    rows.append([("⬅️ Back to menu", "open:home", "primary")])
    return summary(s), rows


def group_menu(s: WMSettings, gkey: str) -> Screen:
    known = dict(GROUPS)
    if gkey not in known:
        return "<b>This setting is no longer available.</b>", [[("⬅️ Back to settings", "m", "primary")]]
    title = f"{GROUP_ICONS.get(gkey, '⚙️')} {plain_label(known[gkey])}"
    rows: list[list[Btn]] = []
    for f in FIELDS:
        if f.group != gkey:
            continue
        if f.key in {"bg_enabled", "pdf_bg_enabled"}:
            enabled = bool(getattr(s, f.key))
            prefix = "PDF background box" if f.key == "pdf_bg_enabled" else "Background box"
            rows.append([
                (f"🟢 {prefix} ON", f"bs:{f.key}:1", "success" if enabled else "default"),
                (f"⚫ {prefix} OFF", f"bs:{f.key}:0", "success" if not enabled else "default"),
            ])
            continue
        if f.kind == "bool":
            enabled = bool(getattr(s, f.key))
            state = "🟢 ON" if enabled else "⚫ OFF"
            label = f"🔘 {plain_label(f.label)} · {state}"
            style = "success" if enabled else "default"
        else:
            label = f"{KIND_ICONS.get(f.kind, '⚙️')} {plain_label(f.label)}: {fmt_value(f, s)}"
            style = "primary"
        rows.append([(label, _field_cb(f), style)])
    if gkey == "pdf":
        rows.append([("👁️ PDF preview", "ppv", "primary"), ("⬅️ Back", "m", "primary")])
    else:
        rows.append([("⬅️ Back", "m", "primary"), ("👁️ Preview", "pv", "primary")])
    return f"<b>{esc(title)}</b>\nChoose an option to change it.", rows


def _field_cb(f: Field) -> str:
    return {"bool": "b", "num": "n", "int": "n", "choice": "c", "color": "k", "text": "t"}[f.kind] + ":" + f.key


def num_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    v = getattr(s, key)
    txt = f"🎚️ <b>{esc(plain_label(f.label))}</b>\nCurrent: <b>{v:g}{f.unit}</b> <i>(range {f.lo:g}–{f.hi:g})</i>"
    if f.help:
        txt += f"\n\n{esc(plain_label(f.help))}"
    st = f.step
    rows = [
        [(f"➖{st * 5:g}", f"ns:{key}:-5", "primary"), (f"➖{st:g}", f"ns:{key}:-1", "primary"),
         (f"➕{st:g}", f"ns:{key}:1", "primary"), (f"➕{st * 5:g}", f"ns:{key}:5", "primary")],
        [("⌨️ Type a value", f"ni:{key}", "primary")],
        [("⬅️ Back", f"g:{f.group}", "primary"), ("👁️ Preview", "pv", "primary")],
    ]
    return txt, rows


def choice_menu(s: WMSettings, key: str) -> Screen:
    f = refresh_font_field_choices(key) if key in {"font", "pdf_font"} else FIELD_MAP[key]
    cur = getattr(s, key)
    txt = f"🔽 <b>{esc(plain_label(f.label))}</b>"
    if f.help:
        txt += f"\n{esc(plain_label(f.help))}"
    rows: list[list[Btn]] = []
    if key in {"position", "pdf_position"}:
        grid = [["tl", "tc", "tr"], ["ml", "c", "mr"], ["bl", "bc", "br"]]
        for row_positions in grid:
            row: list[Btn] = []
            for position in row_positions:
                selected = position == cur
                mark = "✅" if selected else "⚪"
                label = f"{mark} {POSITION_ICONS[position]} {POSITION_NAMES[position]}"
                row.append((label, f"cs:{key}:{POSITIONS.index(position)}", "success" if selected else "default"))
            rows.append(row)
    else:
        per_row = 1 if max(len(plain_label(label)) for _, label in f.choices) > 20 else 2
        row: list[Btn] = []
        for i, (value, label) in enumerate(f.choices):
            selected = value == cur
            mark = "✅" if selected else "▫️"
            row.append((f"{mark} {plain_label(label)}", f"cs:{key}:{i}", "success" if selected else "default"))
            if len(row) == per_row:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([("⬅️ Back", f"g:{f.group}", "primary"), ("👁️ Preview", "pv", "primary")])
    return txt, rows


def color_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    cur = getattr(s, key)
    txt = f"🎨 <b>{esc(plain_label(f.label))}</b>\nCurrent: <code>{cur}</code>\nPick a colour or send a HEX value such as <code>#FF8800</code>."
    names = list(COLORS)
    rows: list[list[Btn]] = []
    row: list[Btn] = []
    for name in names:
        selected = COLORS[name] == cur
        mark = "✅ " if selected else ""
        row.append((f"{mark}{COLOR_ICONS[name]} {name.title()}", f"ks:{key}:{name}", "success" if selected else "default"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("🎨 Custom HEX", f"ki:{key}", "primary")])
    rows.append([("⬅️ Back", f"g:{f.group}", "primary"), ("👁️ Preview", "pv", "primary")])
    return txt, rows


def confirm_reset() -> Screen:
    return "⚠️ <b>Reset your watermark settings to their defaults?</b>", [[("❌ Reset", "rsy", "danger"), ("⬅️ Back to settings", "m", "primary")]]


def confirm_job_card(job_name: str, kind: str, size_text: str) -> Screen:
    return (f"<b>{esc(job_name[:60])}</b>\n<code>{esc(size_text)}</code>\n\n"
            "Ready to watermark with your current text settings."), []
