"""Inline-keyboard callbacks for settings, plans, job controls and text inputs."""
from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, ForceReply, Message

from .. import ui
from ..db import db
from ..jobs import render_card
from ..settings import FIELD_MAP, POSITIONS, WMSettings, parse_color
from ..tgio import answer_callback, markup, progress_markup, reply_text as tgreply_text, safe_edit
from ..utils import esc
from . import access
from .ctx import Awaiting, ctx
from .user import HELP, open_menu, restore_home, send_preview

log = logging.getLogger(__name__)
H = ParseMode.HTML


async def _edit_message_screen(message: Message, screen, uid: int | None = None):
    text, rows = screen
    editor = message.edit_caption if getattr(message, "photo", None) else message.edit_text
    await safe_edit(editor, text, parse_mode=H, reply_markup=markup(rows))
    if uid is not None:
        ctx.menu_owner[(message.chat.id, message.id)] = uid


async def _render(cq: CallbackQuery, screen):
    await _edit_message_screen(cq.message, screen)


def _screen_for(back: str, s: WMSettings):
    """Re-create a settings screen after accepting a typed value."""
    kind, _, arg = back.partition(":")
    screens = {
        "m": lambda: ui.main_menu(s),
        "g": lambda: ui.group_menu(s, arg),
        "n": lambda: ui.num_menu(s, arg),
        "c": lambda: ui.choice_menu(s, arg),
        "k": lambda: ui.color_menu(s, arg),
    }
    return screens.get(kind, lambda: ui.main_menu(s))()


async def _begin_input(cq: CallbackQuery, key: str, kind: str, back: str, hint: str):
    uid = cq.from_user.id
    old = ctx.awaiting.pop(uid, None)
    if old:
        try:
            await ctx.app.delete_messages(old.chat_id, old.prompt_msg_id)
        except Exception:
            pass
    name = FIELD_MAP[key].label if key in FIELD_MAP else "watermark text"
    mention = f'<a href="tg://user?id={uid}">{esc(cq.from_user.first_name or "you")}</a>'
    prompt = await tgreply_text(cq.message,
        f"{mention}, send the new value for <b>{esc(name)}</b>.\n{hint}\n<i>/cancel to abort</i>",
        parse_mode=H, reply_markup=ForceReply(selective=True, placeholder="type a value"),
    )
    ctx.awaiting[uid] = Awaiting(
        key, kind, cq.message.chat.id, cq.message.id, prompt.id, back,
        menu_is_photo=bool(getattr(cq.message, "photo", None)))


async def _send_open_page(client: Client, message: Message, uid: int, which: str):
    """Navigate button-driven pages in-place, keeping a pending job's Start card intact."""
    if which == "home":
        pending_card = any(
            job.user_id == uid and job.status_msg is not None
            and job.status_msg.chat.id == message.chat.id and job.status_msg.id == message.id
            for job in ctx.pending.values()
        )
        if pending_card:
            await open_menu(message, uid, "m")
            return
        await restore_home(message, uid)
        return
    if which in {"m", "settings"}:
        pending_card = any(
            job.user_id == uid and job.status_msg is not None
            and job.status_msg.chat.id == message.chat.id and job.status_msg.id == message.id
            for job in ctx.pending.values()
        )
        if pending_card:
            # Editing this confirmation would hide its Start/Cancel controls.
            await open_menu(message, uid, "m")
            return
        await _edit_message_screen(message, ui.main_menu(await db.get_settings(uid)), uid)
        return
    if which == "admin":
        from .admin_panel import _menu_rows, _menu_text
        if not access.is_admin(uid):
            return
        await _edit_message_screen(message, (_menu_text(), _menu_rows()), uid)
        return
    if which == "help":
        rows = [[("⚙️ Settings", "open:m", "primary"), ("💎 Plans", "open:plans", "primary")],
                [("💬 Contact Admin", "url:https://t.me/ZCYT_2026", "primary")],
                [("⬅️ Back to menu", "open:home", "primary")]]
        await _edit_message_screen(message, (HELP, rows), uid)
        return
    if which in {"plans", "myplan", "usage"}:
        from .plans import page_for
        await _edit_message_screen(message, await page_for(which, uid), uid)
        return
    await _edit_message_screen(message, ui.main_menu(await db.get_settings(uid)), uid)


