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
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import config
from .db import db
from .pdfmark import PDFError, watermark_pdf
from .settings import WMSettings
from .utils import Throttle, SpeedMeter, bar, esc, humanbytes, humantime, normalize_video_thumbnail, safe_name
from . import video as V

log = logging.getLogger(__name__)

KIND_ICON = {"video": "🎬", "pdf": "📄"}


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
    note: str = ""


@dataclass
class Job:
    user_id: int
    chat_id: int
    kind: str                       # video | pdf
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
    source_thumb: Path | None = None
    deliver_to_dm: bool = False
    delivered_to_dm: bool = False
    dm_fallback: bool = False
    sender_username: str = ""
    sender_name: str = ""
    task_number: int = 1
    task_limit: int = 1
    progress_current: int = 0
    progress_total: int = 0
    progress_fraction: float = 0.0
    progress_speed: float = 0.0
    progress_speed_text: str = ""
    progress_eta: float | None = None
    progress_estimated: bool = False
    max_file_bytes: int = 0
    min_crf: int = 23
    priority: int = 2
    usage_day: str | None = None
    automation_target: str = ""
    origin_file_id: str = ""
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self):
        # Keep per-job temp files in a dedicated subtree so startup cleanup cannot
        # remove unrelated files placed in WORK_DIR by the operator.
        self.workdir = config.WORK_DIR / "jobs" / self.id
        if self.progress_total <= 0:
            self.progress_total = max(0, int(self.file_size or 0))

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


def pdf_settings_for(settings: WMSettings) -> WMSettings:
    """Return a PDF render profile; its watermark text/style/layout never follows video settings."""
    pdf_settings = settings.copy()
    for video_key, pdf_key in (
        ("text_enabled", "pdf_text_enabled"), ("text", "pdf_text"), ("font", "pdf_font"),
        ("font_size", "pdf_font_size"), ("text_color", "pdf_text_color"),
        ("text_opacity", "pdf_text_opacity"), ("uppercase", "pdf_uppercase"),
        ("stroke_width", "pdf_stroke_width"), ("stroke_color", "pdf_stroke_color"),
        ("shadow", "pdf_shadow"), ("shadow_color", "pdf_shadow_color"),
        ("bg_enabled", "pdf_bg_enabled"), ("bg_color", "pdf_bg_color"),
        ("bg_opacity", "pdf_bg_opacity"), ("bg_padding", "pdf_bg_padding"),
        ("bg_radius", "pdf_bg_radius"), ("border_width", "pdf_border_width"),
        ("border_color", "pdf_border_color"), ("border_opacity", "pdf_border_opacity"),
        ("mode", "pdf_mode"), ("position", "pdf_position"), ("margin", "pdf_margin"),
        ("rotation", "pdf_rotation"), ("tile_gap", "pdf_tile_gap"),
    ):
        pdf_settings.set(video_key, getattr(settings, pdf_key))
    return pdf_settings


class Transport(Protocol):
    """Everything the pipeline needs from the chat platform. Implemented for Telegram in `tgio.py`."""

    async def download(self, job: Job, dest_dir: Path, on_progress) -> Path: ...
    async def upload(self, job: Job, res: Result, caption: str | None, on_progress) -> None: ...
    async def edit_status(self, job: Job, text: str, with_cancel: bool = True) -> None: ...
    async def finish_status(self, job: Job, text: str, ok: bool) -> None: ...


# ──────────────────────────────────────────────────────────────────────────────────────────
# Progress card
# ──────────────────────────────────────────────────────────────────────────────────────────
_STAGE_INFO = {
    "queued": (0, "Queued"),
    "downloading": (1, "Downloading"),
    "waiting": (2, "Waiting"),
    "processing": (2, "Watermarking"),
    "uploading": (3, "Uploading"),
}

_FFMPEG_VERSION: str | None = None


def _ffmpeg_engine_label() -> str:
    """Return `FFmpeg` plus the installed version when it can be detected once."""
    global _FFMPEG_VERSION
    if _FFMPEG_VERSION is None:
        _FFMPEG_VERSION = "FFmpeg"
        try:
            out = subprocess.run(
                [config.FFMPEG, "-version"], capture_output=True, text=True, timeout=2,
            ).stdout.splitlines()
            if out:
                match = re.search(r"\bversion\s+([0-9][^\s]*)", out[0], re.IGNORECASE)
                if match:
                    _FFMPEG_VERSION = f"FFmpeg v{match.group(1)}"
        except Exception:
            pass
    return _FFMPEG_VERSION


