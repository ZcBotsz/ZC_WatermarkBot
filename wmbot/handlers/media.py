"""Incoming files → jobs.  Handles Telegram media, documents, photos and /ddl direct links."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from .. import config, tgio
from ..db import db
from ..imgmark import IMAGE_EXTS
from ..jobs import Job, render_card
from ..tgio import markup
from ..utils import esc, humanbytes
from ..video import VIDEO_EXTS
from . import access
from .ctx import ctx
from .settings_cb import handle_logo_upload
from .user import gate

log = logging.getLogger(__name__)
H = ParseMode.HTML
MAX_PENDING = 500


def classify(name: str, mime: str) -> str | None:
    ext = Path(name or "").suffix.lower()
    mime = (mime or "").lower()
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in VIDEO_EXTS or mime.startswith("video/"):
        return "video"
    if ext in IMAGE_EXTS or mime.startswith("image/"):
        return "image"
    return None


async def _admit(message: Message, uid: int, size: int) -> bool:
    """Common limits. Replies with the reason when refusing."""
    if size and size > config.MAX_FILE_MB * 1024 * 1024:
        await message.reply_text(f"⚠️ File too large ({humanbytes(size)}). Limit is {config.MAX_FILE_MB} MB.")
        return False
    if not access.is_admin(uid) and len(ctx.pipeline.user_jobs(uid)) >= config.MAX_QUEUE_PER_USER:
        await message.reply_text(f"⏳ You already have {config.MAX_QUEUE_PER_USER} files in the queue. Wait for some to finish (or /cancel).")
        return False
    from ..utils import free_disk_gb
    if size and free_disk_gb(config.WORK_DIR) * 1024 ** 3 < size * 2.5:
        await message.reply_text("💾 The server is low on disk space right now — please try again in a few minutes.")
        return False
    return True


async def _launch(message: Message, job: Job):
    """Either start right away (auto-start) or show the confirm card."""
    s = job.settings
    if s.auto_start:
        job.status_msg = await message.reply_text(render_card(job, "⏳ <b>Queued…</b>"), parse_mode=H, quote=True,
                                                  reply_markup=markup([[("✖ Cancel", f"cx:{job.id}")]]))
        pos = ctx.pipeline.submit(job)
        if pos > 1:
            await ctx.transport.edit_status(job, render_card(job, f"⏳ <b>Queued</b> — {pos - 1} job(s) ahead"))
        return
    ctx.pending[job.id] = job
    if len(ctx.pending) > MAX_PENDING:
        ctx.pending.pop(next(iter(ctx.pending)))
    job.status_msg = await message.reply_text(
        render_card(job, "Ready to watermark with your current settings."), parse_mode=H, quote=True,
        reply_markup=markup([[("▶️ Start", f"go:{job.id}"), ("✖ Cancel", f"no:{job.id}")],
                             [("⚙️ Settings", "open:m"), ("👁 Preview", "pv")]]))
    ctx.menu_owner[(job.status_msg.chat.id, job.status_msg.id)] = job.user_id


async def on_media(client: Client, message: Message):
    if not message.from_user:
        return
    if await handle_logo_upload(client, message):
        return
    private = message.chat.type == ChatType.PRIVATE
    if not access.allowed(message.from_user, message.chat):
        if private:
            await gate(message)
        return
    uid = message.from_user.id
    await db.touch_user(uid, message.from_user.first_name or "")

    kind = name = mime = None
    size = 0
    as_doc = False
    if message.video:
        v = message.video
        kind, name, size, mime = "video", v.file_name or f"video_{message.id}.mp4", v.file_size or 0, v.mime_type
        ctx.last_dims[uid] = (v.width or 1280, v.height or 720)
    elif message.animation:
        a = message.animation
        kind, name, size, mime = "video", a.file_name or f"animation_{message.id}.mp4", a.file_size or 0, a.mime_type
    elif message.photo:
        kind, name, size = "image", f"photo_{message.id}.jpg", message.photo.file_size or 0
        ctx.last_dims[uid] = (message.photo.width or 1280, message.photo.height or 720)
    elif message.document:
        d = message.document
        name, size, mime, as_doc = d.file_name or f"file_{message.id}", d.file_size or 0, d.mime_type, True
        kind = classify(name, mime)
        if kind is None:
            if private:
                await message.reply_text("🤷 I can watermark <b>videos, PDFs and photos</b> — that file type isn't supported.", parse_mode=H)
            return
    if kind is None:
        return
    if not await _admit(message, uid, size):
        return

    s = (await db.get_settings(uid)).copy()
    if s.logo_enabled and not db.logo_path(uid).exists():
        s.set("logo_enabled", False)
    if not s.has_visible_content:
        await message.reply_text("⚠️ Your watermark is empty — turn on text or a logo in /settings first.")
        return
    caption_html = message.caption.html if message.caption else ""
    job = Job(uid, message.chat.id, kind, name, size, s, message, as_document=as_doc,
              caption_html=caption_html, user_msg_id=message.id)
    await _launch(message, job)


async def ddl_cmd(client: Client, message: Message):
    """/ddl <url> [| new file name]"""
    if not await gate(message):
        return
    uid = message.from_user.id
    text = message.text or ""
    arg = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    if not arg and message.reply_to_message and message.reply_to_message.text:
        arg = message.reply_to_message.text
    url, _, newname = arg.partition("|")
    url, newname = url.strip().split()[0] if url.strip() else "", newname.strip()
    if not url:
        await message.reply_text("Usage: <code>/ddl https://site.com/video.mp4</code>\nOptional new name: <code>/ddl URL | my name.mp4</code>", parse_mode=H)
        return
    ok, why = await asyncio.to_thread(tgio.is_safe_url, url)
    if not ok:
        await message.reply_text(f"⚠️ Can't use that link: {esc(why)}.")
        return
    wait = await message.reply_text("🔎 Checking link…")
    try:
        name, size, ctype = await tgio.head_url(url)
    except Exception as e:
        await wait.edit_text(f"⚠️ Couldn't reach the link: {esc(str(e)[:150])}", parse_mode=H)
        return
    if newname:
        from ..utils import safe_name
        name = safe_name(newname)
    kind = classify(name, ctype)
    if kind is None:
        await wait.edit_text("🤷 That link isn't a video, PDF or image.")
        return
    if not await _admit(message, uid, size):
        await wait.delete()
        return
    s = (await db.get_settings(uid)).copy()
    if not s.has_visible_content:
        await wait.edit_text("⚠️ Your watermark is empty — enable text or a logo in /settings.")
        return
    await wait.delete()
    job = Job(uid, message.chat.id, kind, name, size, s, url, as_document=True, user_msg_id=message.id)
    await _launch(message, job)


def register(app: Client):
    media = filters.video | filters.document | filters.photo | filters.animation
    app.add_handler(MessageHandler(on_media, media & ~filters.channel), group=0)
    app.add_handler(MessageHandler(ddl_cmd, filters.command("ddl")), group=0)
