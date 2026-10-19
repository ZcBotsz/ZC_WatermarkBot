"""Telegram I/O: the real `Transport` used by the pipeline + small helpers."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
from pyrogram import Client, StopTransmission
from pyrogram.enums import ButtonStyle, ParseMode
from pyrogram.errors import BadRequest, FloodWait, MessageNotModified, RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import config
from .jobs import Cancelled, Job, Result
from .utils import normalize_video_thumbnail, safe_name

log = logging.getLogger(__name__)


_SMALL_CAPS = str.maketrans({
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ғ", "g": "ɢ", "h": "ʜ",
    "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ", "o": "ᴏ", "p": "ᴘ",
    "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x",
    "y": "ʏ", "z": "ᴢ",
})


_PRESERVE_TEXT = re.compile(
    r"https?://[^\s<>]+|tg://[^\s<>]+|(?<![\w])/[A-Za-z][A-Za-z0-9_]*(?:@[A-Za-z0-9_]+)?|@[A-Za-z0-9_]{4,}"
)


def _small_caps_chunk(text: str) -> str:
    out = []
    at_word_start = True
    for char in text:
        if char.isascii() and char.isalpha():
            out.append(char.upper() if at_word_start else char.lower().translate(_SMALL_CAPS))
            at_word_start = False
        else:
            out.append(char)
            at_word_start = not (char.isalnum() or char in "@#%")
    return "".join(out)


def small_caps_text(text: str) -> str:
    """Use the requested Unicode small-caps look without altering commands, links or handles."""
    text = str(text)
    out, last = [], 0
    for match in _PRESERVE_TEXT.finditer(text):
        out.append(_small_caps_chunk(text[last:match.start()]))
        out.append(match.group())
        last = match.end()
    out.append(_small_caps_chunk(text[last:]))
    return "".join(out)


class _StyledHTML(HTMLParser):
    """Small-cap visible prose and bold it while preserving HTML tags and literal code."""

    _VOID = {"br", "hr", "img", "input", "meta", "link"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.out.append(self.get_starttag_text() or f"<{tag}>")
        if tag.lower() not in self._VOID:
            self.stack.append(tag.lower())

    def handle_startendtag(self, tag, attrs):
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        self.out.append(f"</{tag}>")
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx] == tag:
                del self.stack[idx:]
                break

    def handle_data(self, data):
        if any(tag in {"code", "pre"} for tag in self.stack):
            self.out.append(data)
            return
        left = data[:len(data) - len(data.lstrip())]
        right = data[len(data.rstrip()):] if data.strip() else ""
        core = data[len(left):len(data) - len(right) if right else len(data)]
        if not core:
            self.out.append(data)
            return
        text = small_caps_text(core)
        if not any(tag in {"b", "strong"} for tag in self.stack):
            self.out.append(f"{left}<b>{text}</b>{right}")
        else:
            self.out.append(f"{left}{text}{right}")

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")


def style_message_text(text: str, parse_mode=None) -> tuple[str, object]:
    """Style bot-authored text; keep copyable commands, URLs, handles and code intact."""
    if parse_mode is None:
        source = html.escape(str(text), quote=False)
        mode = ParseMode.HTML
    elif parse_mode == ParseMode.HTML or str(parse_mode).lower() in {"html", "parsemode.html"}:
        source = str(text)
        mode = ParseMode.HTML
    else:
        return small_caps_text(str(text)), parse_mode
    parser = _StyledHTML()
    parser.feed(source)
    parser.close()
    return "".join(parser.out), mode


def _button_text(text: str) -> str:
    """Keep deliberate emoji accents while formatting button prose in small caps."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return small_caps_text(text)


async def reply_text(message, text, *args, **kwargs):
    if isinstance(text, str):
        text, kwargs["parse_mode"] = style_message_text(text, kwargs.get("parse_mode"))
    return await message.reply_text(text, *args, **kwargs)


async def reply_photo(message, photo, *args, **kwargs):
    caption = kwargs.get("caption")
    if isinstance(caption, str):
        kwargs["caption"], kwargs["parse_mode"] = style_message_text(caption, kwargs.get("parse_mode"))
    elif args and isinstance(args[0], str):
        styled, mode = style_message_text(args[0], kwargs.get("parse_mode"))
        args = (styled, *args[1:])
        kwargs["parse_mode"] = mode
    return await message.reply_photo(photo, *args, **kwargs)