def _compact_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    sec = int(max(0, round(float(seconds))))
    if sec < 60:
        return f"{sec}s"
    minutes, secs = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _plain_headline(headline: str) -> str:
    return re.sub(r"<[^>]+>", "", headline or "").strip()


def render_card(job: Job, headline: str, frac: float | None = None, detail: str = "") -> str:
    """Build one compact, self-contained progress card for the live job message."""
    fraction = job.progress_fraction if frac is None else max(0.0, min(1.0, float(frac)))
    total = max(0, int(job.progress_total or job.file_size or 0))
    current = max(0, int(job.progress_current or round(total * fraction)))
    if total:
        current = min(current, total)
    percent = max(0.0, min(100.0, fraction * 100))
    _stage_no, stage_label = _STAGE_INFO.get(
        job.status, (0, job.status.replace("_", " ").title() or "Queued"))
    name = job.file_name if len(job.file_name) <= 60 else job.file_name[:57] + "…"
    if job.automation_target:
        identity = f"Channel automation ({job.user_id})"
        destination = esc(job.automation_target)
    else:
        identity = esc(job.sender_name.strip() or "Telegram user")
        if job.sender_username:
            identity += f" @{esc(job.sender_username)}"
        identity += f" ({job.user_id})"
        destination = "Telegram"
    engine = _ffmpeg_engine_label() if job.kind == "video" else "PyMuPDF"
    speed = job.progress_speed_text or (f"{humanbytes(job.progress_speed)}/s" if job.progress_speed > 0 else "—")
    eta = _compact_duration(job.progress_eta)
    processed_prefix = "≈" if job.progress_estimated else ""
    elapsed = _compact_duration(time.monotonic() - job.created)
    lines = [
        f"{KIND_ICON.get(job.kind, '📦')} <b>{esc(name)}</b>",
        f"<b>Tasks :</b> {max(1, job.task_number)}/{max(1, job.task_limit)}",
        f"<b>Stage :</b> {stage_label}",
    ]
    # Stage already names routine Downloading/Watermarking/Uploading headlines.
    # Keep only genuinely extra headlines (for example, a confirmation prompt).
    plain_headline = _plain_headline(headline).lower()
    routine = ("download", "upload", "watermark", "encoding", "queued", "waiting", "channel automation")
    if headline and not any(word in plain_headline for word in routine):
        lines.append(f"<i>{headline}</i>")
    lines.extend([
        "",
        f"<code>[{bar(fraction, 12)}]</code> <b>{percent:.2f}%</b>",
        "",
        f"<b>Processed :</b> {processed_prefix}{humanbytes(current)}",
        f"<b>Size :</b> {humanbytes(job.file_size)}",
        f"<b>Speed :</b> {speed}",
        f"<b>ETA :</b> {eta}",
        f"<b>Elapsed :</b> {elapsed}",
        f"<b>Upload :</b> {destination}",
        f"<b>Engine :</b> {engine}",
        f"<b>User :</b> {identity}",
    ])
    if detail:
        lines.append(f"<i>{detail}</i>")
    return "\n".join(lines)