async def on_callback(client: Client, cq: CallbackQuery):
    user, msg = cq.from_user, cq.message
    data = cq.data or ""
    if msg is None:
        await answer_callback(cq, )
        return
    if data.startswith("auto:"):
        from .automation import on_callback as on_automation_callback
        await on_automation_callback(client, cq)
        return
    if data.startswith("adm:"):
        from .admin_panel import on_admin_callback
        await on_admin_callback(client, cq)
        return
    if not access.allowed(user, msg.chat):
        await answer_callback(cq, "Not allowed", show_alert=True)
        return
    uid = user.id

    # ── job buttons ──
    if data.startswith("rf:"):
        job = ctx.pipeline.jobs.get(data[3:])
        if not job:
            await answer_callback(cq, "This job has already finished.")
        elif job.user_id != uid and not access.is_admin(uid):
            await answer_callback(cq, "This isn't your job.", show_alert=True)
        else:
            await answer_callback(cq, "Refreshing status…")
            await ctx.pipeline.refresh(job.id)
        return
    if data.startswith("cx:"):
        job = ctx.pipeline.jobs.get(data[3:])
        if not job:
            await answer_callback(cq, "Already finished.")
        elif job.user_id != uid and not access.is_admin(uid):
            await answer_callback(cq, "This isn't your job.", show_alert=True)
        else:
            ctx.pipeline.cancel(job.id)
            await answer_callback(cq, "Cancelling…")
        return
    if data.startswith(("go:", "no:")):
        job = ctx.pending.get(data[3:])
        if not job:
            await answer_callback(cq, "This request expired; send the file again.", show_alert=True)
            return
        if job.user_id != uid and not access.is_admin(uid):
            await answer_callback(cq, "This isn't your file.", show_alert=True)
            return
        ctx.pending.pop(job.id, None)
        if data.startswith("no:"):
            ctx.menu_owner.pop((msg.chat.id, msg.id), None)
            await answer_callback(cq, "Cancelled")
            try:
                await msg.delete()
            except Exception:
                pass
            return
        job.status_msg = msg
        from .media import _reserve_daily_job
        if not await _reserve_daily_job(job):
            ctx.menu_owner.pop((msg.chat.id, msg.id), None)
            await safe_edit(msg.edit_text, "Daily quota reached. Use /plans to review your options.",
                            reply_markup=None)
            await answer_callback(cq, "Daily quota reached", show_alert=True)
            return
        job.settings = (await db.get_settings(job.user_id)).copy()
        if job.kind == "video" and job.settings.crf < job.min_crf:
            job.settings.set("crf", job.min_crf)
        ctx.pipeline.submit(job)
        await answer_callback(cq, "Queued")
        await safe_edit(msg.edit_text, render_card(job, "⏳ Queued", 0.0), parse_mode=H,
                        reply_markup=progress_markup(job))
        return
    if data.startswith("open:"):
        owner = ctx.menu_owner.get((msg.chat.id, msg.id))
        if owner is not None and owner != uid:
            await answer_callback(cq, "This menu belongs to someone else; send /settings for your own.", show_alert=True)
            return
        await answer_callback(cq, )
        await _send_open_page(client, msg, uid, data[5:])
        return

    # ── settings menus ──
    owner = ctx.menu_owner.get((msg.chat.id, msg.id))
    if owner is not None and owner != uid:
        await answer_callback(cq, "This menu belongs to someone else; send /settings for your own.", show_alert=True)
        return
    ctx.menu_owner[(msg.chat.id, msg.id)] = uid

    s = await db.get_settings(uid)
    kind, _, rest = data.partition(":")

    if data == "pv":
        await answer_callback(cq, "Rendering preview…")
        await send_preview(msg, uid)
        return
    if data == "m":
        await _render(cq, ui.main_menu(s))
        await answer_callback(cq, )
        return
    if data == "rs":
        await _render(cq, ui.confirm_reset())
        await answer_callback(cq, )
        return
    if data == "rsy":
        s = WMSettings()
        await db.save_settings(uid, s)
        await _render(cq, ui.main_menu(s))
        await answer_callback(cq, "Settings reset")
        return
    if kind == "g":
        await _render(cq, ui.group_menu(s, rest))
        await answer_callback(cq, )
        return

    # per-field actions: b / n / ns / ni / c / cs / k / ks / ki / t
    parts = data.split(":")
    key = parts[1] if len(parts) > 1 else ""
    f = FIELD_MAP.get(key)
    if f is None:
        await answer_callback(cq, "This setting is not available.")
        return

    if kind == "b":
        s.set(key, not getattr(s, key))
        await db.save_settings(uid, s)
        await _render(cq, ui.group_menu(s, f.group))
        await answer_callback(cq, "ON" if getattr(s, key) else "OFF")
    elif kind == "bs":
        if key != "bg_enabled" or len(parts) < 3 or parts[2] not in {"0", "1"}:
            await answer_callback(cq, "This toggle is not available.", show_alert=True)
            return
        s.set(key, parts[2] == "1")
        await db.save_settings(uid, s)
        await _render(cq, ui.group_menu(s, f.group))
        await answer_callback(cq, "Background box ON" if s.bg_enabled else "Background box OFF")
    elif kind == "n":
        await _render(cq, ui.num_menu(s, key))
        await answer_callback(cq, )
    elif kind == "ns":
        delta = int(parts[2]) * f.step
        s.set(key, round(getattr(s, key) + delta, 4))
        await db.save_settings(uid, s)
        await _render(cq, ui.num_menu(s, key))
        await answer_callback(cq, f"{getattr(s, key):g}{f.unit}")
    elif kind == "ni":
        await answer_callback(cq, )
        await _begin_input(cq, key, "num", f"n:{key}", f"Number between <b>{f.lo:g}</b> and <b>{f.hi:g}</b>{esc(f.unit)}.")
    elif kind == "c":
        await _render(cq, ui.choice_menu(s, key))
        await answer_callback(cq, )
    elif kind == "cs":
        idx = int(parts[2])
        value = POSITIONS[idx] if key == "position" else f.choices[idx][0]
        s.set(key, value)
        await db.save_settings(uid, s)
        await _render(cq, ui.choice_menu(s, key))
        await answer_callback(cq, "Saved")
    elif kind == "k":
        await _render(cq, ui.color_menu(s, key))
        await answer_callback(cq, )
    elif kind == "ks":
        s.set(key, parts[2])
        await db.save_settings(uid, s)
        await _render(cq, ui.color_menu(s, key))
        await answer_callback(cq, "Saved")
    elif kind == "ki":
        await answer_callback(cq, )
        await _begin_input(cq, key, "color", f"k:{key}", "A HEX colour like <code>#FF8800</code> (or a name: red, blue…).")
    elif kind == "t":
        await answer_callback(cq, )
        await _begin_input(cq, key, "text", f"g:{f.group}", (esc(f.help) + "\n" if f.help else "") + "Plain text; new lines allowed.")
    else:
        await answer_callback(cq, )


