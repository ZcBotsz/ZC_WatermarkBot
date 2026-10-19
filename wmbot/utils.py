from __future__ import annotations

import html
import re
import shutil
import time
from pathlib import Path


def humanbytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def humantime(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def bar(frac: float, width: int = 12) -> str:
    """Compact square progress bar used inside Telegram's monospace status cards."""
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * width))
    return "■" * full + "□" * (width - full)


def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def normalize_video_thumbnail(source: Path, dest: Path) -> Path | None:
    """Normalize a thumbnail for Telegram: preserve the image, fit within 320px and <200KB JPEG."""
    try:
        from PIL import Image, ImageOps
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((320, 320), Image.Resampling.LANCZOS)
        for quality in (90, 82, 74, 65):
            image.save(dest, "JPEG", quality=quality, optimize=True, progressive=False)
            if dest.stat().st_size < 190 * 1024:
                return dest
        return dest if dest.exists() and dest.stat().st_size < 200 * 1024 else None
    except Exception:
        return None


_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_name(name: str, default: str = "file", maxlen: int = 120) -> str:
    name = _BAD.sub("_", name or "").strip(" .") or default
    if len(name) > maxlen:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: maxlen - len(ext) - 1] + dot + ext) if dot else name[:maxlen]
    return name


def free_disk_gb(path: Path) -> float:
    """Return free space for `path`, even before the configured work directory exists."""
    try:
        probe = Path(path)
        if probe.exists() and not probe.is_dir():
            probe = probe.parent
        while not probe.exists():
            parent = probe.parent
            if parent == probe:
                return 0.0
            probe = parent
        return shutil.disk_usage(probe).free / 1024 ** 3
    except OSError:
        return 0.0


class Throttle:
    """Allow an action at most once per `interval` seconds."""

    def __init__(self, interval: float):
        self.interval, self._last = interval, 0.0

    def ready(self, force: bool = False) -> bool:
        now = time.monotonic()
        if force or now - self._last >= self.interval:
            self._last = now
            return True
        return False


class SpeedMeter:
    """Smoothed bytes/sec."""

    def __init__(self):
        self.t0 = time.monotonic()
        self._last_t, self._last_b, self.speed = self.t0, 0, 0.0

    def update(self, current: int) -> float:
        now = time.monotonic()
        dt = now - self._last_t
        if dt >= 1.0:
            inst = (current - self._last_b) / dt
            self.speed = inst if self.speed == 0 else self.speed * 0.6 + inst * 0.4
            self._last_t, self._last_b = now, current
        return self.speed or (current / max(now - self.t0, 0.001))
