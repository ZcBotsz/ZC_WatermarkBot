"""Owner-only UI and Telegram handlers for source-to-target channel automation."""
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


def _rows(cfg: dict) -> list[list[tuple]]:
    running = bool(cfg.get("running"))
    backfill = bool(cfg.get("backfill_mode"))
    return [
        [("📥 Set Source", "auto:source", "primary"), ("📤 Set Target", "auto:target", "primary")],
        [("🔍 Check Access", "auto:verify", "primary"),
         (("⏸️ Pause" if running else "▶️ Start"), "auto:toggle", "danger" if running else "success")],
        [(("✅ Finish backfill" if backfill else "📚 Backfill old posts"), "auto:backfill", "primary")],
        [("📋 Queue / status", "auto:queue", "primary"), ("🔁 Retry failed", "auto:retry", "primary")],
        [("⏭️ Skip failed", "auto:skip", "danger"), ("⬅️ Back to Admin", "auto:admin", "primary")],
    ]


async def _home_text() -> str:
    service = ctx.automation
    if service is None:
        return "<b>Channel Automation</b>\n\nAutomation service is not started. Restart the bot after deploying the automation package."
    cfg, counts = await service.summary()
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
        "Photos are forwarded unchanged. Videos and PDFs use your current owner watermark settings. "
        "One video/PDF is watermarked at a time. Backfill holds the queue while old posts are forwarded; Finish releases it oldest-first. "
        "Source edits are ignored; NSFW scanning is off."
    )


async def _edit(message: Message, text: str, rows: list[list[tuple]]):
    await safe_edit(message.edit_text, text, parse_mode=H, reply_markup=markup(rows))


async def _show_home(message: Message):
    cfg = await ctx.automation.summary()
    await _edit(message, await _home_text(), _rows(cfg[0]))


async def open_automation(client: Client, message: Message):
    if not message.from_user or not _owner(message.from_user.id):
        return
    cfg, _ = await ctx.automation.summary()
    await tgreply_text(message, await _home_text(), parse_mode=H, reply_markup=markup(_rows(cfg)))


async def on_callback(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id if cq.from_user else 0
    if not _owner(uid):
        await answer_callback(cq, "Owner only.", show_alert=True)
        return
    service = ctx.automation
    if service is None:
        await answer_callback(cq, "Automation service is unavailable.", show_alert=True)
        return
    data = cq.data or ""
    if data in {"auto:home", "auto:queue"}:
        if data == "auto:queue":
            _, counts = await service.summary()
            items = await db.recent_automation_items(8)
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
            await _show_home(cq.message)
        await answer_callback(cq)
        return
    if data in {"auto:source", "auto:target"}:
        role = "source" if data.endswith("source") else "target"
        service.begin_channel_selection(role)
        prompt = (
            f"Forward a post from the <b>{role}</b> channel to this private bot chat, "
            "or reply with its <code>@username</code> or numeric channel ID.\n\n"
            "Add the bot as an admin first. Source and target must be different channels."
        )
        await _edit(cq.message, prompt, [[("⬅️ Back to automation", "auto:home", "primary")]])
        await answer_callback(cq, "Send the channel now.")
        return
    if data == "auto:verify":
        ok, detail = await service.permissions()
        await answer_callback(cq, detail, show_alert=not ok)
        await _show_home(cq.message)
        return
    if data == "auto:toggle":
        cfg, _ = await service.summary()
        ok, detail = await service.set_running(not bool(cfg.get("running")))
        await answer_callback(cq, detail, show_alert=not ok)
        await _show_home(cq.message)
        return
    if data == "auto:backfill":
        cfg, _ = await service.summary()
        try:
            detail = await service.set_backfill(not bool(cfg.get("backfill_mode")))
        except ValueError as e:
            await answer_callback(cq, str(e), show_alert=True)
            return
        await answer_callback(cq, detail, show_alert=False)
        await _show_home(cq.message)
        return
    if data == "auto:retry":
        count = await service.retry_failed()
        await answer_callback(cq, f"Requeued {count} failed item(s).")
        await _show_home(cq.message)
        return
    if data == "auto:skip":
        count = await service.skip_failed()
        await answer_callback(cq, f"Marked {count} failed item(s) skipped.")
        await _show_home(cq.message)
        return
    if data == "auto:admin":
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
    supported = filters.photo | filters.video | filters.animation | filters.document
    app.add_handler(MessageHandler(on_channel_post, filters.channel & supported), group=0)
    app.add_handler(MessageHandler(open_automation, filters.command("automation")), group=0)
