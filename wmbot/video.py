"""Video watermark engine (FFmpeg).

Speed design
────────────
1. The watermark is ONE pre-rendered transparent PNG → FFmpeg only runs the cheap `overlay` filter
   (no per-frame text rasterising, no fontconfig, no `drawtext` dependency).
2. Audio / extra tracks are stream-copied (zero cost) whenever the container allows it.
3. Single pass: decode → (scale) → overlay → encode. The default x264 preset is `ultrafast`; presets/CRF are user-tunable.
4. Optional GPU encode (NVENC / QSV) is auto-detected with a real 1-frame test and silently falls back to CPU.
5. Progress is read from `-progress pipe:1` (machine-readable) instead of scraping stderr.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from . import config
from .render import hex_rgb, pos_xy, render_badge, render_tile_layer
from .settings import WMSettings

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".flv", ".wmv", ".3gp", ".mpg", ".mpeg", ".mts", ".m2ts"}
MP4_AUDIO_OK = {"aac", "mp3", "ac3", "eac3"}


class VideoError(Exception):
    pass


class OutputTooLarge(VideoError):
    """Encoding was stopped early: the output is on track to exceed the size budget."""


class Cancelled(Exception):
    pass


# ──────────────────────────────────────────────────────────────────────────────────────────
# Probe
# ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0            # display size (rotation already applied)
    duration: float = 0.0
    fps: float = 0.0
    vcodec: str = ""
    pix_fmt: str = ""
    acodecs: list[str] = field(default_factory=list)
    size: int = 0
    bit_rate: int = 0
    rotation: int = 0
    has_video: bool = False

    @property
    def has_audio(self) -> bool:
        return bool(self.acodecs)


def _fps(v: str) -> float:
    try:
        a, b = v.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except Exception:
        try:
            return float(v)
        except Exception:
            return 0.0


async def probe(path: str | Path) -> MediaInfo:
    proc = await asyncio.create_subprocess_exec(
        config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise VideoError(f"ffprobe failed: {err.decode(errors='replace')[-300:]}")
    data = json.loads(out or b"{}")
    mi = MediaInfo()
    fmt = data.get("format", {})
    mi.duration = float(fmt.get("duration") or 0)
    mi.size = int(fmt.get("size") or 0)
    mi.bit_rate = int(fmt.get("bit_rate") or 0)
    for st in data.get("streams", []):
        t = st.get("codec_type")
        if t == "video" and not mi.has_video and not (st.get("disposition") or {}).get("attached_pic"):
            mi.has_video = True
            mi.width, mi.height = int(st.get("width") or 0), int(st.get("height") or 0)
            mi.vcodec, mi.pix_fmt = st.get("codec_name", ""), st.get("pix_fmt", "")
            mi.fps = _fps(st.get("avg_frame_rate") or st.get("r_frame_rate") or "0")
            rot = 0
            for sd in st.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(round(float(sd["rotation"])))
            rot = rot or int(round(float((st.get("tags") or {}).get("rotate", 0) or 0)))
            mi.rotation = rot % 360
            if mi.duration <= 0:
                mi.duration = float(st.get("duration") or 0)
        elif t == "audio":
            mi.acodecs.append(st.get("codec_name", ""))
    if mi.rotation in (90, 270):
        mi.width, mi.height = mi.height, mi.width
    return mi


# ──────────────────────────────────────────────────────────────────────────────────────────
# Hardware encoder detection (real 1-frame test, cached)
# ──────────────────────────────────────────────────────────────────────────────────────────
_HW_CACHE: dict[str, bool] = {}


async def hw_available(codec: str) -> str | None:
    """Return the usable hardware encoder name for 'h264'/'h265', or None."""
    cands = {"h264": ["h264_nvenc", "h264_qsv"], "h265": ["hevc_nvenc", "hevc_qsv"]}[codec]
    for enc in cands:
        if enc in _HW_CACHE:
            if _HW_CACHE[enc]:
                return enc
            continue
        proc = await asyncio.create_subprocess_exec(
            config.FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
            "-frames:v", "2", "-c:v", enc, "-f", "null", "-", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        rc = await proc.wait()
        _HW_CACHE[enc] = rc == 0
        if rc == 0:
            log.info("Hardware encoder available: %s", enc)
            return enc
    return None


# ──────────────────────────────────────────────────────────────────────────────────────────
# Command building
# ──────────────────────────────────────────────────────────────────────────────────────────
def output_dims(info: MediaInfo, resolution: str) -> tuple[int, int]:
    W, H = info.width, info.height
    if resolution != "orig":
        target = int(resolution)
        short = min(W, H)
        if short > target:
            k = target / short
            W, H = int(round(W * k / 2) * 2), int(round(H * k / 2) * 2)
    return W - (W % 2), H - (H % 2)


def resolve_container(s: WMSettings, src: Path) -> str:
    if s.container in ("mp4", "mkv"):
        return s.container
    return "mkv" if src.suffix.lower() in {".mkv", ".webm", ".avi", ".ts", ".mts", ".m2ts", ".flv", ".wmv"} else "mp4"


def overlay_args(s: WMSettings, W: int, H: int, bw: int, bh: int) -> str:
    """The `overlay=` filter arguments for the chosen motion mode (all numbers pre-computed)."""
    M = int(round(s.margin / 100.0 * min(W, H)))
    row = "m" if s.position == "c" else s.position[0]
    if s.mode == "tile":
        xy = "x=0:y=0"
    elif s.mode == "scroll":
        S = max(1.0, s.speed / 100.0 * W)
        y = {"t": M, "m": (H - bh) // 2, "b": H - bh - M}[row]
        xy = f"x='W-mod(t*{S:.3f},W+w)':y={max(0, y)}"
    elif s.mode == "bounce":
        S = max(1.0, s.speed / 100.0 * W)
        # Smooth sinusoidal ping-pong: velocity eases into and out of each edge
        # instead of the old triangular wave's constant speed + instant direction
        # flips, which stepped visibly from pixel to pixel and looked like lag.
        # Periods are derived from the speed setting; the vertical leg runs at
        # 0.57x so the path drifts naturally instead of retracing itself.
        period_x = 2.0 * max(1.0, (W - bw)) / S
        period_y = 2.0 * max(1.0, (H - bh)) / max(1.0, S * 0.57)
        xy = (f"x='(W-w)*(0.5-0.5*cos(2*PI*t/{period_x:.3f}))'"
              f":y='(H-h)*(0.5-0.5*cos(2*PI*t/{period_y:.3f}))'")
    elif s.mode == "hop":
        N = max(1.0, s.hop_every)
        p = f"mod(floor(t/{N:g}),4)"
        xy = f"x='if(eq({p},1)+eq({p},2),W-w-{M},{M})':y='if(gte({p},2),H-h-{M},{M})'"
    else:
        x, y = pos_xy(s.position, W, H, bw, bh, M)
        # Keep a static watermark on the 2x2 luma/chroma grid of yuv420p so its
        # chroma alignment cannot land between pixels on alternate frame sizes.
        x, y = max(0, x - (x % 2)), max(0, y - (y % 2))
        xy = f"x={x}:y={y}"
    en = ""
    if s.show_every > 0 and s.show_for < s.show_every:
        en = f":enable='lt(mod(t,{s.show_every:g}),{s.show_for:g})'"
    return f"overlay={xy}{en}:eval=frame:format=yuv420:shortest=0:repeatlast=1"


def fit_badge(badge: Image.Image, W: int, H: int) -> Image.Image:
    """Never let the badge be wider/taller than the frame (breaks bounce/scroll maths)."""
    k = min(1.0, (W * 0.96) / badge.width, (H * 0.96) / badge.height)
    if k < 1.0:
        badge = badge.resize((max(2, int(badge.width * k)), max(2, int(badge.height * k))), Image.LANCZOS)
    return badge


@dataclass
class EncodePlan:
    cmd: list[str]
    out_path: Path
    container: str
    encoder: str
    badge_path: Path


def _encoder_args(s: WMSettings, hw_enc: str | None, threads: int,
                  maxrate_kbps: int | None = None) -> tuple[list[str], str]:
    crf = s.crf
    if hw_enc:
        hevc = "hevc" in hw_enc
        if "nvenc" in hw_enc:
            a = ["-c:v", hw_enc, "-preset", "p4", "-rc", "vbr", "-cq", str(crf + (4 if hevc else 0)), "-b:v", "0", "-pix_fmt", "yuv420p"]
        else:  # qsv
            a = ["-c:v", hw_enc, "-global_quality", str(crf + (4 if hevc else 0)), "-preset", "veryfast", "-pix_fmt", "nv12"]
        if hevc:
            a += ["-tag:v", "hvc1"]
        return a, hw_enc
    # Capped CRF: quality stays CRF-driven, but the video bitrate can never balloon
    # past the budget (an ultrafast encode of an efficiently-compressed source can
    # otherwise come out 2-4x larger than the input and become unuploadable).
    ceiling = ["-maxrate", f"{maxrate_kbps}k", "-bufsize", f"{maxrate_kbps * 2}k"] if maxrate_kbps else []
    if s.codec == "h265":
        return (["-c:v", "libx265", "-preset", s.preset, "-crf", str(min(40, crf + 5)), "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
                 "-threads", str(threads), "-x265-params", f"log-level=error:pools={threads}"] + ceiling, "libx265")
    return (["-c:v", "libx264", "-preset", s.preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
             "-threads", str(threads)] + ceiling, "libx264")


async def build_plan(src: Path, dst_dir: Path, s: WMSettings, info: MediaInfo,
                     out_stem: str, use_hw: bool = True, size_budget_bytes: int | None = None) -> EncodePlan:
    W, H = output_dims(info, s.resolution)
    container = resolve_container(s, src)

    # watermark image (rendered at the OUTPUT resolution so text size is resolution-independent)
    if s.mode == "tile":
        wm = render_tile_layer(s, W, H)
    else:
        wm = render_badge(s, min(W, H))
        if wm is not None:
            wm = fit_badge(wm, W, H)
    if wm is None:
        raise VideoError("Nothing to draw — set a text watermark first.")
    badge_path = dst_dir / "wm.png"
    wm.save(badge_path, optimize=False, compress_level=1)

    parts = []
    scale = f"scale={W}:{H}:flags=bilinear,setsar=1" if (W, H) != (info.width, info.height) else "setsar=1"
    parts.append(f"[0:v:0]{scale}[base]")
    parts.append(f"[base][1:v]{overlay_args(s, W, H, wm.width, wm.height)}[vout]")
    graph = ";".join(parts)

    hw_enc = (await hw_available(s.codec)) if (use_hw and s.hw == "auto") else None
    threads = config.FFMPEG_THREADS
    maxrate_kbps = None
    if size_budget_bytes and info.duration > 0 and hw_enc is None:
        # ~8% of the budget is reserved for audio + container overhead so the
        # FINAL file stays under the budget, not just the video stream. Above
        # ~50 Mbps the ceiling could never plausibly bind (short/small clips),
        # so skip it there instead of handing FFmpeg a nonsense bitrate.
        computed = int(size_budget_bytes * 8 * 0.92 / info.duration / 1000)
        if 400 <= computed <= 50_000:
            maxrate_kbps = computed
    venc, encoder_name = _encoder_args(s, hw_enc, threads, maxrate_kbps)

    out_path = dst_dir / f"{out_stem}.{container}"
    cmd = [config.FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
           "-filter_complex_threads", str(config.FILTER_THREADS),
           "-i", str(src), "-i", str(badge_path), "-filter_complex", graph, "-map", "[vout]"]
    cmd += venc

    audio_all_mp4 = all(c in MP4_AUDIO_OK for c in info.acodecs)
    if info.has_audio:
        cmd += ["-map", "0:a?"]
        if s.audio == "copy" and (container != "mp4" or audio_all_mp4):
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k" if s.audio in ("copy", "aac192") else "128k"]
    if container == "mkv":
        cmd += ["-map", "0:s?", "-c:s", "copy", "-map", "0:t?", "-c:t", "copy"]
    else:
        cmd += ["-sn", "-dn", "-movflags", "+faststart"]
    cmd += ["-map_metadata", "0", "-max_muxing_queue_size", "9999", str(out_path)]
    return EncodePlan(cmd, out_path, container, encoder_name, badge_path)


# ──────────────────────────────────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Progress:
    fraction: float
    speed: float          # ffmpeg realtime multiplier (e.g. 3.2x)
    fps: float
    eta: float            # seconds
    out_size: int
    encoder: str


ProgressCb = Callable[[Progress], Awaitable[None]]


def _num(v, default: float = 0.0) -> float:
    """ffmpeg's -progress output sometimes says 'N/A' (e.g. before the first frame)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _ffmpeg_failure(rc: int, tail: str) -> str:
    if rc in (-9, 137):  # SIGKILL from the host is commonly an OOM/resource-limit kill
        hint = ("FFmpeg was killed by the host, likely because this instance ran out of memory or hit a resource limit. "
                "The bot caps encoder concurrency/threads; if it continues, choose 720p or lower and the ultrafast preset.")
        return f"{hint}\n{tail}" if tail else hint
    return tail or f"ffmpeg exited with code {rc}"


