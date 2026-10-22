"""Telegram I/O: the real `Transport` used by the pipeline + small helpers."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import math
import os
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
        if data in {"rs", "rsy"} or data.startswith(("no:", "cx:", "lgd", "adm:ban", "adm:revoke")):
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
# Parallel Telegram downloads
# ──────────────────────────────────────────────────────────────────────────────────────────
_MIB = 1024 * 1024
_SEGMENT_CHUNKS = 4          # one ranged fetch covers 4 MiB; workers pull segments from a shared queue
_SEGMENT_ATTEMPTS = 3


def _media_file_ref(job: Job) -> tuple[str, int] | None:
    """(file_id, file_size) of a job's Telegram media, or None when it can't be fetched by id."""
    size = job.file_size if isinstance(job.file_size, int) else 0
    if isinstance(job.origin_file_id, str) and job.origin_file_id and size:
        return job.origin_file_id, size
    origin = job.origin
    if origin is None or isinstance(origin, str):
        return None
    media_items = [origin]
    for name in ("video", "document", "animation", "audio", "photo", "voice", "video_note", "sticker"):
        media = getattr(origin, name, None)
        if media is not None:
            media_items.append(media)
    for media in media_items:
        file_id = getattr(media, "file_id", None)
        if isinstance(file_id, str) and file_id:
            raw = getattr(media, "file_size", None)
            return file_id, raw if isinstance(raw, int) and raw > 0 else size
    return None


class DownloadPool:
    """Extra bot sessions whose only job is to be additional download connections.

    Telegram throttles a single media connection (stock single-stream downloads sit
    around 2-3 MB/s), so fast downloaders fetch ranged chunks of one file over
    several connections (FastTelethon / tdl / TGPorter style). Each helper is the
    same bot logged in with its own session and no updates, which gives the
    parallel downloader an independent connection to the file's DC. Helpers are
    best-effort: if one cannot start, downloads simply use fewer connections.
    """

    def __init__(self, extra: int):
        self.extra = max(0, int(extra))
        self.clients: list[Client] = []
        self._started = False

    async def start(self) -> list[Client]:
        if self._started:
            return self.clients
        self._started = True
        for i in range(self.extra):
            try:
                client = Client(
                    f"{config.SESSION_NAME}_dl{i + 1}", api_id=config.API_ID, api_hash=config.API_HASH,
                    bot_token=config.BOT_TOKEN, workdir=str(config.DATA_DIR), workers=4,
                    sleep_threshold=60, no_updates=True,
                    max_concurrent_transmissions=config.MAX_CONCURRENT_TRANSMISSIONS)
                await client.start()
                self.clients.append(client)
            except Exception:
                log.warning("download helper session %d could not start; using fewer connections", i + 1,
                            exc_info=True)
        if self.clients:
            log.info("parallel download pool ready: %d extra connection(s)", len(self.clients))
        return self.clients

    async def stop(self):
        clients, self.clients = self.clients, []
        self._started = False
        for client in clients:
            try:
                await client.stop()
            except Exception:
                log.debug("download helper stop failed", exc_info=True)


# ──────────────────────────────────────────────────────────────────────────────────────────
# Transport
# ──────────────────────────────────────────────────────────────────────────────────────────
class TgTransport:
    def __init__(self, app: Client, download_pool: DownloadPool | None = None):
        self.app = app
        self.bot_username: str | None = None
        self.download_pool = download_pool

    async def start_download_pool(self):
        if self.download_pool is not None:
            await self.download_pool.start()

    async def stop_download_pool(self):
        if self.download_pool is not None:
            await self.download_pool.stop()

    def _download_clients(self) -> list:
        helpers = list(self.download_pool.clients) if self.download_pool is not None else []
        return [self.app, *helpers]

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

    async def _download_parallel(self, job: Job, path: Path, file_id: str, file_size: int, on_progress) -> Path:
        """Fetch one Telegram file as ranged segments over several connections at once.

        The file is split into 4 MiB segments; each worker pulls the next segment from
        a shared queue and streams it in 1 MiB chunks (``stream_media`` offset/limit)
        from its assigned client, writing at the correct offset with ``os.pwrite`` —
        no merge pass. A failed segment resumes from its last written chunk. Progress
        is the aggregate byte count across all workers.
        """
        clients = self._download_clients()
        total_chunks = max(1, math.ceil(file_size / _MIB))
        segments = [(start, min(_SEGMENT_CHUNKS, total_chunks - start))
                    for start in range(0, total_chunks, _SEGMENT_CHUNKS)]
        workers_n = max(1, min(config.TG_DOWNLOAD_CONNECTIONS, len(segments)))
        queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
        for seg in segments:
            queue.put_nowait(seg)
        done_bytes = 0
        lock = asyncio.Lock()

        with open(path, "wb") as f:
            f.truncate(file_size)

        async def report(n: int):
            nonlocal done_bytes
            async with lock:
                done_bytes += n
                current = done_bytes
            await on_progress(current, file_size)

        async def fetch_segment(client, fd: int, seg_start: int, seg_chunks: int):
            seg_begin = seg_start * _MIB
            seg_end = min(file_size, seg_begin + seg_chunks * _MIB)
            pos = seg_begin
            attempts = 0
            while pos < seg_end:
                if job.cancel.is_set():
                    raise Cancelled()
                try:
                    offset = pos // _MIB
                    limit = math.ceil((seg_end - pos) / _MIB)
                    async for chunk in client.stream_media(file_id, limit=limit, offset=offset):
                        if job.cancel.is_set():
                            raise Cancelled()
                        if not chunk:
                            continue
                        data = bytes(chunk[:seg_end - pos])
                        await asyncio.to_thread(os.pwrite, fd, data, pos)
                        pos += len(data)
                        await report(len(data))
                    if pos < seg_end:
                        raise ConnectionError("Telegram ended a download segment early")
                except (Cancelled, StopTransmission, asyncio.CancelledError, FloodWait):
                    raise
                except Exception:
                    attempts += 1
                    if attempts >= _SEGMENT_ATTEMPTS:
                        raise
                    await asyncio.sleep(0.5 * attempts)

        async def worker(index: int):
            client = clients[index % len(clients)]
            fd = os.open(path, os.O_WRONLY)
            try:
                while True:
                    try:
                        seg_start, seg_chunks = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    await fetch_segment(client, fd, seg_start, seg_chunks)
            finally:
                os.close(fd)

        tasks = [asyncio.create_task(worker(i)) for i in range(workers_n)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if done_bytes != file_size or path.stat().st_size != file_size:
            raise ConnectionError(f"parallel download incomplete ({done_bytes}/{file_size} bytes)")
        return path

    async def download(self, job: Job, dest_dir: Path, on_progress) -> Path:
        path = dest_dir / safe_name(job.file_name)
        await self._capture_original_thumbnail(job, dest_dir)
        ref = _media_file_ref(job)
        if (ref is not None and ref[1] >= config.TG_PARALLEL_MIN_MB * _MIB
                and config.TG_DOWNLOAD_CONNECTIONS > 1 and hasattr(os, "pwrite")):
            try:
                return await self._download_parallel(job, path, ref[0], ref[1], on_progress)
            except (Cancelled, StopTransmission, asyncio.CancelledError, FloodWait):
                path.unlink(missing_ok=True)
                raise
            except Exception:
                log.warning("parallel download failed for %s; falling back to single-connection download",
                            job.file_name, exc_info=True)
                path.unlink(missing_ok=True)
        if job.origin_file_id:
            res = await self.app.download_media(job.origin_file_id, file_name=str(path),
                                                progress=self._wrap_progress(job, on_progress))
        elif isinstance(job.origin, str):
            return await download_url(job.origin, path, on_progress, job.cancel.is_set,
                                      limit_bytes=job.max_file_bytes or None)
        else:
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
