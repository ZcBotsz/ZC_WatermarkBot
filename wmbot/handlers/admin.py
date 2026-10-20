"""Owner/admin commands."""
from __future__ import annotations

import asyncio

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from ..db import db
from ..tgio import edit_text as tgedit_text, reply_text as tgreply_text
from ..utils import esc, humanbytes
from . import access
from .ctx import ctx

H = ParseMode.HTML


def _admin_only(fn):
    async def wrapper(client: Client, message: Message):
        if message.from_user and access.is_admin(message.from_user.id):
            await fn(client, message)
    return wrapper


def _target_id(message: Message) -> int | None:
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].lstrip("-").isdigit():
        return int(parts[1])
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    return None


async def _flag(message: Message, flag: str, value: bool, verb: str):
    uid = _target_id(message)
    if uid is None:
        await tgreply_text(message, f"Usage: <code>/{flag_cmd[(flag, value)]} user_id</code> (or reply to their message)", parse_mode=H)
        return
    await db.set_flag(uid, flag, value)
    await tgreply_text(message, f"✅ User <code>{uid}</code> {verb}.", parse_mode=H)


flag_cmd = {("banned", True): "ban", ("banned", False): "unban", ("allowed", True): "allow", ("allowed", False): "disallow"}


@_admin_only
async def ban(c, m): await _flag(m, "banned", True, "banned")
@_admin_only
async def unban(c, m): await _flag(m, "banned", False, "unbanned")
@_admin_only
async def allow(c, m): await _flag(m, "allowed", True, "allowed")
@_admin_only
async def disallow(c, m): await _flag(m, "allowed", False, "removed from the allow-list")


@_admin_only
async def authchat(c, m: Message):
    if m.chat.type == ChatType.PRIVATE:
        await tgreply_text(m, "Run this inside the group you want to authorise.")
        return
    await db.set_chat(m.chat.id, m.chat.title or "", True)
    await tgreply_text(m, "✅ This group is authorised — everyone here can use the bot.")


@_admin_only
async def unauthchat(c, m: Message):
    await db.set_chat(m.chat.id, "", False)
    await tgreply_text(m, "🚫 This group is no longer authorised.")


@_admin_only
async def stats(c, m: Message):
    st = await db.stats()
    lines = [f"📈 <b>Bot statistics</b>\n\n👥 Users: <b>{st['users']}</b>"]
    for k in st["kinds"]:
        lines.append(f"• {k['kind']}: <b>{k['c']}</b> jobs ({k['ok'] or 0} ok) · in {humanbytes(k['sin'] or 0)} → out "
                     f"{humanbytes(k['sout'] or 0)} · avg {k['avg'] or 0:.0f}s")
    await tgreply_text(m, "\n".join(lines), parse_mode=H)


@_admin_only
async def broadcast(c: Client, m: Message):
    src = m.reply_to_message
    if not src:
        await tgreply_text(m, "Reply to the message you want to broadcast with /broadcast")
        return
    ids = await db.all_user_ids()
    status = await tgreply_text(m, f"📣 Sending to {len(ids)} users…")
    ok = fail = 0
    for uid in ids:
        try:
            await src.copy(uid)
            ok += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            fail += 1
        await asyncio.sleep(0.06)           # stay well below Telegram's ~30 msg/s limit
    await tgedit_text(status, f"📣 Broadcast finished: ✅ {ok} · ❌ {fail}")


def register(app: Client):
    for name, fn in {"allow": allow, "disallow": disallow, "authchat": authchat,
                     "unauthchat": unauthchat, "stats": stats, "broadcast": broadcast}.items():
        app.add_handler(MessageHandler(fn, filters.command(name)), group=0)
