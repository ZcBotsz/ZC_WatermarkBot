"""Owner-only administration: metrics, recent users, manual plan grants and access control."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, ForceReply, Message

from .. import config
from ..db import db
from ..plans import PLANS
from ..tgio import answer_callback, markup, reply_text as tgreply_text, safe_edit
from ..utils import esc, humanbytes, humantime
from . import access
from .ctx import Awaiting, ctx

H = ParseMode.HTML
IST = ZoneInfo("Asia/Kolkata")


def _is_owner(uid: int) -> bool:
    return bool(config.OWNER_ID and uid == config.OWNER_ID)


def _menu_rows():
    return [
        [("📈 Overview", "adm:stats", "primary"), ("👥 Recent Users", "adm:users", "primary")],
        [("🎟️ Grant / Renew", "adm:grant", "primary"), ("🚫 Revoke Plan", "adm:revoke", "danger")],
        [("⛔ Ban User", "adm:ban", "danger"), ("✅ Unban User", "adm:unban", "primary")],
        [("📖 Admin Guide", "adm:guide", "primary"), ("✖️ Close", "adm:close", "danger")],
    ]


def _menu_text() -> str:
    return (
        "🛠️ <b>Admin panel</b>\n\n"
        "Manage plan renewals, user access and bot usage. Plan changes are manual; the bot does not accept payments.\n\n"
        "For commands, use /grant, /revoke, /ban and /unban."
    )


async def _edit_admin_screen(message: Message, text: str, rows):
    editor = message.edit_caption if getattr(message, "photo", None) else message.edit_text
    await safe_edit(editor, text, parse_mode=H, reply_markup=markup(rows))


async def show_admin(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if not _is_owner(uid):
        return
    sent = await tgreply_text(message, _menu_text(), parse_mode=H, reply_markup=markup(_menu_rows()))
    ctx.menu_owner[(sent.chat.id, sent.id)] = uid


async def _stats_text() -> str:
    s = await db.stats()
    plans = s.get("plans", {})
    paid = sum(plans.values())
    kinds = s.get("kinds", [])
    kind_line = "\n".join(f"{esc(k['kind'])}: {k['c']} jobs · {k['ok'] or 0} completed" for k in kinds) or "No completed jobs yet."
    return (
        "<b>Bot overview</b>\n\n"
        f"Users: <b>{s.get('users', 0)}</b> · active in 24h: <b>{s.get('active_24h', 0)}</b>\n"
        f"Active paid plans: <b>{paid}</b> (Plus {plans.get('plus', 0)} · Pro {plans.get('pro', 0)} · Infinity {plans.get('infinity', 0)})\n"
        f"Jobs reserved today: <b>{s.get('jobs_reserved_today', 0)}</b>\n"
        f"Lifetime jobs: <b>{s.get('jobs_total', 0)}</b> · successful: <b>{s.get('jobs_success', 0)}</b>\n\n"
        f"{kind_line}"
    )


async def _users_text() -> str:
    users = await db.recent_users(15)
    if not users:
        return "<b>Recent users</b>\n\nNo users recorded yet."
    lines = ["<b>Recent users</b>"]
    for user in users:
        name = esc(user.get("name") or "Unknown")[:32]
        last_seen = datetime.fromtimestamp(user["last_seen"] or time.time(), IST).strftime("%d %b %H:%M")
        lines.append(f"<code>{user['user_id']}</code> · {name} · {esc(user.get('plan') or 'free')} · {last_seen}")
    return "\n".join(lines)


async def _ask(cq: CallbackQuery, key: str, hint: str):
    uid = cq.from_user.id
    old = ctx.awaiting.pop(uid, None)
    if old:
        try:
            await ctx.app.delete_messages(old.chat_id, old.prompt_msg_id)
        except Exception:
            pass
    prompt = await tgreply_text(cq.message, 
        f"<b>Admin input</b>\n{hint}\n\nUse /cancel to abort.", parse_mode=H,
        reply_markup=ForceReply(selective=True, placeholder="enter values"),
    )
    ctx.awaiting[uid] = Awaiting(key, "admin", cq.message.chat.id, cq.message.id, prompt.id, "admin")


async def on_admin_callback(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    if not _is_owner(uid):
        await answer_callback(cq, "Owner only.", show_alert=True)
        return
    data = cq.data or ""
    if data == "adm:close":
        await answer_callback(cq, )
        try:
            await cq.message.delete()
        except Exception:
            pass
        return
    if data == "adm:grant":
        await answer_callback(cq, )
        await _ask(cq, "admin_grant", "Enter: <code>USER_ID PLAN [DAYS]</code>\nExample: <code>123456789 plus 30</code>. Use <code>permanent</code> for no expiry.")
        return
    if data == "adm:revoke":
        await answer_callback(cq, )
        await _ask(cq, "admin_revoke", "Enter the numeric <code>USER_ID</code> whose paid plan should be changed to Free.")
        return
    if data in {"adm:ban", "adm:unban"}:
        await answer_callback(cq, )
        action = "ban" if data.endswith("ban") and data != "adm:unban" else "unban"
        await _ask(cq, f"admin_{action}", f"Enter the numeric <code>USER_ID</code> to {action}.")
        return
    if data == "adm:guide":
        text = (
            "<b>Admin commands</b>\n\n"
            "<code>/grant USER_ID PLAN [DAYS]</code> — assign or renew a plan\n"
            "<code>/revoke USER_ID</code> — move user to Free\n"
            "<code>/ban USER_ID</code> / <code>/unban USER_ID</code> — control access\n"
            "Plans: <code>free</code>, <code>plus</code>, <code>pro</code>, <code>infinity</code>.\n"
            "Paid plans default to 30 days; specify <code>permanent</code> to omit expiry. "
            "Renewing the same active plan adds days to its current expiry."
        )
        await _edit_admin_screen(cq.message, text, [[('⬅️ Back', 'adm:home', 'primary')]])
        await answer_callback(cq, )
        return
    if data == "adm:home":
        await _edit_admin_screen(cq.message, _menu_text(), _menu_rows())
        await answer_callback(cq, )
        return
    if data == "adm:stats":
        text = await _stats_text()
    elif data == "adm:users":
        text = await _users_text()
    else:
        await answer_callback(cq, )
        return
    await _edit_admin_screen(cq.message, text,
                             [[('⬅️ Back', 'adm:home', 'primary'), ('🔄 Refresh', data, 'primary')]])
    await answer_callback(cq, )


async def _handle_admin_input(message: Message, aw: Awaiting, raw: str):
    key = aw.key
    parts = raw.split()
    if key == "admin_grant":
        if len(parts) < 2 or len(parts) > 3:
            raise ValueError("Use: USER_ID PLAN [DAYS].")
        target = int(parts[0])
        if target <= 0:
            raise ValueError("USER_ID must be a positive numeric Telegram user ID.")
        plan = parts[1].strip().lower()
        if plan not in PLANS:
            raise ValueError("Plan must be free, plus, pro, or infinity.")
        days: int | None
        if plan == "free":
            days = None
        elif len(parts) == 3 and parts[2].lower() in {"permanent", "forever"}:
            days = None
        else:
            days = int(parts[2]) if len(parts) == 3 else 30
            if not 1 <= days <= 3650:
                raise ValueError("Days must be from 1 to 3650, or use 'permanent'.")
        await db.touch_user(target, "")
        expires = await db.set_plan(target, plan, days)
        if expires:
            expiry = datetime.fromtimestamp(expires, IST).strftime("%d %b %Y, %I:%M %p IST")
            detail = f"Expires: {expiry}"
        else:
            detail = "No expiry."
        return f"Assigned <b>{esc(PLANS[plan].name)}</b> to <code>{target}</code>. {detail}"
    if key == "admin_revoke":
        if len(parts) != 1:
            raise ValueError("Enter one numeric USER_ID.")
        target = int(parts[0])
        if target <= 0:
            raise ValueError("USER_ID must be a positive numeric Telegram user ID.")
        await db.touch_user(target, "")
        await db.set_plan(target, "free", None)
        return f"Changed <code>{target}</code> to the Free plan."
    if key in {"admin_ban", "admin_unban"}:
        if len(parts) != 1:
            raise ValueError("Enter one numeric USER_ID.")
        target = int(parts[0])
        if target <= 0:
            raise ValueError("USER_ID must be a positive numeric Telegram user ID.")
        await db.touch_user(target, "")
        banned = key == "admin_ban"
        await db.set_flag(target, "banned", banned)
        return f"User <code>{target}</code> {'banned' if banned else 'unbanned'}."
    raise ValueError("This admin action is not available.")


async def on_admin_text(client: Client, message: Message):
    if not message.from_user or not message.text:
        return
    uid = message.from_user.id
    aw = ctx.awaiting.get(uid)
    if not aw or aw.kind != "admin" or aw.chat_id != message.chat.id:
        return
    if not _is_owner(uid):
        ctx.awaiting.pop(uid, None)
        return
    try:
        response = await _handle_admin_input(message, aw, message.text.strip())
    except (ValueError, OverflowError) as e:
        await tgreply_text(message, f"Invalid input: {esc(str(e))}. Try again or /cancel.", parse_mode=H)
        return
    ctx.awaiting.pop(uid, None)
    for mid in (aw.prompt_msg_id, message.id):
        try:
            await client.delete_messages(message.chat.id, mid)
        except Exception:
            pass
    await tgreply_text(message, response, parse_mode=H,
                       reply_markup=markup([[('🛠️ Admin Panel', 'open:admin', 'primary')]]))


async def _command(client: Client, message: Message, action: str):
    uid = message.from_user.id if message.from_user else 0
    if not _is_owner(uid):
        await tgreply_text(message, "Owner only.")
        return
    parts = (message.text or "").split()[1:]
    try:
        if action == "grant":
            if len(parts) < 2 or len(parts) > 3:
                raise ValueError("Use /grant USER_ID PLAN [DAYS].")
            response = await _handle_admin_input(message, Awaiting("admin_grant", "admin", message.chat.id, message.id, 0, "admin"), " ".join(parts))
        elif action == "revoke":
            response = await _handle_admin_input(message, Awaiting("admin_revoke", "admin", message.chat.id, message.id, 0, "admin"), " ".join(parts))
        else:
            key = "admin_ban" if action == "ban" else "admin_unban"
            response = await _handle_admin_input(message, Awaiting(key, "admin", message.chat.id, message.id, 0, "admin"), " ".join(parts))
        await tgreply_text(message, response, parse_mode=H)
    except (ValueError, OverflowError) as e:
        await tgreply_text(message, f"Invalid input: {esc(str(e))}", parse_mode=H)


async def grant_cmd(client: Client, message: Message):
    await _command(client, message, "grant")


async def revoke_cmd(client: Client, message: Message):
    await _command(client, message, "revoke")


async def ban_cmd(client: Client, message: Message):
    await _command(client, message, "ban")


async def unban_cmd(client: Client, message: Message):
    await _command(client, message, "unban")


def register(app: Client):
    app.add_handler(MessageHandler(show_admin, filters.command("admin")), group=0)
    for name, fn in (("grant", grant_cmd), ("revoke", revoke_cmd), ("ban", ban_cmd), ("unban", unban_cmd)):
        app.add_handler(MessageHandler(fn, filters.command(name)), group=0)
    app.add_handler(MessageHandler(on_admin_text, filters.text & ~filters.regex(r"^/")), group=2)
