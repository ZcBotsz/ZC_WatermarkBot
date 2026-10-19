"""User commands, compact menu, previews and queue controls."""
from __future__ import annotations

import asyncio
import io
import platform
import random
import time

import psutil
from PIL import Image
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from .. import __version__, config, fonts, ui
from ..db import db
from ..render import apply_to_image, sample_canvas
from ..tgio import markup, reply_photo as tgreply_photo, reply_text as tgreply_text, send_photo as tgsend_photo
from ..utils import esc, free_disk_gb, humanbytes, humantime
from . import access
from .ctx import ctx

H = ParseMode.HTML

HELP = (
    "<b>Watermark help</b>\n\n"
    "Send a video or PDF, or use <code>/ddl URL</code> for a direct file link. "
    "The bot applies your text watermark and returns the processed file.\n\n"
    "<b>Commands</b>\n"
    "/settings — change text, font, position and output options\n"
    "/setwm text — set the watermark text\n"
    "/preview — preview the current design\n"
    "/plans, /myplan, /usage — plan and quota details\n"
    "/queue, /cancel — view or stop your jobs\n"
    "/status — server status\n"
    "/id — show your numeric Telegram ID\n\n"
    "In groups, jobs share one sequential processing queue. Processed group submissions are sent privately "
    "when possible; open the bot once with /start to enable private delivery."
)


def is_owner(uid: int) -> bool:
    return bool(config.OWNER_ID and uid == config.OWNER_ID)


def _main_rows(uid: int) -> list[list[tuple[str, str] | tuple[str, str, str]]]:
    rows = [
        [("⚙️ Settings", "open:m", "primary"), ("💎 Plans", "open:plans", "primary")],
        [("👤 My Plan", "open:myplan", "primary"), ("📊 Usage", "open:usage", "primary")],
        [("❔ Help", "open:help", "primary"), ("💬 Contact Admin", "url:https://t.me/ZCYT_2026", "primary")],
        [("📣 Channel", "url:https://t.me/ZCYT_20266", "primary")],
    ]
    if is_owner(uid):
        rows.append([("🛠️ Admin Panel", "open:admin", "primary")])
    return rows


