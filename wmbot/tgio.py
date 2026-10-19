"""Telegram I/O: the real `Transport` used by the pipeline + small helpers."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
from pyrogram import Client, StopTransmission
from pyrogram.enums import ParseMode
from pyrogram.errors import BadRequest, FloodWait, MessageNotModified, RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import config
from .jobs import Cancelled, Job, Result
from .utils import safe_name

log = logging.getLogger(__name__)


def markup(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


async def safe_edit(msg_or_client, *args, **kwargs):
    """edit_message_text that swallows 'not modified' and flood-waits."""
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


async def download_url(url: str, dest: Path, on_progress, cancelled, check: bool = True) -> Path:
    limit = config.MAX_FILE_MB * 1024 * 1024
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
    def _cancel_markup(job: Job):
        return markup([[("✖ Cancel", f"cx:{job.id}")]])

    # -- Transport API -------------------------------------------------------------------
    async def download(self, job: Job, dest_dir: Path, on_progress) -> Path:
        path = dest_dir / safe_name(job.file_name)
        if isinstance(job.origin, str):
            return await download_url(job.origin, path, on_progress, job.cancel.is_set)
        res = await self.app.download_media(job.origin, file_name=str(path), progress=self._wrap_progress(job, on_progress))
        if res is None or job.cancel.is_set():
            if job.cancel.is_set():
                raise Cancelled()
            raise RuntimeError("Telegram returned no file (it may have been deleted)")
        return Path(res)

    async def upload(self, job: Job, res: Result, caption: str | None, on_progress) -> None:
        cb = self._wrap_progress(job, on_progress)
        common = dict(chat_id=job.chat_id, reply_to_message_id=job.user_msg_id or None, progress=cb)
        html = bool(caption) and caption == job.caption_html and job.settings.caption_mode == "keep"
        pm = ParseMode.HTML if html else ParseMode.DISABLED
        thumb = str(res.thumb) if res.thumb else None

        async def send(with_caption: str | None):
            cap = dict(caption=with_caption, parse_mode=pm) if with_caption else {}
            if res.kind == "video" and res.as_video:
                return await self.app.send_video(video=str(res.path), duration=res.duration, width=res.width, height=res.height,
                                                 thumb=thumb, supports_streaming=True, **cap, **common)
            if res.kind == "image" and res.as_photo:
                return await self.app.send_photo(photo=str(res.path), **cap, **common)
            return await self.app.send_document(document=str(res.path), thumb=thumb, force_document=True,
                                                file_name=res.path.name, **cap, **common)

        try:
            sent = await send(caption)
        except BadRequest as e:                       # broken HTML in the original caption, etc.
            if caption and any(k in str(e) for k in ("ENTITY", "CAPTION", "PARSE", "MESSAGE_EMPTY")):
                sent = await send(None)
            else:
                raise
        if sent is None:                              # progress callback requested a stop
            raise Cancelled()

    async def edit_status(self, job: Job, text: str, with_cancel: bool = True) -> None:
        if job.status_msg is None:
            return
        await safe_edit(self.app.edit_message_text, job.status_msg.chat.id, job.status_msg.id, text,
                        parse_mode=ParseMode.HTML, reply_markup=self._cancel_markup(job) if with_cancel else None)

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
