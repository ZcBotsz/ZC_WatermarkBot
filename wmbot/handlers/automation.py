"""UI and Telegram handlers for per-user source-to-target channel automation.

Open to the bot owner and to Infinity-plan users; everyone configures their own
source/target channels and only ever sees their own queue and settings."""
from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import MessageHandler
from pyrogram.types import CallbackQuery, Message

from .. import config
from ..db import db
from ..tgio import answer_callback, markup, reply_text as tgreply_text, safe_edit
from ..utils import esc
from .ctx import ctx

H = ParseMode.HTML


def _owner(uid: int | None) -> bool:
    return bool(uid and config.OWNER_ID and uid == config.OWNER_ID)


async def _allowed(uid: int | None) -> bool:
    service = ctx.automation
    if service is not None:
        return await service.can_access(uid)
    return _owner(uid)


def _rows(cfg: dict, uid: int) -> list[list[tuple]]:
    running = bool(cfg.get("running"))
    backfill = bool(cfg.get("backfill_mode"))
    back = ("⬅️ Back to Admin", "auto:admin", "primary") if _owner(uid) else ("⬅️ Back to menu", "open:home", "primary")
    return [
        [("📥 Set Source", "auto:source", "primary"), ("📤 Set Target", "auto:target", "primary")],
        [("🔍 Check Access", "auto:verify", "primary"),
         (("⏸️ Pause" if running else "▶️ Start"), "auto:toggle", "danger" if running else "success")],
        [(("✅ Finish backfill" if backfill else "📚 Backfill old posts"), "auto:backfill", "primary")],
        [("📋 Queue / status", "auto:queue", "primary"), ("🔁 Retry failed", "auto:retry", "primary")],
        [("⏭️ Skip failed", "auto:skip", "danger"), back],
    ]


async def _home_text(uid: int) -> str:
    service = ctx.automation
    if service is None:
        return "<b>Channel Automation</b>\n\nAutomation service is not started. Restart the bot after deploying the automation package."
    cfg, counts = await service.summary(uid)
    status = "🟢 RUNNING" if cfg.get("running") else "⚪ PAUSED / OFF"
    source = f"{esc(cfg.get('source_title') or 'Not set')} <code>{cfg.get('source_chat_id') or ''}</code>"
    target = f"{esc(cfg.get('target_title') or 'Not set')} <code>{cfg.get('target_chat_id') or ''}</code>"
    backfill = "ON — forward old source posts to the bot" if cfg.get("backfill_mode") else "OFF"
    return (
        "📡 <b>Channel Automation</b>\n\n"
        f"Status: <b>{status}</b>\n"
        f"Source: {source}\n"
        f"Target: {target}\n"
        f"Backfill: <b>{backfill}</b>\n\n"
        f"Queue: {counts.get('queued', 0)} waiting · {counts.get('processing', 0)} processing · "
        f"{counts.get('failed', 0)} failed · {counts.get('done', 0)} done\n\n"
        "Only videos and PDFs are watermarked with your own current settings; every other post type is copied unchanged without a Forwarded-from tag (text, photos, stickers, audio, and other documents). "
        "One video/PDF is watermarked at a time. Backfill holds the queue while old posts are forwarded; Finish releases it oldest-first. "
        "Source edits are ignored; NSFW scanning is off."
    )


async def _edit(message: Message, text: str, rows: list[list[tuple]]):
    # The automation panel can be opened from the /start photo card: edit the
    # caption there, and the text elsewhere, so navigation never breaks.
    editor = message.edit_caption if getattr(message, "photo", None) else message.edit_text
    await safe_edit(editor, text, parse_mode=H, reply_markup=markup(rows))


async def _show_home(message: Message, uid: int):
    cfg, _ = await ctx.automation.summary(uid)
    await _edit(message, await _home_text(uid), _rows(cfg, uid))


