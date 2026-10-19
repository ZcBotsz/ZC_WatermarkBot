"""Plan catalogue and user quota pages. Upgrades are assigned manually by the owner."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import Message

from .. import config
from ..db import db
from ..plans import PLANS, get_plan
from ..tgio import markup, reply_text as tgreply_text
from ..utils import humanbytes
from .ctx import ctx
from .user import gate

H = ParseMode.HTML
IST = ZoneInfo("Asia/Kolkata")


def plan_catalogue() -> str:
    lines = ["<b>Plans</b>", ""]
    for key in ("free", "plus", "pro", "infinity"):
        p = PLANS[key]
        daily = "Unlimited jobs/day" if p.daily_jobs is None else f"{p.daily_jobs} jobs/day"
        line = (f"<b>{p.name}</b> — {p.price}\n"
                f"Batch limit {p.batch_limit} · {p.max_file_mb} MB/file · {daily} · CRF limit {p.min_crf}")
        if key == "infinity":
            line += " · priority processing"
        lines.extend((line, ""))
    lines.extend((
        "CRF values lower than your plan limit are clamped; higher CRF values remain available for smaller output files.",
        "",
        "<b>Infinity file-size note:</b> the screenshot says 4 GB, but Telegram bot uploads are capped at 2,000 MB. "
        "This bot therefore enforces a 2,000 MB maximum, not 4 GB.",
        "",
        "Upgrades and renewals are manual. Contact <a href=\"https://t.me/ZCYT_2026\">@ZCYT_2026</a>; "
        "send your Telegram ID from /id. No online payments are processed here.",
    ))
    return "\n".join(lines)


def _menu_buttons(owner: bool = False):
    rows = [[("💬 Contact Admin", "url:https://t.me/ZCYT_2026", "primary"), ("👤 My Plan", "open:myplan", "primary")],
            [("📊 Usage", "open:usage", "primary"), ("⚙️ Settings", "open:m", "primary")]]
    if owner:
        rows.append([("🛠️ Admin Panel", "open:admin", "primary")])
    return rows


async def _myplan_text(uid: int) -> str:
    record = await db.user_profile(uid)
    plan_key = record["plan"] if record else "free"
    p = get_plan(plan_key)
    used = await db.daily_used(uid)
    daily = "unlimited" if p.daily_jobs is None else str(p.daily_jobs)
    expires = record.get("plan_expires") if record else None
    if plan_key == "free":
        expiry_line = "Plan expiry: no expiry"
    elif expires is None:
        expiry_line = "Plan expiry: never (permanent assignment)"
    else:
        expiry_line = f"Renews/expires: {datetime.fromtimestamp(expires, IST).strftime('%d %b %Y, %I:%M %p IST')}"
    active = len(ctx.pipeline.user_jobs(uid)) + sum(1 for j in ctx.pending.values() if j.user_id == uid)
    return (
        f"<b>My plan: {p.name}</b>\n"
        f"Price: {p.price}\n"
        f"Daily jobs: {used}/{daily}\n"
        f"File limit: {p.max_file_mb} MB · Batch limit: {p.batch_limit}\n"
        f"CRF limit: {p.min_crf} · Priority: {'yes' if p.priority == 0 else 'standard'}\n"
        f"Active or awaiting jobs: {active}\n"
        f"{expiry_line}"
    )


async def _usage_text(uid: int) -> str:
    record = await db.user_profile(uid)
    p = get_plan(record["plan"] if record else "free")
    used = await db.daily_used(uid)
    daily_limit = "unlimited" if p.daily_jobs is None else str(p.daily_jobs)
    remaining = "unlimited" if p.daily_jobs is None else str(max(0, p.daily_jobs - used))
    active = len(ctx.pipeline.user_jobs(uid)) + sum(1 for j in ctx.pending.values() if j.user_id == uid)
    lifetime_jobs = int(record.get("jobs") or 0) if record else 0
    lifetime_bytes = int(record.get("bytes_in") or 0) if record else 0
    return (
        f"<b>Usage — {p.name}</b>\n\n"
        f"Today: <b>{used}/{daily_limit}</b> jobs · remaining: <b>{remaining}</b>\n"
        f"Active or awaiting: <b>{active}/{p.batch_limit}</b>\n"
        f"Per-file limit: <b>{p.max_file_mb} MB</b>\n\n"
        f"Lifetime jobs recorded: {lifetime_jobs}\n"
        f"Lifetime input processed: {humanbytes(lifetime_bytes)}\n\n"
        "Daily usage resets at midnight India Standard Time."
    )


async def page_for(which: str, uid: int) -> tuple[str, list[list[tuple]]]:
    owner = uid == config.OWNER_ID
    if which == "plans":
        return plan_catalogue(), _menu_buttons(owner)
    if which == "myplan":
        return await _myplan_text(uid), [[("💎 Plans", "open:plans", "primary"), ("📊 Usage", "open:usage", "primary")],
                                        [("💬 Contact Admin", "url:https://t.me/ZCYT_2026", "primary")]]
    if which == "usage":
        return await _usage_text(uid), [[("👤 My Plan", "open:myplan", "primary"), ("💎 Plans", "open:plans", "primary")],
                                       [("⚙️ Settings", "open:m", "primary")]]
    return "<b>This page is not available.</b>", [[("🏠 Menu", "open:m", "primary")]]


async def plans_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    text, rows = await page_for("plans", message.from_user.id)
    sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = message.from_user.id


async def myplan_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    text, rows = await page_for("myplan", message.from_user.id)
    sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = message.from_user.id


async def usage_cmd(client: Client, message: Message):
    if not await gate(message):
        return
    text, rows = await page_for("usage", message.from_user.id)
    sent = await tgreply_text(message, text, parse_mode=H, reply_markup=markup(rows))
    ctx.menu_owner[(sent.chat.id, sent.id)] = message.from_user.id


def register(app: Client):
    for name, fn in (("plans", plans_cmd), ("myplan", myplan_cmd), ("usage", usage_cmd)):
        app.add_handler(MessageHandler(fn, filters.command(name)), group=0)
