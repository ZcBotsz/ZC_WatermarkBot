"""All inline-button callbacks (settings menus, job confirm/cancel) + typed-value input."""
from __future__ import annotations

import io
import logging

from PIL import Image, ImageOps
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, ForceReply, Message

from .. import ui
from ..db import db
from ..jobs import render_card
from ..settings import COLORS, FIELD_MAP, POSITIONS, WMSettings, apply_style_preset, parse_color
from ..tgio import markup, safe_edit
from ..utils import esc
from . import access
from .ctx import Awaiting, ctx
from .user import HELP, open_menu, send_preview

log = logging.getLogger(__name__)
H = ParseMode.HTML


async def _render(cq: CallbackQuery, screen):
    text, rows = screen
    await safe_edit(cq.message.edit_text, text, parse_mode=H, reply_markup=markup(rows))


def _screen_for(back: str, s: WMSettings, has_logo: bool):
    """Re-create a screen from its callback code."""
    kind, _, arg = back.partition(":")
    return {
        "m": lambda: ui.main_menu(s, has_logo),
        "g": lambda: ui.group_menu(s, arg),
        "n": lambda: ui.num_menu(s, arg),
        "c": lambda: ui.choice_menu(s, arg),
        "k": lambda: ui.color_menu(s, arg),
        "p": ui.presets_menu,
        "lg": lambda: ui.logo_menu(s, has_logo),
    }[kind]()


async def _begin_input(cq: CallbackQuery, key: str, kind: str, back: str, hint: str):
    uid = cq.from_user.id
    old = ctx.awaiting.pop(uid, None)
    if old:
        try:
            await ctx.app.delete_messages(old.chat_id, old.prompt_msg_id)
        except Exception:
            pass
    name = FIELD_MAP[key].label if key in FIELD_MAP else "logo"
    mention = f'<a href="tg://user?id={uid}">{esc(cq.from_user.first_name or "you")}</a>'
    prompt = await cq.message.reply_text(
        f"✍️ {mention}, send the new value for <b>{esc(name)}</b>.\n{hint}\n<i>/cancel to abort</i>", parse_mode=H,
        reply_markup=ForceReply(selective=True, placeholder="type here…"))
    ctx.awaiting[uid] = Awaiting(key, kind, cq.message.chat.id, cq.message.id, prompt.id, back)