async def _run(plan: EncodePlan, duration: float, cancel: asyncio.Event, cb: ProgressCb | None) -> None:
    proc = await asyncio.create_subprocess_exec(*plan.cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    err_buf: list[bytes] = []

    async def read_err():
        assert proc.stderr
        async for line in proc.stderr:
            err_buf.append(line)
            del err_buf[:-40]

    async def watch_cancel():
        await cancel.wait()
        if proc.returncode is None:
            proc.kill()

    err_task, cancel_task = asyncio.create_task(read_err()), asyncio.create_task(watch_cancel())
    state: dict[str, str] = {}
    last_emit = 0.0
    started = time.monotonic()
    try:
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            state[k] = v
            if k != "progress":
                continue
            now = time.monotonic()
            if cb and (now - last_emit >= 1.0 or v == "end"):
                last_emit = now
                us = _num(state.get("out_time_us"), _num(state.get("out_time_ms")))
                done = max(0.0, us / 1_000_000)
                frac = min(0.999, done / duration) if duration > 0 else 0.0
                try:
                    speed = float((state.get("speed") or "0x").rstrip("x").strip() or 0)
                except ValueError:
                    speed = 0.0
                eta = (duration - done) / speed if speed > 0 and duration > 0 else 0.0
                fps = float(_num(state.get("fps")))
                await cb(Progress(frac, speed, fps, max(0.0, eta), int(_num(state.get("total_size"))), plan.encoder))
        rc = await asyncio.wait_for(proc.wait(), timeout=config.ENCODE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        proc.kill()
        raise VideoError("Encoding timed out")
    finally:
        cancel_task.cancel()
        await asyncio.gather(err_task, return_exceptions=True)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if cancel.is_set():
        raise Cancelled()
    if rc != 0:
        tail = b"".join(err_buf).decode(errors="replace").strip()[-900:]
        raise VideoError(_ffmpeg_failure(rc, tail))
    log.info("encoded %s with %s in %.1fs", plan.out_path.name, plan.encoder, time.monotonic() - started)


async def watermark_video(src: Path, dst_dir: Path, s: WMSettings, cancel: asyncio.Event,
                          out_stem: str, cb: ProgressCb | None = None, info: MediaInfo | None = None,
                          size_budget_bytes: int | None = None) -> tuple[Path, MediaInfo]:
    """Watermark `src`; returns (output path, output MediaInfo). Retries on CPU if the GPU encoder fails."""
    info = info or await probe(src)
    if not info.has_video:
        raise VideoError("No video stream found in this file.")
    dst_dir.mkdir(parents=True, exist_ok=True)
    plan = await build_plan(src, dst_dir, s, info, out_stem, size_budget_bytes=size_budget_bytes)
    try:
        await _run(plan, info.duration, cancel, cb)
    except VideoError as e:
        if isinstance(e, OutputTooLarge):
            raise  # retrying on CPU would just waste the same encoding time again
        if plan.encoder not in ("libx264", "libx265"):
            log.warning("hardware encode failed (%s) — retrying on CPU", str(e)[:200])
            plan = await build_plan(src, dst_dir, s, info, out_stem, use_hw=False, size_budget_bytes=size_budget_bytes)
            await _run(plan, info.duration, cancel, cb)
        else:
            raise
    finally:
        try:
            plan.badge_path.unlink(missing_ok=True)
        except OSError:
            pass
    return plan.out_path, await probe(plan.out_path)


async def make_thumbnail(video: Path, out: Path, duration: float, width: int = 320) -> Path | None:
    at = max(0.0, min(duration * 0.1, 10.0)) if duration > 0 else 0
    proc = await asyncio.create_subprocess_exec(
        config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
        "-vf", f"scale={width}:-2", "-q:v", "5", str(out), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return out if out.exists() and out.stat().st_size > 0 else None
