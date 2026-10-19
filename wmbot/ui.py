"""Text-only inline-keyboard screens for the user settings flow.

Button labels are normalized to emoji-free small caps and Telegram button colors in ``tgio.markup``.
"""
from __future__ import annotations

import re

from .settings import COLORS, FIELD_MAP, FIELDS, GROUPS, POSITIONS, WMSettings, Field
from .utils import esc

Btn = tuple[str, str]
Screen = tuple[str, list[list[Btn]]]

_EMOJI = re.compile(
    "[\\U0001F000-\\U0001FAFF\\U0001F1E6-\\U0001F1FF\\u2300-\\u23FF\\u2600-\\u27BF\\uFE0F\\u200D]"
)


def plain_label(value: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI.sub("", str(value))).strip()


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
        if f.key == "position":
            return POSITION_NAMES.get(v, v)
        return choice_label(f, v)
    if f.kind == "color":
        return next((n.title() for n, h in COLORS.items() if h == v), v)
    if f.kind == "text":
        t = str(v).replace("\n", " / ")
        return (t[:22] + "…") if len(t) > 22 else (t or "—")
    return str(v)


POSITION_NAMES = {
    "tl": "Top left", "tc": "Top", "tr": "Top right",
    "ml": "Left", "c": "Center", "mr": "Right",
    "bl": "Bottom left", "bc": "Bottom", "br": "Bottom right",
}


def _short(label: str, value: str) -> str:
    return f"{plain_label(label)}: {value}"


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
    pages = s.pdf_pages or "all"
    return (
        "<b>Watermark settings</b>\n\n"
        f"Text: <code>{esc(text)}</code>\n"
        f"Font: {esc(choice_label(FIELD_MAP['font'], s.font))} · {s.font_size:g}% · {s.text_opacity}%\n"
        f"Style: {esc(', '.join(effects) or 'plain')}\n"
        f"Position: {esc(mode)} · {esc(POSITION_NAMES.get(s.position, s.position))}\n"
        f"Video: CRF {s.crf} · {esc(s.preset)} · {esc(choice_label(FIELD_MAP['resolution'], s.resolution))}\n"
        f"PDF pages: {esc(pages)}"
    )


def main_menu(s: WMSettings) -> Screen:
    rows: list[list[Btn]] = []
    pair: list[Btn] = []
    for key, label in GROUPS:
        pair.append((plain_label(label), f"g:{key}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([("Preview", "pv"), ("Reset settings", "rs")])
    rows.append([("Close", "x")])
    return summary(s), rows


def group_menu(s: WMSettings, gkey: str) -> Screen:
    known = dict(GROUPS)
    if gkey not in known:
        return "<b>This setting is no longer available.</b>", [[("Back to settings", "m")]]
    title = plain_label(known[gkey])
    rows: list[list[Btn]] = []
    for f in FIELDS:
        if f.group != gkey:
            continue
        label = _short(f.label, fmt_value(f, s))
        rows.append([(label, _field_cb(f))])
    rows.append([("Back", "m"), ("Preview", "pv")])
    return f"<b>{esc(title)}</b>\nChoose an option to change it.", rows


def _field_cb(f: Field) -> str:
    return {"bool": "b", "num": "n", "int": "n", "choice": "c", "color": "k", "text": "t"}[f.kind] + ":" + f.key


def num_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    v = getattr(s, key)
    txt = f"<b>{esc(plain_label(f.label))}</b>\nCurrent: <b>{v:g}{f.unit}</b> <i>(range {f.lo:g}–{f.hi:g})</i>"
    if f.help:
        txt += f"\n\n{esc(plain_label(f.help))}"
    st = f.step
    rows = [
        [(f"−{st * 5:g}", f"ns:{key}:-5"), (f"−{st:g}", f"ns:{key}:-1"), (f"+{st:g}", f"ns:{key}:1"), (f"+{st * 5:g}", f"ns:{key}:5")],
        [("Type a value", f"ni:{key}")],
        [("Back", f"g:{f.group}"), ("Preview", "pv")],
    ]
    return txt, rows


def choice_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    cur = getattr(s, key)
    txt = f"<b>{esc(plain_label(f.label))}</b>"
    if f.help:
        txt += f"\n{esc(plain_label(f.help))}"
    rows: list[list[Btn]] = []
    if key == "position":
        grid = [["tl", "tc", "tr"], ["ml", "c", "mr"], ["bl", "bc", "br"]]
        for r in grid:
            rows.append([(("* " if p == cur else "") + POSITION_NAMES[p], f"cs:{key}:{POSITIONS.index(p)}") for p in r])
    else:
        per_row = 1 if max(len(plain_label(label)) for _, label in f.choices) > 20 else 2
        row: list[Btn] = []
        for i, (v, label) in enumerate(f.choices):
            row.append((("* " if v == cur else "") + plain_label(label), f"cs:{key}:{i}"))
            if len(row) == per_row:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([("Back", f"g:{f.group}"), ("Preview", "pv")])
    return txt, rows


def color_menu(s: WMSettings, key: str) -> Screen:
    f = FIELD_MAP[key]
    cur = getattr(s, key)
    txt = f"<b>{esc(plain_label(f.label))}</b>\nCurrent: <code>{cur}</code>\nPick a colour or send a HEX value such as <code>#FF8800</code>."
    names = list(COLORS)
    rows, row = [], []
    for name in names:
        mark = "* " if COLORS[name] == cur else ""
        row.append((f"{mark}{name.title()}", f"ks:{key}:{name}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("Custom HEX", f"ki:{key}")])
    rows.append([("Back", f"g:{f.group}"), ("Preview", "pv")])
    return txt, rows


def confirm_reset() -> Screen:
    return "<b>Reset your watermark settings to their defaults?</b>", [[("Reset", "rsy"), ("Cancel", "m")]]


def confirm_job_card(job_name: str, kind: str, size_text: str) -> Screen:
    return (f"<b>{esc(job_name[:60])}</b>\n<code>{esc(size_text)}</code>\n\n"
            "Ready to watermark with your current text settings."), []