async def on_callback(client: Client, cq: CallbackQuery):
    user, msg = cq.from_user, cq.message
    data = cq.data or ""
    if msg is None:
        await cq.answer()
        return
    if not access.allowed(user, msg.chat):
        await cq.answer("🔒 Not allowed", show_alert=True)
        return
    uid = user.id

    # ── job buttons ──
    if data.startswith("cx:"):
        job = ctx.pipeline.jobs.get(data[3:])
        if not job:
            await cq.answer("Already finished.")
        elif job.user_id != uid and not access.is_admin(uid):
            await cq.answer("This isn't your job.", show_alert=True)
        else:
            ctx.pipeline.cancel(job.id)
            await cq.answer("Cancelling…")
        return
    if data.startswith(("go:", "no:")):
        job = ctx.pending.get(data[3:])
        if not job:
            await cq.answer("This request expired — send the file again.", show_alert=True)
            return
        if job.user_id != uid and not access.is_admin(uid):
            await cq.answer("This isn't your file.", show_alert=True)
            return
        ctx.pending.pop(job.id, None)
        if data.startswith("no:"):
            await cq.answer("Cancelled")
            try:
                await msg.delete()
            except Exception:
                pass
            return
        job.settings = (await db.get_settings(job.user_id)).copy()      # use the *latest* settings
        job.status_msg = msg
        ctx.pipeline.submit(job)
        await cq.answer("Queued ✅")
        await safe_edit(msg.edit_text, render_card(job, "⏳ <b>Queued…</b>"), parse_mode=H, reply_markup=markup([[("✖ Cancel", f"cx:{job.id}")]]))
        return
    if data.startswith("open:"):
        await cq.answer()
        which = data[5:]
        if which == "help":
            await msg.reply_text(HELP, parse_mode=H)
        else:
            await open_menu(msg, uid, which)
        return

    # ── settings menus (owner only) ──
    owner = ctx.menu_owner.get((msg.chat.id, msg.id))
    if owner and owner != uid:
        await cq.answer("This menu belongs to someone else — send /settings for your own.", show_alert=True)
        return
    ctx.menu_owner[(msg.chat.id, msg.id)] = uid

    s = await db.get_settings(uid)
    has_logo = db.logo_path(uid).exists()
    kind, _, rest = data.partition(":")

    if data == "x":
        await cq.answer()
        try:
            await msg.delete()
        except Exception:
            pass
        return
    if data == "pv":
        await cq.answer("Rendering preview…")
        await send_preview(msg, uid)
        return
    if data in ("m", "p", "lg"):
        await _render(cq, _screen_for(data, s, has_logo))
        await cq.answer()
        return
    if data == "rs":
        await _render(cq, ui.confirm_reset())
        await cq.answer()
        return
    if data == "rsy":
        s = WMSettings()
        await db.save_settings(uid, s)
        await _render(cq, ui.main_menu(s, has_logo))
        await cq.answer("Settings reset ♻️")
        return
    if data == "lgu":
        await cq.answer()
        await _begin_input(cq, "logo", "logo", "lg", "Send your logo as a <b>photo or file</b> (PNG with transparency is best).")
        return
    if data == "lgd":
        db.delete_logo(uid)
        s.set("logo_enabled", False)
        await db.save_settings(uid, s)
        await _render(cq, ui.logo_menu(s, False))
        await cq.answer("Logo removed")
        return

    if kind == "g":
        await _render(cq, ui.group_menu(s, rest))
        await cq.answer()
        return
    if kind == "ps":
        if apply_style_preset(s, rest):
            await db.save_settings(uid, s)
            await cq.answer("Style applied ✨")
            await _render(cq, ui.presets_menu())
            await send_preview(msg, uid)
        else:
            await cq.answer("Unknown style")
        return

    # per-field actions: b / n / ns / ni / c / cs / k / ks / ki / t
    parts = data.split(":")
    key = parts[1] if len(parts) > 1 else ""
    f = FIELD_MAP.get(key)
    if f is None:
        await cq.answer()
        return

    if kind == "b":                                           # toggle
        s.set(key, not getattr(s, key))
        await db.save_settings(uid, s)
        if key == "logo_enabled":
            await _render(cq, ui.logo_menu(s, has_logo))
        else:
            await _render(cq, ui.group_menu(s, f.group))
        await cq.answer("ON ✅" if getattr(s, key) else "OFF")
    elif kind == "n":
        await _render(cq, ui.num_menu(s, key))
        await cq.answer()
    elif kind == "ns":                                        # stepper
        delta = int(parts[2]) * f.step
        s.set(key, round(getattr(s, key) + delta, 4))
        await db.save_settings(uid, s)
        await _render(cq, ui.num_menu(s, key))
        await cq.answer(f"{getattr(s, key):g}{f.unit}")
    elif kind == "ni":
        await cq.answer()
        await _begin_input(cq, key, "num", f"n:{key}", f"Number between <b>{f.lo:g}</b> and <b>{f.hi:g}</b>{f.unit}.")
    elif kind == "c":
        await _render(cq, ui.choice_menu(s, key))
        await cq.answer()
    elif kind == "cs":
        idx = int(parts[2])
        value = POSITIONS[idx] if key == "position" else f.choices[idx][0]
        s.set(key, value)
        await db.save_settings(uid, s)
        await _render(cq, ui.choice_menu(s, key))
        await cq.answer("Saved ✅")
    elif kind == "k":
        await _render(cq, ui.color_menu(s, key))
        await cq.answer()
    elif kind == "ks":
        s.set(key, parts[2])
        await db.save_settings(uid, s)
        await _render(cq, ui.color_menu(s, key))
        await cq.answer("Saved ✅")
    elif kind == "ki":
        await cq.answer()
        await _begin_input(cq, key, "color", f"k:{key}", "A HEX colour like <code>#FF8800</code> (or a name: red, blue…).")
    elif kind == "t":
        await cq.answer()
        await _begin_input(cq, key, "text", f"g:{f.group}", (esc(f.help) + "\n" if f.help else "") + "Plain text; new lines allowed.")
    else:
        await cq.answer()


