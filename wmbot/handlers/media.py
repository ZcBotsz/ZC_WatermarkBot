"""Incoming videos/PDFs → queued text-watermark jobs."""
from __future__ import annotations

import asyncio
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from .. import config, tgio
from ..db import db
from ..jobs import Job, render_card
from ..plans import get_plan
from ..tgio import edit_text as tgedit_text, markup, reply_text as tgreply_text
from ..utils import esc, free_disk_gb, humanbytes
from ..video import VIDEO_EXTS
from . import access
from .ctx import ctx
from .user import gate

H = ParseMode.HTML
MAX_PENDING = 1000


def classify(name: str, mime: str) -> str | None:
    ext = Path(name or "").suffix.lower()
    mime = (mime or "").lower()
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in VIDEO_EXTS or mime.startswith("video/"):
        return "video"
    return None


def _pending_count(uid: int) -> int:
    return sum(1 for job in ctx.pending.values() if job.user_id == uid)


def _sender_name(user) -> str:
    return " ".join(part for part in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if part).strip()


async def _admit(message: Message, uid: int, size: int) -> bool:
    """Apply the global, per-plan queue, per-file and disk limits before accepting a job."""
    plan_key, _expiry = await db.plan_state(uid)
    plan = get_plan(plan_key)
    plan_bytes = plan.max_file_mb * 1024 * 1024
    if size and size > plan_bytes:
        await tgreply_text(message, 
            f"File too large ({humanbytes(size)}). Your {plan.name} limit is {plan.max_file_mb} MB per file. "
            "Use /plans to see upgrade options."
        )
        return False
    if size and size > config.MAX_FILE_MB * 1024 * 1024:
        await tgreply_text(message, f"The server limit is {config.MAX_FILE_MB} MB per file.")
        return False
    active = len(ctx.pipeline.user_jobs(uid)) + _pending_count(uid)
    batch_limit = min(plan.batch_limit, config.MAX_QUEUE_PER_USER)
    if active >= batch_limit:
        await tgreply_text(message, 
            f"Your queue is full ({active}/{batch_limit} files for the {plan.name} plan). "
            "Wait for a result or use /cancel."
        )
        return False
    if size and free_disk_gb(config.WORK_DIR) * 1024 ** 3 < size * 2.5:
        await tgreply_text(message, "The server is low on disk space right now. Please try again later.")
        return False
    return True


async def _reserve_daily_job(job: Job, message: Message | None = None) -> bool:
    plan_key, _expiry = await db.plan_state(job.user_id)
    plan = get_plan(plan_key)
    ok, day, used = await db.reserve_daily_job(job.user_id, plan.daily_jobs)
    if not ok:
        limit = "unlimited" if plan.daily_jobs is None else str(plan.daily_jobs)
        text = f"You have reached today’s {limit}-job limit on the {plan.name} plan. Use /plans to view upgrades."
        if message is not None:
            await tgreply_text(message, text)
        elif job.status_msg is not None:
            try:
                await tgedit_text(job.status_msg, text)
            except Exception:
                pass
        return False
    job.usage_day = day
    job.priority = plan.priority
    job.min_crf = plan.min_crf
    job.max_file_bytes = plan.max_file_mb * 1024 * 1024
    return True


async def _launch(message: Message, job: Job):
    """Start immediately or wait for confirmation; only submitted jobs consume daily quota."""
    if job.settings.auto_start:
        if not await _reserve_daily_job(job, message):
            return
        job.status_msg = await tgreply_text(message,
            render_card(job, "⏳ Queued"), parse_mode=H, quote=True,
            reply_markup=tgio.progress_markup(job),
        )
        ctx.pipeline.submit(job)
        return

    ctx.pending[job.id] = job
    if len(ctx.pending) > MAX_PENDING:
        ctx.pending.pop(next(iter(ctx.pending)))
    job.status_msg = await tgreply_text(message, 
        render_card(job, "Ready to watermark with your text settings."), parse_mode=H, quote=True,
        reply_markup=markup([
            [("🚀 Start", f"go:{job.id}", "primary"), ("❌ Cancel", f"no:{job.id}", "danger")],
            [("⚙️ Settings", "open:m", "primary"), ("👁️ Preview", "pv", "primary")],
        ]),
    )
    ctx.menu_owner[(job.status_msg.chat.id, job.status_msg.id)] = job.user_id


