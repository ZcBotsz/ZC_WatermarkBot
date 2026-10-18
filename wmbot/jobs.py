"""Job model + staged pipeline.

        ┌───────────┐   ┌──────────────┐   ┌───────────┐
 submit →│ download  │ → │ encode/light │ → │  upload   │ → done
        │ (N slots) │   │ (video | pdf)│   │ (N slots) │
        └───────────┘   └──────────────┘   └───────────┘

Stages run concurrently, so while video #1 is being encoded, video #2 is already downloading and
video #0 is uploading — the CPU never waits for the network.  A semaphore bounds how many files may be
"in flight" so a long queue can't fill the disk.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import config
from .db import db
from .imgmark import IMAGE_EXTS, watermark_image
from .pdfmark import PDFError, watermark_pdf
from .settings import WMSettings
from .utils import Throttle, SpeedMeter, bar, esc, humanbytes, humantime, safe_name
from . import video as V

log = logging.getLogger(__name__)

KIND_ICON = {"video": "🎬", "pdf": "📄", "image": "🖼"}


class Cancelled(Exception):
    pass


@dataclass
class Result:
    path: Path
    kind: str
    size: int = 0
    duration: int = 0
    width: int = 0
    height: int = 0
    thumb: Path | None = None
    as_video: bool = False
    as_photo: bool = False
    note: str = ""


@dataclass
class Job:
    user_id: int
    chat_id: int
    kind: str                       # video | pdf | image
    file_name: str
    file_size: int
    settings: WMSettings
    origin: Any                     # pyrogram Message  or  URL string
    as_document: bool = False       # input arrived as a "file" rather than native media
    caption_html: str = ""
    user_msg_id: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"          # queued downloading waiting processing uploading done failed cancelled
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    workdir: Path = field(default=None)  # type: ignore
    src_path: Path | None = None
    result: Result | None = None
    status_msg: Any = None
    created: float = field(default_factory=time.monotonic)
    t_stage: dict[str, float] = field(default_factory=dict)
    released: bool = False
    error: str = ""

    def __post_init__(self):
        # Keep per-job temp files in a dedicated subtree so startup cleanup cannot
        # remove unrelated files placed in WORK_DIR by the operator.
        self.workdir = config.WORK_DIR / "jobs" / self.id

    @property
    def stem(self) -> str:
        return Path(self.file_name).stem or "file"

    @property
    def out_stem(self) -> str:
        s = self.settings
        stem = safe_name(self.stem)
        if s.filename_mode == "suffix":
            stem += safe_name(s.filename_text, "_wm")
        elif s.filename_mode == "prefix":
            stem = safe_name(s.filename_text, "wm_") + stem
        return stem


class Transport(Protocol):
    """Everything the pipeline needs from the chat platform. Implemented for Telegram in `tgio.py`."""

    async def download(self, job: Job, dest_dir: Path, on_progress) -> Path: ...
    async def upload(self, job: Job, res: Result, caption: str | None, on_progress) -> None: ...
    async def edit_status(self, job: Job, text: str, with_cancel: bool = True) -> None: ...
    async def finish_status(self, job: Job, text: str, ok: bool) -> None: ...


# ──────────────────────────────────────────────────────────────────────────────────────────
# Progress card
# ──────────────────────────────────────────────────────────────────────────────────────────
STAGES = [("downloading", "⬇️ Download"), ("processing", "⚙️ Watermark"), ("uploading", "⬆️ Upload")]


def _chips(status: str) -> str:
    order = [s for s, _ in STAGES]
    cur = order.index("processing" if status == "waiting" else status) if status in order + ["waiting"] else -1
    out = []
    for i, (_, label) in enumerate(STAGES):
        mark = "✅" if i < cur else ("▶️" if i == cur else "▫️")
        out.append(f"{mark} {label.split(' ', 1)[1]}")
    return "  ›  ".join(out)


def render_card(job: Job, headline: str, frac: float | None = None, detail: str = "") -> str:
    icon = KIND_ICON.get(job.kind, "📦")
    lines = [f"{icon} <b>{esc(job.file_name[:60])}</b>", f"<code>{humanbytes(job.file_size)}</code>", "",
             _chips(job.status), "", headline]
    if frac is not None:
        lines.append(f"<code>{bar(frac)}</code> <b>{frac * 100:.0f}%</b>")
    if detail:
        lines.append(f"<i>{detail}</i>")
    return "\n".join(lines)


class Reporter:
    """Throttled progress-message updater for one job."""

    def __init__(self, job: Job, tr: Transport):
        self.job, self.tr = job, tr
        self.throttle = Throttle(config.PROGRESS_EDIT_INTERVAL)
        self.meter = SpeedMeter()

    async def show(self, headline: str, frac: float | None = None, detail: str = "", force: bool = False):
        if self.throttle.ready(force):
            try:
                await self.tr.edit_status(self.job, render_card(self.job, headline, frac, detail))
            except Exception as e:     # never let a UI hiccup kill a job
                log.debug("status edit failed: %s", e)

    def reset(self):
        self.throttle = Throttle(config.PROGRESS_EDIT_INTERVAL)
        self.meter = SpeedMeter()

    async def transfer(self, headline: str, current: int, total: int):
        speed = self.meter.update(current)
        eta = (total - current) / speed if speed > 0 else 0
        frac = current / total if total else 0
        await self.show(headline, frac, f"{humanbytes(current)} / {humanbytes(total)} · {humanbytes(speed)}/s · ETA {humantime(eta)}",
                        force=(current >= total))


# ──────────────────────────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────────────────────────
class Pipeline:
    def __init__(self, transport: Transport):
        self.tr = transport
        self.jobs: dict[str, Job] = {}
        self.dl_q: asyncio.Queue[Job] = asyncio.Queue()
        self.enc_q: asyncio.Queue[Job] = asyncio.Queue()
        self.light_q: asyncio.Queue[Job] = asyncio.Queue()
        self.ul_q: asyncio.Queue[Job] = asyncio.Queue()
        self.inflight = asyncio.Semaphore(config.ENCODE_SLOTS + 2)
        self._tasks: list[asyncio.Task] = []
        self._reporters: dict[str, Reporter] = {}
        self.completed = 0
        self.failed = 0

    # -- lifecycle -------------------------------------------------------------------------
    async def start(self):
        config.WORK_DIR.mkdir(parents=True, exist_ok=True)
        job_root = config.WORK_DIR / "jobs"
        job_root.mkdir(parents=True, exist_ok=True)
        # Only clear this application's managed temporary subtree. Never sweep
        # unrelated files that an operator may keep in WORK_DIR.
        for p in job_root.iterdir():
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        spec = [(self._dl_worker, config.DOWNLOAD_SLOTS), (self._enc_worker, config.ENCODE_SLOTS),
                (self._light_worker, config.LIGHT_SLOTS), (self._ul_worker, config.UPLOAD_SLOTS)]
        for fn, n in spec:
            self._tasks += [asyncio.create_task(fn(), name=f"{fn.__name__}-{i}") for i in range(n)]

    async def stop(self):
        for j in list(self.jobs.values()):
            j.cancel.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -- API -------------------------------------------------------------------------------
    def user_jobs(self, user_id: int) -> list[Job]:
        return [j for j in self.jobs.values() if j.user_id == user_id]

    def submit(self, job: Job) -> int:
        self.jobs[job.id] = job
        self._reporters[job.id] = Reporter(job, self.tr)
        self.dl_q.put_nowait(job)
        return len(self.jobs)

    def cancel(self, job_id: str) -> bool:
        j = self.jobs.get(job_id)
        if not j:
            return False
        j.cancel.set()
        return True

    def cancel_user(self, user_id: int) -> int:
        n = 0
        for j in self.user_jobs(user_id):
            j.cancel.set()
            n += 1
        return n

    def snapshot(self) -> dict:
        by = {}
        for j in self.jobs.values():
            by[j.status] = by.get(j.status, 0) + 1
        return {"active": len(self.jobs), "by_status": by, "completed": self.completed, "failed": self.failed}

    # -- helpers ---------------------------------------------------------------------------
    def _rep(self, job: Job) -> Reporter:
        return self._reporters[job.id]

    async def _finish(self, job: Job, status: str, message: str = ""):
        if job.id not in self.jobs:
            return
        job.status = status
        total = time.monotonic() - job.created
        ok = status == "done"
        if job.released is False:
            job.released = True
            self.inflight.release() if job.t_stage.get("inflight") else None
        shutil.rmtree(job.workdir, ignore_errors=True)
        self.jobs.pop(job.id, None)
        self._reporters.pop(job.id, None)
        if ok:
            self.completed += 1
        elif status == "failed":
            self.failed += 1
        try:
            await self.tr.finish_status(job, message, ok)
        except Exception as e:
            log.debug("finish_status failed: %s", e)
        size_out = job.result.size if job.result else 0
        try:
            await db.record_job(job.user_id, job.kind, job.file_name, job.file_size, size_out, total, ok)
        except Exception:
            log.exception("record_job failed")

    async def _guard(self, job: Job, coro):
        """Run one stage; translate every failure mode into a clean job outcome. Returns True on success."""
        try:
            await coro
            return True
        except (Cancelled, V.Cancelled, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError) and not job.cancel.is_set():
                raise
            await self._finish(job, "cancelled", "🛑 <b>Cancelled.</b>")
        except (V.VideoError, PDFError) as e:
            log.warning("job %s failed: %s", job.id, str(e)[:300])
            await self._finish(job, "failed", f"❌ <b>Failed</b>\n<code>{esc(str(e)[-700:])}</code>")
        except Exception as e:  # pragma: no cover
            if type(e).__name__ == "StopTransmission" or job.cancel.is_set():
                await self._finish(job, "cancelled", "🛑 <b>Cancelled.</b>")
            else:
                log.exception("job %s crashed", job.id)
                await self._finish(job, "failed", f"❌ <b>Unexpected error</b>\n<code>{esc(type(e).__name__)}: {esc(str(e)[:300])}</code>")
        return False

    # -- stage 1: download -----------------------------------------------------------------
    async def _dl_worker(self):
        while True:
            job = await self.dl_q.get()
            if job.id not in self.jobs:
                continue
            if job.cancel.is_set():
                await self._finish(job, "cancelled", "🛑 <b>Cancelled.</b>")
                continue
            await self.inflight.acquire()
            job.t_stage["inflight"] = 1.0
            if job.cancel.is_set():
                await self._finish(job, "cancelled", "🛑 <b>Cancelled.</b>")
                continue
            await self._guard(job, self._download(job))

    async def _download(self, job: Job):
        job.status = "downloading"
        job.workdir.mkdir(parents=True, exist_ok=True)
        rep = self._rep(job)
        rep.reset()
        job.t_stage["dl_start"] = time.monotonic()
        await rep.show("⬇️ <b>Downloading…</b>", 0.0, force=True)

        async def on_progress(cur, total):
            if job.cancel.is_set():
                raise Cancelled()
            await rep.transfer("⬇️ <b>Downloading…</b>", cur, total or job.file_size)

        indir = job.workdir / "in"
        indir.mkdir(parents=True, exist_ok=True)
        job.src_path = await self.tr.download(job, indir, on_progress)
        if job.cancel.is_set():
            raise Cancelled()
        job.t_stage["dl_end"] = time.monotonic()
        job.status = "waiting"
        (self.enc_q if job.kind == "video" else self.light_q).put_nowait(job)
        await rep.show("⏳ <b>Waiting for a free encoder…</b>" if job.kind == "video" else "⚙️ <b>Starting…</b>", force=True)

    # -- stage 2: process ------------------------------------------------------------------
    async def _enc_worker(self):
        while True:
            job = await self.enc_q.get()
            if job.id in self.jobs:
                await self._guard(job, self._process(job)) and self.ul_q.put_nowait(job)

    async def _light_worker(self):
        while True:
            job = await self.light_q.get()
            if job.id in self.jobs:
                await self._guard(job, self._process(job)) and self.ul_q.put_nowait(job)

    async def _process(self, job: Job):
        if job.cancel.is_set():
            raise Cancelled()
        job.status = "processing"
        s, rep = job.settings, self._rep(job)
        rep.reset()
        logo = db.get_logo(job.user_id) if s.logo_enabled else None
        job.t_stage["proc_start"] = time.monotonic()
        await rep.show("⚙️ <b>Adding watermark…</b>", 0.0, force=True)
        src, wd = job.src_path, job.workdir / "out"      # separate dirs: output name may equal input name
        wd.mkdir(parents=True, exist_ok=True)

        if job.kind == "video":
            async def cb(p: V.Progress):
                enc = {"libx264": "x264", "libx265": "x265"}.get(p.encoder, p.encoder.upper() + " ⚡")
                await rep.show("🎞 <b>Encoding video…</b>", p.fraction,
                               f"{p.speed:.1f}× realtime · {p.fps:.0f} fps · {enc} · ETA {humantime(p.eta)}")

            out, info = await V.watermark_video(src, wd, s, logo, job.cancel, job.out_stem, cb)
            container = out.suffix.lower()
            doc_in = job.as_document
            as_video = container == ".mp4" and (s.send_as == "video" or (s.send_as == "auto" and not doc_in))
            thumb = await V.make_thumbnail(out, wd / "thumb.jpg", info.duration)
            job.result = Result(out, "video", out.stat().st_size, int(round(info.duration)), info.width, info.height, thumb, as_video=as_video)

        elif job.kind == "pdf":
            ev = threading.Event()

            async def watch():
                await job.cancel.wait()
                ev.set()

            wt = asyncio.create_task(watch())
            out = wd / f"{job.out_stem}.pdf"
            loop = asyncio.get_running_loop()

            def prog(i, n):
                asyncio.run_coroutine_threadsafe(rep.show("📄 <b>Watermarking pages…</b>", i / n, f"page {i}/{n}"), loop)

            try:
                r = await asyncio.to_thread(watermark_pdf, src, out, s, logo, ev, prog)
            except InterruptedError:
                raise Cancelled()
            finally:
                wt.cancel()
            job.result = Result(out, "pdf", out.stat().st_size, note=f"{r['pages']}/{r['total']} pages")

        else:  # image
            ext = Path(job.file_name).suffix.lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".png"
            out = wd / f"{job.out_stem}{ext}"
            await asyncio.to_thread(watermark_image, src, out, s, logo, )
            size = out.stat().st_size
            from PIL import Image
            with Image.open(out) as im:
                w, h = im.size
            photo_ok = (ext in (".jpg", ".jpeg", ".png") and size < 9.5 * 1024 * 1024 and w + h < 10000
                        and max(w, h) / max(1, min(w, h)) < 20)
            as_photo = photo_ok and (s.send_as == "video" or (s.send_as == "auto" and not job.as_document))
            job.result = Result(out, "image", size, width=w, height=h, as_photo=as_photo)

        # source no longer needed → free disk early
        try:
            src.unlink(missing_ok=True)
        except OSError:
            pass
        job.t_stage["proc_end"] = time.monotonic()

    # -- stage 3: upload -------------------------------------------------------------------
    async def _ul_worker(self):
        while True:
            job = await self.ul_q.get()
            if job.id in self.jobs:
                await self._guard(job, self._upload(job))

    def _caption(self, job: Job) -> tuple[str | None, bool]:
        """(caption, is_html)"""
        s = job.settings
        if s.caption_mode == "none":
            return None, False
        if s.caption_mode == "custom" and s.caption_text.strip():
            return s.caption_text, False
        return (job.caption_html or None), True

    async def _upload(self, job: Job):
        if job.cancel.is_set():
            raise Cancelled()
        job.status = "uploading"
        rep = self._rep(job)
        rep.reset()
        res = job.result
        assert res
        await rep.show("⬆️ <b>Uploading…</b>", 0.0, force=True)

        async def on_progress(cur, total):
            if job.cancel.is_set():
                raise Cancelled()
            await rep.transfer("⬆️ <b>Uploading…</b>", cur, total or res.size)

        caption, _html = self._caption(job)
        t0 = time.monotonic()
        await self.tr.upload(job, res, caption, on_progress)
        t = job.t_stage
        total = time.monotonic() - job.created
        parts = []
        if "dl_end" in t:
            parts.append(f"⬇️ {t['dl_end'] - t['dl_start']:.0f}s")
        if "proc_end" in t:
            parts.append(f"⚙️ {t['proc_end'] - t['proc_start']:.0f}s")
        parts.append(f"⬆️ {time.monotonic() - t0:.0f}s")
        note = f" · {res.note}" if res.note else ""
        msg = (f"✅ <b>Done in {humantime(total)}</b>\n<i>{' · '.join(parts)} · "
               f"{humanbytes(job.file_size)} → {humanbytes(res.size)}{note}</i>")
        await self._finish(job, "done", msg)