async def _deny(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        await tgreply_text(message, access.deny_text(message.from_user.id if message.from_user else 0), parse_mode=H)


async def gate(message: Message) -> bool:
    if message.from_user:
        await db.touch_user(message.from_user.id, message.from_user.first_name or "")
    if not access.allowed(message.from_user, message.chat):
        await _deny(message)
        return False
    return True


async def open_menu(message: Message, uid: int, which: str = "m"):
    if which != "m":
        which = "m"
    s = await db.get_settings(uid)
    text, rows = ui.main_menu(s)
    sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = uid
    if len(ctx.menu_owner) > 2000:
        ctx.menu_owner.pop(next(iter(ctx.menu_owner)))


def _next_start_banner(uid: int):
    candidates = [path for path in config.START_BANNER_PATHS if path.is_file()]
    if not candidates:
        return None
    previous = ctx.last_start_banner.get(uid)
    choices = [path for path in candidates if str(path) != previous] or candidates
    banner = random.choice(choices)
    ctx.last_start_banner[uid] = str(banner)
    if len(ctx.last_start_banner) > 10_000:
        ctx.last_start_banner.pop(next(iter(ctx.last_start_banner)))
    return banner


async def start_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    uid = message.from_user.id
    name = esc(message.from_user.first_name or "there")
    text = (
        f"👋 <b>Hi {name}!</b>\n\n"
        "✨ I add text watermarks to videos and PDFs."
    )
    rows = _main_rows(uid)
    try:
        await client.send_chat_action(message.chat.id, "typing")
        await asyncio.sleep(0.9)  # keep Telegram's native typing dots visible before the photo arrives
    except Exception:
        pass
    banner = _next_start_banner(uid)
    if banner is not None and banner.is_file():
        try:
            sent = await tgsend_photo(client,
                message.chat.id, str(banner), caption=text, parse_mode=H,
                reply_markup=markup(rows), reply_to_message_id=message.id,
            )
        except Exception:
            sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    else:
        sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = uid


async def help_cmd(client: Client, message: Message):
    if await gate(message):
        rows = [[("⚙️ Settings", "open:m", "primary"), ("💎 Plans", "open:plans", "primary")],
                [("💬 Contact Admin", "url:https://t.me/ZCYT_2026", "primary")]]
        sent = await tgreply_text(message, HELP, parse_mode=H, reply_markup=markup(rows))
        ctx.menu_owner[(sent.chat.id, sent.id)] = message.from_user.id


async def id_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    await tgreply_text(message, f"Your Telegram ID: <code>{uid}</code>\nChat ID: <code>{message.chat.id}</code>", parse_mode=H)


async def settings_cmd(client: Client, message: Message):
    if await gate(message):
        await open_menu(message, message.from_user.id, "m")


async def reset_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    text, rows = ui.confirm_reset()
    sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = message.from_user.id


async def setwm_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await tgreply_text(message, "Usage: <code>/setwm @YourChannel</code>\nUse /settings for text appearance and placement.", parse_mode=H)
        return
    uid = message.from_user.id
    s = await db.get_settings(uid)
    s.set("text", parts[1].strip())
    s.set("text_enabled", True)
    await db.save_settings(uid, s)
    await tgreply_text(message, f"Watermark text set to <code>{esc(s.text)}</code>. Try /preview.", parse_mode=H)


# ── preview ──────────────────────────────────────────────────────────────────────────────
async def _target_frame(message: Message, uid: int) -> Image.Image:
    """A real Telegram thumbnail if available, otherwise a sample canvas."""
    m = message.reply_to_message
    w = h = 0
    file_id = None
    if m is not None:
        if m.video:
            w, h, th = m.video.width, m.video.height, m.video.thumbs
            file_id = th[-1].file_id if th else None
        elif m.animation:
            th = m.animation.thumbs
            file_id = th[-1].file_id if th else None
        elif m.document and m.document.thumbs:
            file_id = m.document.thumbs[-1].file_id
    if file_id:
        try:
            bio = await ctx.app.download_media(file_id, in_memory=True)
            img = Image.open(bio).convert("RGB")
            if w and h:
                k = min(1.0, 1920 / max(w, h))
                img = img.resize((int(w * k), int(h * k)), Image.Resampling.LANCZOS)
            return img
        except Exception:
            pass
    w, h = ctx.last_dims.get(uid, (1280, 720))
    k = min(1.0, 1280 / max(w, h))
    return sample_canvas(int(w * k), int(h * k))


async def send_preview(message: Message, uid: int, reply_to: Message | None = None):
    s = await db.get_settings(uid)
    if not s.has_visible_content:
        await tgreply_text(message, "Set a text watermark first with /setwm or /settings.")
        return
    base = await _target_frame(reply_to or message, uid)
    img = await asyncio.to_thread(apply_to_image, base, s)
    bio = io.BytesIO()
    img.convert("RGB").save(bio, "JPEG", quality=90)
    bio.name = "preview.jpg"
    bio.seek(0)
    note = "\nMotion modes move in the video; preview shows the starting position." if s.mode in ("scroll", "bounce", "hop") else ""
    await tgreply_photo(message, bio, caption=f"<b>Preview</b>{esc(note)}", parse_mode=H)


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
        ram_line = f"Container RAM: <b>{humanbytes(used)} / {humanbytes(cap)}</b> ({used / cap * 100:.0f}%)\n"
    else:
        vm = psutil.virtual_memory()
        ram_line = f"Host RAM: <b>{vm.percent:.0f}%</b> ({humanbytes(vm.available)} free)\n"
    up = time.time() - ctx.started
    await tgreply_text(message, 
        f"<b>Server status</b>\n\n"
        f"CPU: <b>{cpu:.0f}%</b> ({config.CPU_COUNT} cores available)\n"
        f"{ram_line}"
        f"Disk free: <b>{free_disk_gb(config.WORK_DIR):.1f} GB</b>\n"
        f"Uptime: {humantime(up)}\n\n"
        f"Encoders: {config.ENCODE_SLOTS} parallel · {config.FFMPEG_THREADS} threads/encode · GPU: {', '.join(hw) or 'none (CPU x264)'}\n"
        f"Active jobs: <b>{snap['active']}</b> {snap['by_status'] or ''}\n"
        f"Completed: {snap['completed']} · Failed: {snap['failed']}\n\n"
        f"<i>v{__version__} · Python {platform.python_version()}</i>", parse_mode=H)


async def queue_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    jobs = ctx.pipeline.user_jobs(message.from_user.id)
    pending = [j for j in ctx.pending.values() if j.user_id == message.from_user.id]
    jobs = pending + jobs
    if not jobs:
        await tgreply_text(message, "You have no queued or running jobs.")
        return
    lines = [f"<b>Your jobs</b> ({len(jobs)})"]
    rows = []
    for j in jobs:
        lines.append(f"• <code>{esc(j.file_name[:40])}</code> — {esc(j.status)}")
        rows.append([(f"❌ Cancel {j.file_name[:24]}", f"cx:{j.id}", "danger")])
    await tgreply_text(message, "\n".join(lines), parse_mode=H, reply_markup=markup(rows))


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
        await tgreply_text(message, "Input cancelled.")
        return
    n = ctx.pipeline.cancel_user(uid)
    pending_ids = [k for k, j in ctx.pending.items() if j.user_id == uid]
    for jid in pending_ids:
        job = ctx.pending.pop(jid, None)
        if job:
            ctx.menu_owner.pop((job.status_msg.chat.id, job.status_msg.id), None) if job.status_msg else None
    await tgreply_text(message, f"Cancelling {n} job(s)…" if n else "Nothing to cancel.")


def register(app: Client):
    cmds = {"start": start_cmd, "help": help_cmd, "id": id_cmd, "settings": settings_cmd,
            "reset": reset_cmd, "setwm": setwm_cmd, "preview": preview_cmd, "status": status_cmd,
            "queue": queue_cmd, "cancel": cancel_cmd}
    for name, fn in cmds.items():
        app.add_handler(MessageHandler(fn, filters.command(name)), group=0)