class Reporter:
    """Throttled progress editor with a force-refresh path for live ETA estimates."""

    def __init__(self, job: Job, tr: Transport):
        self.job, self.tr = job, tr
        self.throttle = Throttle(config.PROGRESS_EDIT_INTERVAL)
        self.meter = SpeedMeter()
        self.last_headline = "Queued"
        self.last_frac = 0.0
        self.last_detail = ""
        self._last_update = time.monotonic()
        self._transfer_snapshot: tuple[float, int, float, float] | None = None

    async def show(self, headline: str, frac: float | None = None, detail: str = "", force: bool = False):
        self.last_headline = headline
        self.last_detail = detail
        self._last_update = time.monotonic()
        if frac is not None:
            fraction = max(0.0, min(1.0, float(frac)))
            self.last_frac = fraction
            self.job.progress_fraction = fraction
            total = max(0, int(self.job.progress_total or self.job.file_size or 0))
            self.job.progress_current = min(total, int(round(total * fraction))) if total else 0
            self.job.progress_estimated = self.job.status == "processing"
        if self.job.status not in {"downloading", "uploading"}:
            self._transfer_snapshot = None
        if self.throttle.ready(force):
            try:
                await self.tr.edit_status(self.job, render_card(self.job, headline, self.last_frac, detail))
            except Exception as e:     # never let a UI hiccup kill a job
                log.debug("status edit failed: %s", e)

    def reset(self, total: int | None = None):
        self.throttle = Throttle(config.PROGRESS_EDIT_INTERVAL)
        self.meter = SpeedMeter()
        self.last_frac = 0.0
        self.last_detail = ""
        self._last_update = time.monotonic()
        self._transfer_snapshot = None
        self.job.progress_current = 0
        self.job.progress_total = max(0, int(self.job.file_size if total is None else total))
        self.job.progress_fraction = 0.0
        self.job.progress_speed = 0.0
        self.job.progress_speed_text = ""
        self.job.progress_eta = None
        self.job.progress_estimated = False

    async def transfer(self, headline: str, current: int, total: int):
        total = max(0, int(total or self.job.progress_total or self.job.file_size or 0))
        current = max(0, min(total, int(current or 0))) if total else 0
        speed = self.meter.update(current)
        eta = 0.0 if total and current >= total else ((total - current) / speed if speed > 0 else None)
        fraction = current / total if total else 0.0
        self.job.progress_current = current
        self.job.progress_total = total
        self.job.progress_speed = max(0.0, speed)
        self.job.progress_speed_text = ""
        self.job.progress_eta = eta
        self.job.progress_estimated = False
        await self.show(headline, fraction, force=(bool(total) and current >= total))
        self._transfer_snapshot = (float(current), total, max(0.0, speed), self._last_update)

    async def refresh(self):
        """Force an immediate card edit; estimate transfer bytes and count ETA down between callbacks."""
        now = time.monotonic()
        if self._transfer_snapshot and self.job.status in {"downloading", "uploading"}:
            current, total, speed, updated = self._transfer_snapshot
            if speed > 0 and total > 0:
                current = min(float(total), current + speed * max(0.0, now - updated))
                self.job.progress_current = int(current)
                self.job.progress_total = total
                self.job.progress_fraction = current / total
                self.job.progress_speed = speed
                self.job.progress_speed_text = ""
                self.job.progress_eta = max(0.0, (total - current) / speed)
                self.last_frac = self.job.progress_fraction
                self._transfer_snapshot = (current, total, speed, now)
        elif self.job.status == "processing" and self.job.progress_eta is not None:
            self.job.progress_eta = max(0.0, self.job.progress_eta - max(0.0, now - self._last_update))
        await self.show(self.last_headline, self.last_frac, self.last_detail, force=True)