async def on_media(client: Client, message: Message):
    if not message.from_user:
        return
    private = message.chat.type == ChatType.PRIVATE
    if not access.allowed(message.from_user, message.chat):
        if private:
            await gate(message)
        return
    uid = message.from_user.id
    await db.touch_user(uid, message.from_user.first_name or "")

    if message.photo:
        await tgreply_text(message, "Photo watermarking is turned off. Send a video or PDF instead.")
        return

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
    elif message.document:
        d = message.document
        name, size, mime, as_doc = d.file_name or f"file_{message.id}", d.file_size or 0, d.mime_type, True
        kind = classify(name, mime)
        if kind is None:
            await tgreply_text(message, "I can process videos and PDFs only. Send one of those files to begin.")
            return
    if kind is None:
        return
    if not await _admit(message, uid, size):
        return

    plan_key, _expiry = await db.plan_state(uid)
    plan = get_plan(plan_key)
    s = (await db.get_settings(uid)).copy()
    if kind == "video" and s.crf < plan.min_crf:
        s.set("crf", plan.min_crf)
    has_text = s.has_pdf_visible_content if kind == "pdf" else s.has_visible_content
    if not has_text:
        target = "PDF watermark text" if kind == "pdf" else "text watermark"
        await tgreply_text(message, f"Your {target} is empty. Set one in /settings first.")
        return

    caption_html = message.caption.html if message.caption else ""
    task_number = len(ctx.pipeline.user_jobs(uid)) + _pending_count(uid) + 1
    task_limit = min(plan.batch_limit, config.MAX_QUEUE_PER_USER)
    job = Job(
        uid, message.chat.id, kind, name, size, s, message, as_document=as_doc,
        caption_html=caption_html, user_msg_id=message.id,
        deliver_to_dm=not private,
        sender_username=getattr(message.from_user, "username", None) or "",
        sender_name=_sender_name(message.from_user), task_number=task_number, task_limit=task_limit,
        max_file_bytes=plan.max_file_mb * 1024 * 1024,
        min_crf=plan.min_crf,
        priority=plan.priority,
    )
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
        await tgreply_text(message, 
            "Usage: <code>/ddl https://site.com/video.mp4</code>\nOptional filename: <code>/ddl URL | name.mp4</code>",
            parse_mode=H,
        )
        return
    ok, why = await asyncio.to_thread(tgio.is_safe_url, url)
    if not ok:
        await tgreply_text(message, f"Can't use that link: {esc(why)}.")
        return
    wait = await tgreply_text(message, "Checking the link…")
    try:
        name, size, ctype = await tgio.head_url(url)
    except Exception as e:
        await tgedit_text(wait, f"Couldn't reach the link: {esc(str(e)[:150])}", parse_mode=H)
        return
    if newname:
        from ..utils import safe_name
        name = safe_name(newname)
    kind = classify(name, ctype)
    if kind is None:
        await tgedit_text(wait, "That link isn't a video or PDF.")
        return
    if not await _admit(message, uid, size):
        await wait.delete()
        return
    plan_key, _expiry = await db.plan_state(uid)
    plan = get_plan(plan_key)
    s = (await db.get_settings(uid)).copy()
    if kind == "video" and s.crf < plan.min_crf:
        s.set("crf", plan.min_crf)
    has_text = s.has_pdf_visible_content if kind == "pdf" else s.has_visible_content
    if not has_text:
        target = "PDF watermark text" if kind == "pdf" else "text watermark"
        await tgedit_text(wait, f"Your {target} is empty. Set one in /settings first.")
        return
    await wait.delete()
    task_number = len(ctx.pipeline.user_jobs(uid)) + _pending_count(uid) + 1
    task_limit = min(plan.batch_limit, config.MAX_QUEUE_PER_USER)
    job = Job(
        uid, message.chat.id, kind, name, size, s, url, as_document=True,
        user_msg_id=message.id, deliver_to_dm=message.chat.type != ChatType.PRIVATE,
        sender_username=getattr(message.from_user, "username", None) or "",
        sender_name=_sender_name(message.from_user), task_number=task_number, task_limit=task_limit,
        max_file_bytes=plan.max_file_mb * 1024 * 1024,
        min_crf=plan.min_crf,
        priority=plan.priority,
    )
    await _launch(message, job)


def register(app: Client):
    media = filters.video | filters.document | filters.photo | filters.animation
    app.add_handler(MessageHandler(on_media, media & ~filters.channel), group=0)
    app.add_handler(MessageHandler(ddl_cmd, filters.command("ddl")), group=0)