async def open_automation(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if not await _allowed(uid):
        await tgreply_text(
            message,
            "📡 <b>Channel Automation</b> is included with the 💎 <b>Infinity</b> plan.\n\n"
            "With it you connect your own source → target channels: videos/PDFs are watermarked "
            "with your settings and every other post is copied without a Forwarded-from tag.\n\n"
            "See /plans — upgrades are manual via 💬 Contact Admin.",
            parse_mode=H)
        return
    cfg, _ = await ctx.automation.summary(uid)
    sent = await tgreply_text(message, await _home_text(uid), parse_mode=H, reply_markup=markup(_rows(cfg, uid)))
    ctx.menu_owner[(sent.chat.id, sent.id)] = uid


async def on_callback(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id if cq.from_user else 0
    service = ctx.automation
    if service is None:
        await answer_callback(cq, "Automation service is unavailable.", show_alert=True)
        return
    if not await service.can_access(uid):
        await answer_callback(cq, "Channel Automation is included with the Infinity plan.", show_alert=True)
        return
    data = cq.data or ""
    if data in {"auto:home", "auto:queue"}:
        if data == "auto:queue":
            _, counts = await service.summary(uid)
            items = await db.recent_automation_items(8, uid)
            lines = ["📋 <b>Channel queue</b>",
                     f"Waiting {counts['queued']} · Processing {counts['processing']} · Failed {counts['failed']} · Done {counts['done']}\n"]
            for item in reversed(items):
                status = item["status"].upper()
                line = f"• <code>{esc(item['file_name'])}</code> — {status}"
                if item.get("error") and status == "FAILED":
                    line += f"\n  <i>{esc(item['error'][:160])}</i>"
                lines.append(line)
            await _edit(cq.message, "\n".join(lines), [[("⬅️ Back", "auto:home", "primary")]])
        else:
            await _show_home(cq.message, uid)
        await answer_callback(cq)
        return
    if data in {"auto:source", "auto:target"}:
        role = "source" if data.endswith("source") else "target"
        service.begin_channel_selection(role, uid)
        prompt = (
            f"Forward a post from the <b>{role}</b> channel to this private bot chat, "
            "or reply with its <code>@username</code> or numeric channel ID.\n\n"
            "Add the bot as an admin first. Source and target must be different channels."
        )
        await _edit(cq.message, prompt, [[("⬅️ Back to automation", "auto:home", "primary")]])
        await answer_callback(cq, "Send the channel now.")
        return
    if data == "auto:verify":
        ok, detail = await service.permissions(uid)
        await answer_callback(cq, detail, show_alert=not ok)
        await _show_home(cq.message, uid)
        return
    if data == "auto:toggle":
        cfg, _ = await service.summary(uid)
        ok, detail = await service.set_running(not bool(cfg.get("running")), uid)
        await answer_callback(cq, detail, show_alert=not ok)
        await _show_home(cq.message, uid)
        return
    if data == "auto:backfill":
        cfg, _ = await service.summary(uid)
        try:
            detail = await service.set_backfill(not bool(cfg.get("backfill_mode")), uid)
        except ValueError as e:
            await answer_callback(cq, str(e), show_alert=True)
            return
        await answer_callback(cq, detail, show_alert=False)
        await _show_home(cq.message, uid)
        return
    if data == "auto:retry":
        count = await service.retry_failed(uid)
        await answer_callback(cq, f"Requeued {count} failed item(s).")
        await _show_home(cq.message, uid)
        return
    if data == "auto:skip":
        count = await service.skip_failed(uid)
        await answer_callback(cq, f"Marked {count} failed item(s) skipped.")
        await _show_home(cq.message, uid)
        return
    if data == "auto:admin":
        if not _owner(uid):
            await answer_callback(cq, "Owner only.", show_alert=True)
            return
        from .admin_panel import _menu_rows, _menu_text
        await _edit(cq.message, _menu_text(), _menu_rows())
        await answer_callback(cq)
        return
    await answer_callback(cq)


async def on_channel_post(client: Client, message: Message):
    service = ctx.automation
    if service is not None:
        await service.on_channel_post(message)


def register(app: Client):
    # Listen to every new post in channels; the service watermarks only PDF/video and copies the rest.
    app.add_handler(MessageHandler(on_channel_post, filters.channel), group=0)
    app.add_handler(MessageHandler(open_automation, filters.command("automation")), group=0)