# ──────────────────────────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────────────────────────
class Pipeline:
    def __init__(self, transport: Transport):
        self.tr = transport
        self.jobs: dict[str, Job] = {}
        self.dl_q: asyncio.Queue[Job] = asyncio.Queue()
        # One shared priority queue keeps video and PDF watermarking sequential.
        self.proc_q: asyncio.PriorityQueue[tuple[int, int, Job]] = asyncio.PriorityQueue()
        self._queue_seq = 0
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
        # Processing workers follow ENCODE_SLOTS: >1 lets several files watermark in
        # parallel (FFMPEG_THREADS auto-divides the CPUs between them in config).
        spec = [(self._dl_worker, config.DOWNLOAD_SLOTS), (self._proc_worker, config.ENCODE_SLOTS),
                (self._ul_worker, config.UPLOAD_SLOTS)]
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

    async def refresh(self, job_id: str) -> bool:
        """Immediately re-render one active job card and refresh its ETA estimate."""
        reporter = self._reporters.get(job_id)
        if reporter is None:
            return False
        await reporter.refresh()
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
        if status != "done" and message:
            job.error = message[:700]
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
        if not ok and job.usage_day:
            try:
                await db.refund_daily_job(job.user_id, job.usage_day)
            except Exception:
                log.exception("daily usage refund failed")
        try:
            await self.tr.finish_status(job, message, ok)
        except Exception as e:
            log.debug("finish_status failed: %s", e)
        size_out = job.result.size if job.result else 0
        try:
            await db.record_job(job.user_id, job.kind, job.file_name, job.file_size, size_out, total, ok)
        except Exception:
            log.exception("record_job failed")
        finally:
            job.done_event.set()

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
        rep.reset(job.file_size)
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
        if job.max_file_bytes and job.src_path.stat().st_size > job.max_file_bytes:
            raise ValueError("The downloaded file exceeds your plan's per-file limit.")
        job.t_stage["dl_end"] = time.monotonic()
        job.status = "waiting"
        rep.reset(job.file_size)
        self._queue_seq += 1
        self.proc_q.put_nowait((job.priority, self._queue_seq, job))
        await rep.show("⏳ Waiting for the shared watermark queue…", 0.0, force=True)

    # -- stage 2: shared sequential process queue ------------------------------------------
    async def _proc_worker(self):
        while True:
            _priority, _sequence, job = await self.proc_q.get()
            if job.id in self.jobs:
                await self._guard(job, self._process(job)) and self.ul_q.put_nowait(job)

    async def _process(self, job: Job):
        if job.cancel.is_set():
            raise Cancelled()
        job.status = "processing"
        s, rep = job.settings, self._rep(job)
        rep.reset(job.file_size)
        if job.kind == "video" and s.crf < job.min_crf:
            s.set("crf", job.min_crf)
        job.t_stage["proc_start"] = time.monotonic()
        await rep.show("⚙️ <b>Adding watermark…</b>", 0.0, force=True)
        src, wd = job.src_path, job.workdir / "out"      # separate dirs: output name may equal input name
        wd.mkdir(parents=True, exist_ok=True)

        if job.kind == "video":
            async def cb(p: V.Progress):
                fraction = max(0.0, min(1.0, p.fraction))
                processed_estimate = job.file_size * fraction
                speed = rep.meter.update(processed_estimate)
                job.progress_total = max(0, job.file_size)
                job.progress_speed = max(0.0, speed)
                job.progress_speed_text = f"≈ {humanbytes(speed)}/s"
                job.progress_eta = max(0.0, p.eta)
                await rep.show("🎞 <b>Encoding video…</b>", fraction)

            out, info = await V.watermark_video(src, wd, s, job.cancel, job.out_stem, cb)
            container = out.suffix.lower()
            doc_in = job.as_document
            as_video = container == ".mp4" and (s.send_as == "video" or (s.send_as == "auto" and not doc_in))
            # Keep the source thumbnail unchanged when Telegram supplied one; for direct links, use an unwatermarked source frame.
            thumb = job.source_thumb
            if thumb is None:
                raw_thumb = await V.make_thumbnail(src, wd / "source_thumb_raw.jpg", info.duration)
                thumb = normalize_video_thumbnail(raw_thumb, wd / "source_thumb.jpg") if raw_thumb else None
            if thumb is None:
                raw_thumb = await V.make_thumbnail(out, wd / "output_thumb_raw.jpg", info.duration)
                thumb = normalize_video_thumbnail(raw_thumb, wd / "output_thumb.jpg") if raw_thumb else None
            job.result = Result(out, "video", out.stat().st_size, int(round(info.duration)), info.width, info.height, thumb, as_video=as_video)

        elif job.kind == "pdf":
            ev = threading.Event()

            async def watch():
                await job.cancel.wait()
                ev.set()

            wt = asyncio.create_task(watch())
            out = wd / f"{job.out_stem}.pdf"
            loop = asyncio.get_running_loop()
            page_meter = SpeedMeter()

            def prog(i, n):
                rate_pages = page_meter.update(i)
                fraction = i / n if n else 0.0
                processed_estimate = job.file_size * fraction
                speed = rep.meter.update(processed_estimate)
                job.progress_total = max(0, job.file_size)
                job.progress_speed = max(0.0, speed)
                job.progress_speed_text = f"≈ {humanbytes(speed)}/s"
                job.progress_eta = (n - i) / rate_pages if rate_pages > 0 else None
                asyncio.run_coroutine_threadsafe(rep.show("🖋️ Watermarking PDF…", fraction), loop)

            try:
                r = await asyncio.to_thread(watermark_pdf, src, out, pdf_settings_for(s), ev, prog)
            except InterruptedError:
                raise Cancelled()
            finally:
                wt.cancel()
            job.result = Result(out, "pdf", out.stat().st_size, note=f"{r['pages']}/{r['total']} pages")

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
        if job.result is not None and job.result.size > config.BOT_UPLOAD_CAP_MB * 1024 * 1024:
            # Telegram bots cannot upload more than 2,000 MiB (4 GB is a Premium
            # user-account feature). Fail clearly instead of a raw library error.
            raise V.VideoError(
                f"The watermarked file is {humanbytes(job.result.size)}, over Telegram's "
                f"{config.BOT_UPLOAD_CAP_MB} MB bot upload limit. Lower the resolution or "
                "increase CRF, then try again.")
        job.status = "uploading"
        rep = self._rep(job)
        res = job.result
        assert res
        rep.reset(res.size)
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
        delivery_note = ""
        if job.delivered_to_dm:
            delivery_note = "\nProcessed file sent to your private chat."
        elif job.dm_fallback:
            delivery_note = "\nPrivate chat was not available; the file was sent here."
        msg = (f"<b>Done in {humantime(total)}</b>\n<i>{' · '.join(parts)} · "
               f"{humanbytes(job.file_size)} → {humanbytes(res.size)}{note}</i>{delivery_note}")
        await self._finish(job, "done", msg)
