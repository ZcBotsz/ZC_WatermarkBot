"""Owner-controlled, sequential source-to-target channel automation.

New channel posts are watched only while the saved automation is ON. Old posts can be
added by manually forwarding them to the bot while Backfill mode is enabled. The bot
account does not enumerate channel history. Source edits are intentionally ignored in
this first version (the owner selected that behavior).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import RPCError
from pyrogram.types import Message

from . import config
from .db import db
from .jobs import Job, render_card
from .settings import WMSettings
from .tgio import markup, progress_markup, reply_text, send_message
from .utils import esc
from .video import VIDEO_EXTS

log = logging.getLogger(__name__)


def classify_message(message: Message) -> tuple[str, str, int, bool]:
    """Classify source posts: only videos/PDFs are watermarked; every other post is forwarded."""
    message_id = int(getattr(message, "id", 0) or 0)
    photo = getattr(message, "photo", None)
    if photo:
        return "photo", f"photo_{message_id}.jpg", int(getattr(photo, "file_size", 0) or 0), False

    video = getattr(message, "video", None)
    if video:
        return "video", getattr(video, "file_name", None) or f"video_{message_id}.mp4", int(getattr(video, "file_size", 0) or 0), False

    document = getattr(message, "document", None)
    if document:
        name = getattr(document, "file_name", None) or f"file_{message_id}"
        mime = (getattr(document, "mime_type", None) or "").lower()
        ext = Path(name).suffix.lower()
        size = int(getattr(document, "file_size", 0) or 0)
        if ext == ".pdf" or mime == "application/pdf":
            return "pdf", name, size, True
        if ext in VIDEO_EXTS or mime.startswith("video/"):
            return "video", name, size, True
        return "forward", name, size, True

    # Animations, stickers, audio/voice/video notes, polls, contacts, locations, etc.
    # are not watermark inputs; retain them as native Telegram forwards.
    for attr in ("animation", "sticker", "audio", "voice", "video_note", "poll", "contact", "location", "venue", "dice", "game"):
        media = getattr(message, attr, None)
        if media is not None:
            name = getattr(media, "file_name", None) or f"{attr}_{message_id}"
            return "forward", name, int(getattr(media, "file_size", 0) or 0), False

    # Includes plain text posts, captions on otherwise unrecognized post types, and
    # any future Telegram message type: queue it for native forwarding, never discard it.
    return "forward", f"message_{message_id}", 0, False


def forwarded_channel(message: Message):
    """Pyrogram's forward origin field for a public/private channel post, if exposed."""
    return getattr(message, "forward_from_chat", None)


def media_file_id(message: Message) -> str:
    for name in ("photo", "video", "animation", "document"):
        media = getattr(message, name, None)
        file_id = getattr(media, "file_id", None) if media is not None else None
        if isinstance(file_id, str) and file_id:
            return file_id
    return ""