async def send_photo(client, *args, **kwargs):
    caption = kwargs.get("caption")
    if isinstance(caption, str):
        kwargs["caption"], kwargs["parse_mode"] = style_message_text(caption, kwargs.get("parse_mode"))
    return await client.send_photo(*args, **kwargs)


async def send_message(client, chat_id, text, *args, **kwargs):
    if isinstance(text, str):
        text, kwargs["parse_mode"] = style_message_text(text, kwargs.get("parse_mode"))
    return await client.send_message(chat_id, text, *args, **kwargs)


async def edit_text(message, text, *args, **kwargs):
    if isinstance(text, str):
        text, kwargs["parse_mode"] = style_message_text(text, kwargs.get("parse_mode"))
    return await message.edit_text(text, *args, **kwargs)


async def answer_callback(cq, text=None, **kwargs):
    if text is not None:
        text = small_caps_text(text)
    return await cq.answer(text, **kwargs)


def _button_style(data: str, override: str | None = None):
    name = (override or "").strip().lower()
    if not name:
        if data in {"x", "rs", "rsy"} or data.startswith(("no:", "cx:", "lgd", "adm:ban", "adm:revoke", "adm:close")):
            name = "danger"
        else:
            name = "primary"
    if name in {"default", "neutral", "off"}:
        # Telegram's default button style is the nearest available OFF/neutral appearance.
        # The Bot API does not expose an arbitrary black button color.
        return None
    return {
        "primary": ButtonStyle.PRIMARY,
        "success": ButtonStyle.SUCCESS,
        "danger": ButtonStyle.DANGER,
    }.get(name, ButtonStyle.PRIMARY)


def markup(rows: list[list[tuple]]) -> InlineKeyboardMarkup | None:
    """Create emoji-accented small-caps buttons. A payload ``url:<https-url>`` creates a link button."""
    if not rows:
        return None
    keyboard = []
    for row in rows:
        buttons = []
        for item in row:
            text, payload = item[0], item[1]
            override = item[2] if len(item) > 2 else None
            style = _button_style(str(payload), override)
            label = _button_text(str(text))
            if str(payload).startswith("url:"):
                buttons.append(InlineKeyboardButton(text=label, url=str(payload)[4:], style=style))
            else:
                buttons.append(InlineKeyboardButton(text=label, callback_data=str(payload), style=style))
        keyboard.append(buttons)
    return InlineKeyboardMarkup(keyboard)


def progress_markup(job: Job) -> InlineKeyboardMarkup:
    """Live job controls: destructive Cancel in red and non-destructive Refresh in blue."""
    return markup([[("❌ Cancel", f"cx:{job.id}", "danger"),
                   ("🔄 Refresh", f"rf:{job.id}", "primary")]])


async def safe_edit(msg_or_client, *args, **kwargs):
    """Style and safely edit a bot-authored text/caption, swallowing harmless Telegram edit errors."""
    args = list(args)
    method_name = getattr(msg_or_client, "__name__", "")
    text_idx = (2 if method_name in {"edit_message_text", "edit_message_caption"} and len(args) > 2
                else next((i for i, value in enumerate(args) if isinstance(value, str)), None))
    if text_idx is not None and isinstance(args[text_idx], str):
        args[text_idx], kwargs["parse_mode"] = style_message_text(args[text_idx], kwargs.get("parse_mode"))
    elif isinstance(kwargs.get("text"), str):
        kwargs["text"], kwargs["parse_mode"] = style_message_text(kwargs["text"], kwargs.get("parse_mode"))
    elif isinstance(kwargs.get("caption"), str):
        kwargs["caption"], kwargs["parse_mode"] = style_message_text(kwargs["caption"], kwargs.get("parse_mode"))
    try:
        return await msg_or_client(*args, **kwargs)
    except MessageNotModified:
        return None
    except FloodWait as e:
        log.debug("FloodWait %ss on edit", e.value)
        return None
    except RPCError as e:
        log.debug("edit failed: %s", e)
        return None


# ──────────────────────────────────────────────────────────────────────────────────────────
# URL helpers (direct-link mode)
# ──────────────────────────────────────────────────────────────────────────────────────────
def is_safe_url(url: str) -> tuple[bool, str]:
    """Allow HTTP(S) only when every DNS answer is publicly routable."""
    try:
        p = urlparse(url)
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return False, "invalid URL"
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return False, "only plain http(s) links are supported"
    try:
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "host not found"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False, "invalid host address"
        if not ip.is_global:
            return False, "that address is not allowed"
    return True, ""