# ── typed replies ────────────────────────────────────────────────────────────────────────
async def on_text(client: Client, message: Message):
    """Free-text answers to settings prompts or owner channel-automation setup."""
    if ctx.automation is not None and await ctx.automation.handle_private_message(message):
        return
    if not message.from_user or message.text is None:
        return
    uid = message.from_user.id
    aw = ctx.awaiting.get(uid)
    if not aw or aw.kind not in {"text", "num", "color"} or aw.chat_id != message.chat.id:
        return
    s = await db.get_settings(uid)
    f = FIELD_MAP.get(aw.key)
    if f is None:
        return
    raw = message.text.strip()
    ok = False
    if aw.kind == "color":
        color = parse_color(raw)
        ok = color is not None
        if ok:
            s.set(aw.key, color)
    elif aw.kind == "num":
        try:
            ok = s.set(aw.key, raw.replace(",", ".").rstrip("%°s"))
        except (ValueError, TypeError):
            ok = False
    else:
        if f.key == "pdf_text" and raw.casefold() in {"inherit", "same", "default"}:
            ok = s.set(aw.key, "")
        else:
            if f.key == "pdf_pages":
                from ..pdfmark import PDFError, parse_pages
                try:
                    parse_pages(raw, 9999)
                except PDFError as e:
                    await tgreply_text(message, f"{esc(str(e))}", parse_mode=H)
                    return
            ok = s.set(aw.key, message.text.strip("\n"))
            if ok and aw.key == "text" and raw:
                s.set("text_enabled", True)
    if not ok:
        await tgreply_text(message, "That value is not valid. Try again, or /cancel.")
        return
    ctx.awaiting.pop(uid, None)
    await db.save_settings(uid, s)
    for mid in (aw.prompt_msg_id, message.id):
        try:
            await client.delete_messages(message.chat.id, mid)
        except Exception:
            pass
    text, rows = _screen_for(aw.back, s)
    try:
        if aw.menu_is_photo:
            await safe_edit(client.edit_message_caption, aw.chat_id, aw.menu_msg_id, text, parse_mode=H, reply_markup=markup(rows))
        else:
            await safe_edit(client.edit_message_text, aw.chat_id, aw.menu_msg_id, text, parse_mode=H, reply_markup=markup(rows))
    except Exception:
        pass


def register(app: Client):
    app.add_handler(CallbackQueryHandler(on_callback), group=0)
    app.add_handler(MessageHandler(on_text, filters.text & ~filters.command(
        ["start", "help", "settings", "reset", "setwm", "preview", "status", "queue", "cancel", "ddl", "id",
         "plans", "myplan", "usage", "admin", "grant", "revoke", "ban", "unban"])), group=1)