class ChannelAutomation:
    def __init__(self, app, pipeline):
        self.app = app
        self.pipeline = pipeline
        self.bot_id: int | None = None
        self.awaiting_role: str | None = None
        self._worker: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._live_messages: dict[tuple[int, int], Message] = {}
        self._closed = False

    async def start(self):
        self.bot_id = (await self.app.get_me()).id
        await db.recover_automation_items()
        self._closed = False
        self._worker = asyncio.create_task(self._run(), name="channel-automation-worker")
        self._wake.set()

    async def stop(self):
        self._closed = True
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    @staticmethod
    def is_owner(user_id: int | None) -> bool:
        return bool(user_id and config.OWNER_ID and user_id == config.OWNER_ID)

    async def summary(self) -> tuple[dict, dict[str, int]]:
        return await db.automation_config(), await db.automation_counts()

    def begin_channel_selection(self, role: str):
        if role not in {"source", "target"}:
            raise ValueError("role must be source or target")
        self.awaiting_role = role

    async def _resolve_chat(self, reference: str):
        value = reference.strip()
        if value.startswith("https://t.me/") or value.startswith("http://t.me/"):
            value = value.rstrip("/").split("/")[-1].split("?")[0]
        chat = await self.app.get_chat(value)
        if chat.type != ChatType.CHANNEL:
            raise ValueError("Please choose a Telegram channel, not a group or user.")
        return chat

    async def _save_channel(self, role: str, chat):
        cfg = await db.automation_config()
        old_id = cfg.get(f"{role}_chat_id")
        if role == "source" and cfg.get("target_chat_id") == chat.id:
            raise ValueError("Source and target must be different channels.")
        if role == "target" and cfg.get("source_chat_id") == chat.id:
            raise ValueError("Source and target must be different channels.")
        if old_id and old_id != chat.id:
            counts = await db.automation_counts()
            if any(counts.get(key, 0) for key in ("queued", "processing", "failed")):
                raise ValueError("Finish or skip queued/failed items before changing a channel.")
        title = (chat.title or (f"@{chat.username}" if chat.username else str(chat.id)))[:200]
        if role == "source":
            await db.set_automation_config(source_chat_id=chat.id, source_title=title, running=0, backfill_mode=0)
        else:
            await db.set_automation_config(target_chat_id=chat.id, target_title=title, running=0, backfill_mode=0)
        self._wake.set()
        return title

    async def handle_private_message(self, message: Message) -> bool:
        """Consume the owner's channel-selection or manual-backfill forwards."""
        if not message.from_user or not self.is_owner(message.from_user.id) or message.chat.type != ChatType.PRIVATE:
            return False

        if self.awaiting_role:
            role = self.awaiting_role
            channel = forwarded_channel(message)
            try:
                if channel is None:
                    raw = (message.text or "").strip()
                    if not raw:
                        raise ValueError("Forward a post from that channel or send its @username/numeric ID.")
                    channel = await self._resolve_chat(raw)
                elif channel.type != ChatType.CHANNEL:
                    raise ValueError("That forward is not from a channel. Forward a channel post or send its @username/ID.")
                title = await self._save_channel(role, channel)
                self.awaiting_role = None
                await reply_text(message, f"Saved {role}: <b>{esc(title)}</b> (<code>{channel.id}</code>).", parse_mode=ParseMode.HTML,
                                  reply_markup=markup([[('📡 Channel Automation', 'auto:home', 'primary')]]))
            except (ValueError, RPCError) as e:
                await reply_text(message, f"I couldn't save that channel: {esc(str(e)[:220])}", parse_mode=ParseMode.HTML)
            return True

        cfg = await db.automation_config()
        if not cfg.get("backfill_mode"):
            return False
        channel = forwarded_channel(message)
        if not channel or channel.id != cfg.get("source_chat_id"):
            await reply_text(message, "Backfill is on. Forward any post from the saved source channel, or tap <b>Finish backfill</b>.", parse_mode=ParseMode.HTML)
            return True
        info = classify_message(message)
        if not info:
            await reply_text(message, "I couldn't identify that post type; it was not queued. Please try forwarding it again.")
            return True
        original_id = getattr(message, "forward_from_message_id", None)
        if not original_id:
            await reply_text(message, "Telegram hid the original message ID, so I can't safely deduplicate this forward. It was not queued.")
            return True
        if not cfg.get("target_chat_id"):
            await reply_text(message, "Set the target channel before adding old posts to the backfill queue.")
            return True
        kind, name, size, _as_document = info
        inserted = await db.enqueue_automation_item(
            source_chat_id=channel.id, source_message_id=int(original_id),
            origin_chat_id=message.chat.id, origin_message_id=message.id,
            target_chat_id=int(cfg["target_chat_id"]), kind=kind, file_name=name,
            file_size=size, caption_html=message.caption.html if message.caption else "",
            file_id=media_file_id(message), as_document=_as_document,
        )
        if inserted:
            self._live_messages[(channel.id, int(original_id))] = message
            self._wake.set()
            await reply_text(message, f"Added old post <code>{original_id}</code> to the channel queue.", parse_mode=ParseMode.HTML)
        else:
            await reply_text(message, "That source post is already queued or processed; I did not add it again.")
        return True

    async def on_channel_post(self, message: Message):
        """Persist new media posts from the configured source while automation is ON."""
        if message.chat.type != ChatType.CHANNEL:
            return
        cfg = await db.automation_config()
        source_id = cfg.get("source_chat_id")
        target_id = cfg.get("target_chat_id")
        if not source_id or not target_id or message.chat.id != source_id or not cfg.get("running"):
            return
        if source_id == target_id:
            return
        info = classify_message(message)
        if not info:
            return
        kind, name, size, _as_document = info
        inserted = await db.enqueue_automation_item(
            source_chat_id=message.chat.id, source_message_id=message.id,
            origin_chat_id=message.chat.id, origin_message_id=message.id,
            target_chat_id=int(target_id), kind=kind, file_name=name, file_size=size,
            caption_html=message.caption.html if message.caption else "",
            file_id=media_file_id(message), as_document=_as_document,
        )
        if inserted:
            self._live_messages[(message.chat.id, message.id)] = message
            self._wake.set()

    async def permissions(self) -> tuple[bool, str]:
        cfg = await db.automation_config()
        source_id, target_id = cfg.get("source_chat_id"), cfg.get("target_chat_id")
        if not source_id or not target_id:
            return False, "Set both source and target channels first."
        if source_id == target_id:
            return False, "Source and target must be different channels."
        if self.bot_id is None:
            self.bot_id = (await self.app.get_me()).id
        try:
            source_member = await self.app.get_chat_member(source_id, self.bot_id)
            target_member = await self.app.get_chat_member(target_id, self.bot_id)
        except RPCError as e:
            return False, f"Could not verify bot access: {str(e)[:180]}"
        source_admin = source_member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}
        if not source_admin:
            return False, "The bot must be an administrator in the source channel to receive posts."
        target_admin = target_member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}
        target_rights = getattr(target_member, "privileges", None)
        can_post = target_member.status == ChatMemberStatus.OWNER or bool(getattr(target_rights, "can_post_messages", False))
        if not target_admin or not can_post:
            return False, "The bot must be a target-channel admin with permission to post messages."
        return True, "Source and target permissions look good."

    async def set_running(self, enabled: bool) -> tuple[bool, str]:
        if enabled:
            ok, detail = await self.permissions()
            if not ok:
                return False, detail
        await db.set_automation_config(running=int(enabled))
        self._wake.set()
        return True, "Automation started. New source posts will be queued." if enabled else "Paused. The current item will finish; pending items are kept. New source posts are ignored while paused."

    async def set_backfill(self, enabled: bool) -> str:
        cfg = await db.automation_config()
        if enabled and (not cfg.get("source_chat_id") or not cfg.get("target_chat_id")):
            raise ValueError("Set both source and target before starting backfill.")
        await db.set_automation_config(backfill_mode=int(enabled))
        self._wake.set()
        if enabled:
            return "Backfill is ready. Forward any old post from the saved source into this private bot chat. Only videos and PDFs are watermarked; all other post types are copied without a Forwarded-from tag. New live posts are queued too, and processing waits until you finish backfill."
        return "Backfill mode finished. The saved queue will process oldest source-message ID first."

    async def retry_failed(self) -> int:
        count = await db.retry_automation_failed()
        self._wake.set()
        return count

    async def skip_failed(self) -> int:
        return await db.skip_automation_failed()

    async def _run(self):
        while not self._closed:
            try:
                cfg = await db.automation_config()
                if not cfg.get("running") or cfg.get("backfill_mode"):
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                item = await db.claim_automation_item()
                if item is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        pass
                    continue
                # A pause may have been pressed while the DB item was being claimed.
                latest = await db.automation_config()
                if not latest.get("running") or latest.get("backfill_mode"):
                    await db.finish_automation_item(int(item["id"]), "queued", "Paused before processing.")
                    continue
                await self._process_item(item, cfg)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("channel automation worker loop failed")
                await asyncio.sleep(2)

    async def _load_origin(self, item: dict) -> Message | None:
        key = (int(item["source_chat_id"]), int(item["source_message_id"]))
        msg = self._live_messages.pop(key, None)
        if msg is None:
            try:
                msg = await self.app.get_messages(int(item["origin_chat_id"]), int(item["origin_message_id"]))
            except RPCError:
                msg = None
        if not msg or getattr(msg, "empty", False):
            return None
        return msg

    async def _process_item(self, item: dict, cfg: dict):
        item_id = int(item["id"])
        try:
            target_id = int(item["target_chat_id"])
            if item["kind"] not in {"video", "pdf"}:
                # Copy rather than forward so Telegram does not attach a "Forwarded from" header.
                origin = await self._load_origin(item)
                if origin is not None:
                    copied = await origin.copy(chat_id=target_id)
                else:
                    copied = await self.app.copy_message(
                        chat_id=target_id, from_chat_id=int(item["origin_chat_id"]),
                        message_id=int(item["origin_message_id"]),
                    )
                if not copied:
                    raise RuntimeError("Telegram did not copy the source post.")
                await db.finish_automation_item(item_id, "done")
                return
            origin = await self._load_origin(item)
            if origin is None and not item.get("file_id"):
                raise RuntimeError("The source post is unavailable and Telegram supplied no reusable file ID. Forward it again or skip it.")
            await self._submit_watermark_job(item, origin, cfg)
        except asyncio.CancelledError:
            await db.finish_automation_item(item_id, "queued", "Worker stopped; will retry after restart.")
            raise
        except Exception as e:
            log.exception("channel automation item %s failed", item_id)
            await db.finish_automation_item(item_id, "failed", str(e))
            await self._notify_owner(f"Automation item failed: <code>{esc(item['file_name'])}</code>\n{esc(str(e)[:300])}")

    async def _submit_watermark_job(self, item: dict, origin: Message | None, cfg: dict):
        from .handlers.ctx import ctx

        owner_id = int(config.OWNER_ID)
        settings: WMSettings = (await db.get_settings(owner_id)).copy()
        kind = item["kind"]
        # Automation prioritizes the source post's visible caption and base filename.
        settings.set("caption_mode", "keep")
        settings.set("filename_mode", "keep")
        if kind == "video":
            settings.set("container", "same")
        if kind == "video" and not settings.has_visible_content:
            raise ValueError("Video watermark text is empty. Set it in the owner's Settings first.")
        if kind == "pdf" and not settings.has_pdf_visible_content:
            raise ValueError("PDF watermark text is empty. Set PDF text in the owner's PDF settings first.")
        counts = await db.automation_counts()
        total = max(1, sum(counts.values()))
        finished = counts.get("done", 0) + counts.get("failed", 0) + counts.get("skipped", 0)
        media = None
        if origin is not None:
            media = getattr(origin, "document", None) or getattr(origin, kind, None) or getattr(origin, "animation", None)
        as_document = bool(item.get("as_document"))
        actual_name = getattr(media, "file_name", None) or item["file_name"]
        actual_size = int(getattr(media, "file_size", None) or item["file_size"] or 0)
        caption_html = (origin.caption.html if origin and origin.caption else item.get("caption_html", ""))
        job = Job(
            user_id=owner_id,
            chat_id=int(item["target_chat_id"]),
            kind=kind,
            file_name=actual_name,
            file_size=actual_size,
            settings=settings,
            origin=origin,
            as_document=as_document,
            caption_html=caption_html or "",
            user_msg_id=0,
            deliver_to_dm=False,
            sender_name="Channel automation",
            task_number=finished + 1,
            task_limit=total,
            max_file_bytes=config.MAX_FILE_MB * 1024 * 1024,
            min_crf=23,
            priority=2,
            automation_target=cfg.get("target_title") or str(item["target_chat_id"]),
            origin_file_id=str(item.get("file_id") or ""),
        )
        try:
            job.status_msg = await self.app.send_message(
                owner_id, render_card(job, "📡 <b>Channel automation</b>"),
                parse_mode=ParseMode.HTML, reply_markup=progress_markup(job),
            )
        except RPCError:
            job.status_msg = None  # The owner can still inspect the Automation queue screen.
        ctx.pipeline.submit(job)
        await job.done_event.wait()
        if job.status == "done":
            await db.finish_automation_item(int(item["id"]), "done")
        else:
            await db.finish_automation_item(int(item["id"]), "failed", job.error or f"Watermark job {job.status}.")

    async def _notify_owner(self, text: str):
        if not config.OWNER_ID:
            return
        try:
            await send_message(self.app, int(config.OWNER_ID), text, parse_mode=ParseMode.HTML)
        except Exception:
            log.debug("could not notify owner about an automation failure", exc_info=True)
