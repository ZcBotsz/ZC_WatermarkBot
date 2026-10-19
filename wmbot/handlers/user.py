"""Everyday commands: /start /help /settings /style /logo /reset /setwm /preview /status /queue /cancel /id"""
from __future__ import annotations

import asyncio
import io
import platform
import time

import psutil
from PIL import Image
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import ForceReply, Message

from .. import __version__, config, fonts, ui
from ..db import db
from ..render import apply_to_image, sample_canvas
from ..tgio import markup
from ..utils import esc, free_disk_gb, humanbytes, humantime
from . import access
from .ctx import ctx

H = ParseMode.HTML

START = (
    "👋 <b>Hi {name}! I add watermarks — fast.</b>\n\n"
    "🎬 <b>Videos</b> · 📄 <b>PDFs</b> · 🖼 <b>Photos</b>\n"
    "Just send me a file and I'll send it back watermarked.\n\n"
    "<b>You control everything:</b> text, font, size, colour, opacity, background box, border, outline, shadow, "
    "logo, position, scrolling/bouncing/tiled modes, video quality (CRF), speed preset and resolution.\n\n"
    "👉 Tap <b>⚙️ Settings</b> to design your watermark, or send /help."
)

HELP = (
    "📖 <b>How to use</b>\n\n"
    "1️⃣ /settings — design your watermark (text, colours, box, border, logo, position…)\n"
    "2️⃣ /preview — see it on a sample picture (reply to a video/photo to preview on it)\n"
    "3️⃣ Send a <b>video, PDF or photo</b> (or several!) — they're processed one after another\n\n"
    "<b>Commands</b>\n"
    "/settings — full settings menu\n"
    "/style — one-tap looks (corner, label, tiles, ticker…)\n"
    "/setwm <i>text</i> — quickly change the watermark text\n"
    "/logo — upload / manage your logo\n"
    "/preview — preview your watermark\n"
    "/ddl <i>url</i> — watermark a file from a direct link\n"
    "/queue — your running jobs · /cancel — stop them\n"
    "/status — server load & speed · /reset — default settings\n\n"
    "<b>Speed tips</b> ⚡\n"
    "• Preset <code>ultrafast</code>/<code>superfast</code> = fastest (bigger file)\n"
    "• Lower resolution (720p) = much faster\n"
    "• Raise CRF (26–28) for smaller files\n"
    "• Static/moving/tiled modes all cost the same speed"
)


