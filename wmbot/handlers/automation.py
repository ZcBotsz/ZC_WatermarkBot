"""UI and Telegram handlers for per-user source-to-target channel automation."""
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
    back = ("⬅️ Back to Admin", "auto:admin", "primary") if _owner(uid) else ("⬅️ Back", "open:home", "primary")
    return [
        [("📥 Source", "auto:source", "primary"), ("📤 Target", "auto:target", "primary")],
        [("🔍 Check access", "auto:verify", "primary"),
         (("⏸️ Pause" if running else "▶️ Start"), "auto:toggle", "danger" if running else "success")],
        [(("✅ Finish backfill" if backfill else "📚 Backfill old posts"), "auto:backfill", "primary")],
        [("📋 Queue / status", "auto:queue", "primary"), ("🔁 Retry failed", "auto:retry", "primary")],
        [("⏭️ Skip failed", "auto:skip", "danger"), ("⛔ Stop & clear", "auto:clear", "danger")],
        [back],
    ]


def _live_job(uid: int):
    pipeline = ctx.pipeline
    if pipeline is None:
        return None
    return next((job for job in pipeline.user_jobs(uid) if job.automation_target), None)


def _current_line(uid: int, processing_count: int = 0) -> str:
    job = _live_job(uid)
    if job is None:
        return "Current: <i>Preparing source…</i>" if processing_count else ""
    stage = {
        "queued": "Queued", "downloading": "Downloading", "waiting": "Waiting for watermark",
        "processing": "Watermarking", "uploading": "Uploading",
    }.get(job.status, job.status.title())
    name = esc(job.file_name[:54] + ("…" if len(job.file_name) > 54 else ""))
    if job.status in {"downloading", "processing", "uploading"}:
        return f"Current: <code>{name}</code> · {stage} {job.progress_fraction * 100:.0f}%"
    return f"Current: <code>{name}</code> · {stage}"


async def _home_text(uid: int) -> str:
    service = ctx.automation
    if service is None:
        return "<b>Channel Automation</b>\nService unavailable; please try again shortly."
    cfg, counts = await service.summary(uid)
    status = "🟢 Running" if cfg.get("running") else "⚪ Paused"
    source = f"{esc(cfg.get('source_title') or 'Not set')} <code>{cfg.get('source_chat_id') or ''}</code>"
    target = f"{esc(cfg.get('target_title') or 'Not set')} <code>{cfg.get('target_chat_id') or ''}</code>"
    current = _current_line(uid, counts.get("processing", 0))
    lines = [
        "📡 <b>Channel Automation</b>",
        f"Status: {status}",
        f"Source: {source}",
        f"Target: {target}",
        f"Queue: {counts.get('queued', 0)} waiting · {counts.get('processing', 0)} active · {counts.get('failed', 0)} failed",
        f"Backfill: {'ON' if cfg.get('backfill_mode') else 'OFF'}",
    ]
    if current:
        lines.append(current)
    lines.append("<i>Videos/PDFs are watermarked; other posts are copied.</i>")
    return "\n".join(lines)


async def _edit(message: Message, text: str, rows: list[list[tuple]]):
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
        await tgreply_text(message, "📡 Channel Automation is available on the Infinity plan. See /plans.", parse_mode=H)
        return
    cfg, _ = await ctx.automation.summary(uid)
    sent = await tgreply_text(message, await _home_text(uid), parse_mode=H, reply_markup=markup(_rows(cfg, uid)))
    ctx.menu_owner[(sent.chat.id, sent.id)] = uid


async def on_callback(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id if cq.from_user else 0
    service = ctx.automation
    if service is None:
        await answer_callback(cq, "Automation is unavailable.", show_alert=True)
        return
    if not await service.can_access(uid):
        await answer_callback(cq, "Automation is included with the Infinity plan.", show_alert=True)
        return
    data = cq.data or ""
    if data in {"auto:home", "auto:queue"}:
        if data == "auto:queue":
            _, counts = await service.summary(uid)
            items = await db.recent_automation_items(8, uid)
            lines = ["📋 <b>Channel queue</b>",
                     f"{counts['queued']} waiting · {counts['processing']} active · {counts['failed']} failed"]
            current = _current_line(uid, counts.get("processing", 0))
            if current:
                lines.append(current)
            for item in reversed(items):
                status = item["status"].upper()
                line = f"• <code>{esc(item['file_name'])}</code> — {status}"
                if item.get("error") and status == "FAILED":
                    line += f"\n  <i>{esc(item['error'][:120])}</i>"
                lines.append(line)
            await _edit(cq.message, "\n".join(lines), [[("⬅️ Back", "auto:home", "primary")]])
        else:
            await _show_home(cq.message, uid)
        await answer_callback(cq)
        return
    if data in {"auto:source", "auto:target"}:
        role = "source" if data.endswith("source") else "target"
        service.begin_channel_selection(role, uid)
        prompt = f"Send the {role} channel's <code>@username</code>/ID, or forward one of its posts here."
        await _edit(cq.message, prompt, [[("⬅️ Back", "auto:home", "primary")]])
        await answer_callback(cq, "Send the channel now.")
        return
    if data == "auto:verify":
        ok, detail = await service.permissions(uid)
        await answer_callback(cq, detail, show_alert=not ok)
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
        except ValueError as exc:
            await answer_callback(cq, str(exc), show_alert=True)
            return
        await answer_callback(cq, detail, show_alert=True)
        await _show_home(cq.message, uid)
        return
    if data == "auto:clear":
        _, counts = await service.summary(uid)
        unfinished = sum(counts.get(k, 0) for k in ("queued", "processing", "failed"))
        text = (
            f"Stop automation and delete <b>{unfinished}</b> unfinished item(s)?\n"
            "Completed posts will stay recorded to prevent duplicates."
        )
        await _edit(cq.message, text, [
            [("⛔ Stop & clear", "auto:clear:yes", "danger")],
            [("⬅️ Keep queue", "auto:home", "primary")],
        ])
        await answer_callback(cq)
        return
    if data == "auto:clear:yes":
        removed = await service.stop_and_clear(uid)
        await answer_callback(cq, f"Stopped; removed {removed} unfinished item(s).", show_alert=True)
        await _show_home(cq.message, uid)
        return
    if data == "auto:retry":
        count = await service.retry_failed(uid)
        await answer_callback(cq, f"Requeued {count} failed item(s).")
        await _show_home(cq.message, uid)
        return
    if data == "auto:skip":
        count = await service.skip_failed(uid)
        await answer_callback(cq, f"Skipped {count} failed item(s).")
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
    app.add_handler(MessageHandler(on_channel_post, filters.channel), group=0)
    app.add_handler(MessageHandler(open_automation, filters.command("automation")), group=0)