# ── typed replies ────────────────────────────────────────────────────────────────────────
async def on_text(client: Client, message: Message):
    """Free-text answers to a pending prompt (text / number / colour)."""
    if not message.from_user or message.text is None:
        return
    uid = message.from_user.id
    aw = ctx.awaiting.get(uid)
    if not aw or aw.kind == "logo" or aw.chat_id != message.chat.id:
        return
    s = await db.get_settings(uid)
    f = FIELD_MAP[aw.key]
    raw = message.text.strip()
    ok, shown = False, ""
    if aw.kind == "color":
        c = parse_color(raw)
        ok = c is not None
        if ok:
            s.set(aw.key, c)
    elif aw.kind == "num":
        try:
            float(raw.replace(",", ".").rstrip("%°s"))
            ok = s.set(aw.key, raw.replace(",", ".").rstrip("%°s"))
        except ValueError:
            ok = False
    else:
        if f.key == "pdf_pages":
            from ..pdfmark import PDFError, parse_pages
            try:
                parse_pages(raw, 9999)
            except PDFError as e:
                await message.reply_text(f"⚠️ {esc(str(e))}", parse_mode=H)
                return
        ok = s.set(aw.key, message.text.strip("\n"))
        if ok and aw.key == "text" and raw:
            s.set("text_enabled", True)
    if not ok:
        await message.reply_text("⚠️ That value isn't valid — try again, or /cancel.")
        return
    ctx.awaiting.pop(uid, None)
    await db.save_settings(uid, s)
    for mid in (aw.prompt_msg_id, message.id):
        try:
            await client.delete_messages(message.chat.id, mid)
        except Exception:
            pass
    text, rows = _screen_for(aw.back, s, db.logo_path(uid).exists())
    try:
        await client.edit_message_text(aw.chat_id, aw.menu_msg_id, text, parse_mode=H, reply_markup=markup(rows))
    except Exception:
        pass


async def handle_logo_upload(client: Client, message: Message) -> bool:
    """If the user is in 'upload logo' mode, consume this photo/document. Returns True when handled."""
    uid = message.from_user.id if message.from_user else 0
    aw = ctx.awaiting.get(uid)
    if not aw or aw.kind != "logo" or aw.chat_id != message.chat.id:
        return False
    doc = message.document
    if doc and not (doc.mime_type or "").startswith("image/"):
        await message.reply_text("⚠️ That isn't an image. Send a PNG/JPG logo, or /cancel.")
        return True
    if doc and doc.file_size and doc.file_size > 15 * 1024 * 1024:
        await message.reply_text("⚠️ Logo is too big (max 15 MB).")
        return True
    bio = await client.download_media(message, in_memory=True)
    try:
        im = ImageOps.exif_transpose(Image.open(bio)).convert("RGBA")
    except Exception:
        await message.reply_text("⚠️ I couldn't read that image. Try a PNG.")
        return True
    if max(im.size) > 1024:
        k = 1024 / max(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    db.logo_path(uid).parent.mkdir(parents=True, exist_ok=True)
    im.save(db.logo_path(uid), "PNG")
    db._logo_cache.pop(uid, None)
    s = await db.get_settings(uid)
    s.set("logo_enabled", True)
    await db.save_settings(uid, s)
    ctx.awaiting.pop(uid, None)
    for mid in (aw.prompt_msg_id,):
        try:
            await client.delete_messages(message.chat.id, mid)
        except Exception:
            pass
    text, rows = ui.logo_menu(s, True)
    try:
        await client.edit_message_text(aw.chat_id, aw.menu_msg_id, text, parse_mode=H, reply_markup=markup(rows))
    except Exception:
        pass
    await message.reply_text("✅ Logo saved and switched ON. Try /preview", quote=True)
    return True


def register(app: Client):
    app.add_handler(CallbackQueryHandler(on_callback), group=0)
    app.add_handler(MessageHandler(on_text, filters.text & ~filters.command(
        ["start", "help", "settings", "style", "logo", "reset", "setwm", "preview", "status", "queue", "cancel", "ddl", "id"])), group=1)