async def _deny(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await message.reply_text(access.deny_text(message.from_user.id if message.from_user else 0), parse_mode=H)


async def gate(message: Message) -> bool:
    if message.from_user:
        await db.touch_user(message.from_user.id, message.from_user.first_name or "")
    if not access.allowed(message.from_user, message.chat):
        await _deny(message)
        return False
    return True


async def start_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    name = esc(message.from_user.first_name or "there")
    await message.reply_text(START.format(name=name), parse_mode=H,
                             reply_markup=markup([[("⚙️ Settings", "open:m"), ("🎨 Quick styles", "open:p")], [("📖 Help", "open:help")]]))


async def help_cmd(client: Client, message: Message):
    if await gate(message):
        await message.reply_text(HELP, parse_mode=H)


async def id_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    await message.reply_text(f"👤 Your ID: <code>{uid}</code>\n💬 Chat ID: <code>{message.chat.id}</code>", parse_mode=H)


async def open_menu(message: Message, uid: int, which: str = "m"):
    s = await db.get_settings(uid)
    has_logo = db.logo_path(uid).exists()
    text, rows = {"m": lambda: ui.main_menu(s, has_logo), "p": ui.presets_menu,
                  "lg": lambda: ui.logo_menu(s, has_logo)}[which]()
    sent = await message.reply_text(text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = uid
    if len(ctx.menu_owner) > 2000:
        ctx.menu_owner.pop(next(iter(ctx.menu_owner)))


async def settings_cmd(client: Client, message: Message):
    if await gate(message):
        await open_menu(message, message.from_user.id, "m")


async def style_cmd(client: Client, message: Message):
    if await gate(message):
        await open_menu(message, message.from_user.id, "p")


async def logo_cmd(client: Client, message: Message):
    if await gate(message):
        await open_menu(message, message.from_user.id, "lg")


async def reset_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    text, rows = ui.confirm_reset()
    sent = await message.reply_text(text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = message.from_user.id


async def setwm_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text("Usage: <code>/setwm @YourChannel</code>\n(Use /settings for fonts, colours, box…)", parse_mode=H)
        return
    uid = message.from_user.id
    s = await db.get_settings(uid)
    s.set("text", parts[1].strip())
    s.set("text_enabled", True)
    await db.save_settings(uid, s)
    await message.reply_text(f"✅ Watermark text set to <code>{esc(s.text)}</code>\nTry /preview", parse_mode=H)


# ── preview ──────────────────────────────────────────────────────────────────────────────
async def _target_frame(message: Message, uid: int) -> Image.Image:
    """A real frame if the user replied to media (Telegram's own thumbnail), else a sample picture."""
    m = message.reply_to_message
    w = h = 0
    file_id = None
    if m is not None:
        if m.video:
            w, h, th = m.video.width, m.video.height, m.video.thumbs
            file_id = th[-1].file_id if th else None
        elif m.photo:
            file_id = m.photo.file_id
        elif m.document and m.document.thumbs:
            file_id = m.document.thumbs[-1].file_id
    if file_id:
        try:
            bio = await ctx.app.download_media(file_id, in_memory=True)
            img = Image.open(bio).convert("RGB")
            if w and h:
                k = min(1.0, 1920 / max(w, h))
                img = img.resize((int(w * k), int(h * k)), Image.LANCZOS)
            return img
        except Exception:
            pass
    w, h = ctx.last_dims.get(uid, (1280, 720))
    k = min(1.0, 1280 / max(w, h))
    return sample_canvas(int(w * k), int(h * k))


async def send_preview(message: Message, uid: int, reply_to: Message | None = None):
    s = await db.get_settings(uid)
    if not s.has_visible_content:
        await message.reply_text("Nothing to draw yet — enable text or a logo in /settings.")
        return
    base = await _target_frame(reply_to or message, uid)
    logo = db.get_logo(uid) if s.logo_enabled else None
    img = await asyncio.to_thread(apply_to_image, base, s, logo)
    bio = io.BytesIO()
    img.convert("RGB").save(bio, "JPEG", quality=90)
    bio.name = "preview.jpg"
    bio.seek(0)
    note = ""
    if s.mode in ("scroll", "bounce", "hop"):
        note = "\n<i>Motion modes move in the video — preview shows the start position.</i>"
    await message.reply_photo(bio, caption=f"👁 <b>Preview</b>{note}", parse_mode=H)


async def preview_cmd(client: Client, message: Message):
    if await gate(message):
        await send_preview(message, message.from_user.id)


# ── status / queue / cancel ──────────────────────────────────────────────────────────────
async def status_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    from .. import video
    snap = ctx.pipeline.snapshot()
    hw = []
    for c in ("h264", "h265"):
        e = await video.hw_available(c)
        if e:
            hw.append(e)

    def cpu_percent():
        start_usage = config.cpu_usage_seconds()
        started = time.monotonic()
        time.sleep(0.5)
        end_usage = config.cpu_usage_seconds()
        elapsed = max(0.01, time.monotonic() - started)
        if start_usage is not None and end_usage is not None and end_usage >= start_usage:
            return min(100.0, (end_usage - start_usage) / elapsed / max(1, config.CPU_COUNT) * 100)
        return min(100.0, psutil.Process().cpu_percent(interval=0.01) / max(1, config.CPU_COUNT))

    cpu = await asyncio.to_thread(cpu_percent)
    used, cap = config.memory_current_bytes(), config.MEMORY_LIMIT_BYTES
    if used is not None and cap:
        ram_line = f"🧠 Container RAM: <b>{humanbytes(used)} / {humanbytes(cap)}</b> ({used / cap * 100:.0f}%)\n"
    else:
        vm = psutil.virtual_memory()
        ram_line = f"🧠 Host RAM: <b>{vm.percent:.0f}%</b> ({humanbytes(vm.available)} free)\n"
    up = time.time() - ctx.started
    await message.reply_text(
        f"📊 <b>Server status</b>\n\n"
        f"🖥 CPU: <b>{cpu:.0f}%</b> ({config.CPU_COUNT} cores available)\n"
        f"{ram_line}"
        f"💾 Disk free: <b>{free_disk_gb(config.WORK_DIR):.1f} GB</b>\n"
        f"⏱ Uptime: {humantime(up)}\n\n"
        f"⚙️ Encoders: {config.ENCODE_SLOTS} parallel · {config.FFMPEG_THREADS} threads/encode · GPU: {', '.join(hw) or 'none (CPU x264)'}\n"
        f"📦 Active jobs: <b>{snap['active']}</b> {snap['by_status'] or ''}\n"
        f"✅ Completed: {snap['completed']} · ❌ Failed: {snap['failed']}\n\n"
        f"<i>v{__version__} · Python {platform.python_version()}</i>", parse_mode=H)


async def queue_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    jobs = ctx.pipeline.user_jobs(message.from_user.id)
    if not jobs:
        await message.reply_text("📭 You have no running jobs.")
        return
    lines = [f"📦 <b>Your jobs</b> ({len(jobs)})"]
    rows = []
    for j in jobs:
        lines.append(f"• <code>{esc(j.file_name[:40])}</code> — {j.status}")
        rows.append([(f"✖ Cancel {j.file_name[:24]}", f"cx:{j.id}")])
    await message.reply_text("\n".join(lines), parse_mode=H, reply_markup=markup(rows))


async def cancel_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    uid = message.from_user.id
    aw = ctx.awaiting.pop(uid, None)
    if aw:
        try:
            await client.delete_messages(aw.chat_id, aw.prompt_msg_id)
        except Exception:
            pass
        await message.reply_text("👌 Input cancelled.")
        return
    n = ctx.pipeline.cancel_user(uid)
    for jid in [k for k, j in ctx.pending.items() if j.user_id == uid]:
        ctx.pending.pop(jid, None)
    await message.reply_text(f"🛑 Cancelling {n} job(s)…" if n else "Nothing to cancel.")


def register(app: Client):
    cmds = {"start": start_cmd, "help": help_cmd, "id": id_cmd, "settings": settings_cmd, "style": style_cmd,
            "logo": logo_cmd, "reset": reset_cmd, "setwm": setwm_cmd, "preview": preview_cmd, "status": status_cmd,
            "queue": queue_cmd, "cancel": cancel_cmd}
    for name, fn in cmds.items():
        app.add_handler(MessageHandler(fn, filters.command(name)), group=0)