class _PublicResolver(aiohttp.abc.AbstractResolver):
    """Resolve at connection time too, blocking DNS rebinding to private addresses."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
        results = []
        for addr_family, _socktype, proto, _canonname, sockaddr in infos:
            address = sockaddr[0]
            try:
                ip = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError as exc:
                raise OSError("invalid host address") from exc
            if not ip.is_global:
                raise OSError("host resolved to a non-public address")
            results.append({
                "hostname": host,
                "host": address,
                "port": port,
                "family": addr_family,
                "proto": proto,
                "flags": 0,
            })
        if not results:
            raise OSError("host has no addresses")
        return results

    async def close(self):
        return None


def name_from_response(url: str, headers) -> str:
    cd = headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        return safe_name(unquote(m.group(1).strip()))
    name = unquote(Path(urlparse(url).path).name) or "download"
    return safe_name(name)


class _Opened:
    """Async context manager: GET `url`, following redirects by hand so EVERY hop passes the SSRF check."""

    def __init__(self, sess, url, headers=None, check=True):
        self.sess, self.url, self.headers, self.check, self.resp = sess, url, headers or {}, check, None

    async def __aenter__(self):
        url = self.url
        for _ in range(6):
            if self.check:
                ok, why = await asyncio.to_thread(is_safe_url, url)
                if not ok:
                    raise ValueError(why)
            r = await self.sess.get(url, headers=self.headers, allow_redirects=False)
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = str(r.url.join(r.url.__class__(r.headers["Location"])))
                r.release()
                continue
            self.resp = r
            return r
        raise ValueError("too many redirects")

    async def __aexit__(self, *exc):
        if self.resp is not None:
            self.resp.release()


async def head_url(url: str, check: bool = True) -> tuple[str, int, str]:
    """→ (file name, size, content-type). Uses a 1-byte ranged GET because many servers refuse HEAD."""
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(resolver=_PublicResolver() if check else None)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as sess:
        async with _Opened(sess, url, {"Range": "bytes=0-0"}, check) as r:
            if r.status >= 400:
                raise ValueError(f"server answered HTTP {r.status}")
            size = 0
            cr = r.headers.get("Content-Range", "")
            if "/" in cr and cr.split("/")[-1].isdigit():
                size = int(cr.split("/")[-1])
            elif r.headers.get("Content-Length", "").isdigit() and r.status == 200:
                size = int(r.headers["Content-Length"])
            return name_from_response(str(r.url), r.headers), size, r.headers.get("Content-Type", "")


async def download_url(url: str, dest: Path, on_progress, cancelled, check: bool = True, limit_bytes: int | None = None) -> Path:
    limit = min(config.MAX_FILE_MB * 1024 * 1024, limit_bytes) if limit_bytes else config.MAX_FILE_MB * 1024 * 1024
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    connector = aiohttp.TCPConnector(resolver=_PublicResolver() if check else None)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as sess:
        async with _Opened(sess, url, check=check) as r:
            if r.status >= 400:
                raise ValueError(f"download failed: HTTP {r.status}")
            total = int(r.headers.get("Content-Length") or 0)
            if total and total > limit:
                raise ValueError("file is larger than the allowed limit")
            done = 0
            with open(dest, "wb") as f:
                async for chunk in r.content.iter_chunked(1024 * 1024):
                    if cancelled():
                        raise Cancelled()
                    f.write(chunk)
                    done += len(chunk)
                    if done > limit:
                        raise ValueError("file is larger than the allowed limit")
                    await on_progress(done, total or done)
    return dest


# ──────────────────────────────────────────────────────────────────────────────────────────
# Transport
# ──────────────────────────────────────────────────────────────────────────────────────────
class TgTransport:
    def __init__(self, app: Client):
        self.app = app
        self.bot_username: str | None = None

    # -- helpers -------------------------------------------------------------------------
    @staticmethod
    def _wrap_progress(job: Job, on_progress):
        async def cb(current, total):
            try:
                await on_progress(current, total)
            except Cancelled:
                raise StopTransmission
        return cb

    @staticmethod
    def _progress_markup(job: Job):
        return progress_markup(job)

    # -- Transport API -------------------------------------------------------------------
    async def _capture_original_thumbnail(self, job: Job, dest_dir: Path):
        if job.kind != "video" or isinstance(job.origin, str):
            return
        origin = job.origin
        media = next((getattr(origin, name, None) for name in ("video", "animation", "document")
                      if getattr(origin, name, None) is not None), None)
        thumbs = getattr(media, "thumbs", None) if media is not None else None
        if not thumbs:
            return
        try:
            raw = await self.app.download_media(thumbs[-1].file_id, file_name=str(dest_dir / "source_thumb_raw"))
            if raw:
                job.source_thumb = await asyncio.to_thread(
                    normalize_video_thumbnail, Path(raw), dest_dir / "source_thumb.jpg")
        except Exception as e:
            log.debug("original thumbnail download skipped: %s", e)

    async def download(self, job: Job, dest_dir: Path, on_progress) -> Path:
        path = dest_dir / safe_name(job.file_name)
        await self._capture_original_thumbnail(job, dest_dir)
        if isinstance(job.origin, str):
            return await download_url(job.origin, path, on_progress, job.cancel.is_set,
                                      limit_bytes=job.max_file_bytes or None)
        res = await self.app.download_media(job.origin, file_name=str(path), progress=self._wrap_progress(job, on_progress))
        if res is None or job.cancel.is_set():
            if job.cancel.is_set():
                raise Cancelled()
            raise RuntimeError("Telegram returned no file (it may have been deleted)")
        return Path(res)

    async def upload(self, job: Job, res: Result, caption: str | None, on_progress) -> None:
        cb = self._wrap_progress(job, on_progress)
        target_chat = job.user_id if job.deliver_to_dm else job.chat_id
        reply_id = job.user_msg_id if target_chat == job.chat_id else None
        common = dict(chat_id=target_chat, reply_to_message_id=reply_id or None, progress=cb)
        html = bool(caption) and caption == job.caption_html and job.settings.caption_mode == "keep"
        pm = ParseMode.HTML if html else ParseMode.DISABLED
        thumb = str(res.thumb) if res.thumb else None

        async def send(with_caption: str | None):
            cap = dict(caption=with_caption, parse_mode=pm) if with_caption else {}
            if res.kind == "video" and res.as_video:
                return await self.app.send_video(video=str(res.path), duration=res.duration, width=res.width, height=res.height,
                                                 thumb=thumb, supports_streaming=True, **cap, **common)
            return await self.app.send_document(document=str(res.path), thumb=thumb, force_document=True,
                                                file_name=res.path.name, **cap, **common)

        def is_private_chat_error(error: Exception) -> bool:
            text = str(error).upper()
            return any(code in text for code in (
                "PEER_ID_INVALID", "USER_ID_INVALID", "USER_IS_BLOCKED", "CHAT_WRITE_FORBIDDEN",
                "INPUT_USER_DEACTIVATED", "CHAT_NOT_FOUND",
            ))

        async def try_send(with_caption: str | None):
            try:
                return await send(with_caption)
            except RPCError as error:
                if with_caption and any(k in str(error).upper() for k in ("ENTITY", "CAPTION", "PARSE", "MESSAGE_EMPTY")):
                    return await send(None)
                raise

        try:
            sent = await try_send(caption)
            job.delivered_to_dm = bool(job.deliver_to_dm)
        except RPCError as error:
            if not (job.deliver_to_dm and is_private_chat_error(error)):
                raise
            # Users must open the bot once before it can DM a file. Fall back to the group for this job.
            job.dm_fallback = True
            common["chat_id"] = job.chat_id
            common["reply_to_message_id"] = job.user_msg_id or None
            try:
                who = (f"@{job.sender_username}" if job.sender_username else
                       f'<a href="tg://user?id={job.user_id}">You</a>')
                bot_username = self.bot_username
                start_link = f"https://t.me/{bot_username}?start=group" if bot_username else "https://t.me/"
                await send_message(
                    self.app, job.chat_id,
                    f"{who}, open <a href=\"{start_link}\">the bot in private</a> with /start to receive future results there. "
                    "I’m sending this result in the group this time.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            sent = await try_send(caption)
        if sent is None:                              # progress callback requested a stop
            raise Cancelled()

    async def edit_status(self, job: Job, text: str, with_cancel: bool = True) -> None:
        if job.status_msg is None:
            return
        await safe_edit(self.app.edit_message_text, job.status_msg.chat.id, job.status_msg.id, text,
                        parse_mode=ParseMode.HTML, reply_markup=self._progress_markup(job) if with_cancel else None)

    async def finish_status(self, job: Job, text: str, ok: bool) -> None:
        if job.status_msg is None:
            return
        await safe_edit(self.app.edit_message_text, job.status_msg.chat.id, job.status_msg.id, text, parse_mode=ParseMode.HTML)
        if ok:
            msg = job.status_msg

            async def later():
                await asyncio.sleep(10)
                try:
                    await msg.delete()
                except Exception:
                    pass
            asyncio.create_task(later())
