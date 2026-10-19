"""Environment-backed configuration. Everything has a safe default except the 3 Telegram credentials."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _ids(name: str) -> set[int]:
    out: set[int] = set()
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def _parse_cpu_max(text: str) -> int | None:
    """Parse cgroup v2 cpu.max into a conservative whole-core limit."""
    parts = text.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, quota // period)


def _parse_cpu_v1(quota_text: str, period_text: str) -> int | None:
    """Parse cgroup v1 CFS quota/period values."""
    try:
        quota, period = int(quota_text.strip()), int(period_text.strip())
    except ValueError:
        return None
    if quota <= 0 or period <= 0:  # quota -1 means unlimited
        return None
    return max(1, quota // period)


def _parse_memory_limit(text: str) -> int | None:
    """Parse a cgroup memory limit, returning bytes or None for unlimited/invalid."""
    try:
        limit = int(text.strip())
    except ValueError:
        return None
    if limit <= 0 or limit >= (1 << 60):  # cgroup v1 uses a huge sentinel for unlimited
        return None
    return limit


def _cgroup_roots(controller: str) -> list[Path]:
    roots = [Path("/sys/fs/cgroup")]
    if controller == "cpu":
        roots += [Path("/sys/fs/cgroup/cpu"), Path("/sys/fs/cgroup/cpu,cpuacct")]
    elif controller == "memory":
        roots += [Path("/sys/fs/cgroup/memory")]
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            _, controllers, member = line.split(":", 2)
            if not controllers:  # cgroup v2
                roots.append(Path("/sys/fs/cgroup") / member.lstrip("/"))
            elif controller in controllers.split(",") or (controller == "cpu" and "cpuacct" in controllers.split(",")):
                rel = member.lstrip("/")
                bases = [Path("/sys/fs/cgroup/cpu"), Path("/sys/fs/cgroup/cpu,cpuacct")] if controller == "cpu" else [Path("/sys/fs/cgroup/memory")]
                roots.extend(base / rel for base in bases)
    except (OSError, ValueError):
        pass
    return list(dict.fromkeys(roots))


def effective_cpu_count() -> int:
    """Use affinity/cgroup quota instead of the host's often-misleading os.cpu_count()."""
    count = os.cpu_count() or 1
    try:
        count = min(count, max(1, len(os.sched_getaffinity(0))))
    except (AttributeError, OSError):
        pass
    for root in _cgroup_roots("cpu"):
        try:
            limit = _parse_cpu_max((root / "cpu.max").read_text())
        except OSError:
            limit = None
        if limit is None:
            try:
                limit = _parse_cpu_v1((root / "cpu.cfs_quota_us").read_text(),
                                      (root / "cpu.cfs_period_us").read_text())
            except OSError:
                limit = None
        if limit is not None:
            count = min(count, limit)
            break
    return max(1, count)


def memory_limit_bytes() -> int | None:
    """Return the container's cgroup memory cap, if one is set."""
    for root in _cgroup_roots("memory"):
        for filename in ("memory.max", "memory.limit_in_bytes"):
            try:
                limit = _parse_memory_limit((root / filename).read_text())
            except OSError:
                limit = None
            if limit is not None:
                return limit
    return None


def memory_current_bytes() -> int | None:
    """Return current cgroup memory usage, including child FFmpeg processes."""
    for root in _cgroup_roots("memory"):
        for filename in ("memory.current", "memory.usage_in_bytes"):
            try:
                value = int((root / filename).read_text().strip())
            except (OSError, ValueError):
                continue
            if value >= 0:
                return value
    return None


def cpu_usage_seconds() -> float | None:
    """Read cumulative cgroup CPU time (includes FFmpeg child processes)."""
    for root in _cgroup_roots("cpu"):
        try:
            stats = (root / "cpu.stat").read_text().splitlines()
            for line in stats:
                key, value = line.split()[:2]
                if key == "usage_usec":
                    return int(value) / 1_000_000
        except (OSError, ValueError):
            pass
        try:
            return int((root / "cpuacct.usage").read_text().strip()) / 1_000_000_000
        except (OSError, ValueError):
            continue
    return None


# ── Telegram credentials (https://my.telegram.org  +  @BotFather) ────────────────────────
API_ID = _int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION_NAME = os.getenv("SESSION_NAME", "wmbot")

# ── Access control ───────────────────────────────────────────────────────────────────────
OWNER_ID = _int("OWNER_ID", 0)
ADMINS = {OWNER_ID} if OWNER_ID else set()   # OWNER_ID is the sole administrator
ALLOWED_USERS = _ids("ALLOWED_USERS")        # extra users allowed when PUBLIC_MODE=false
AUTH_CHATS = _ids("AUTH_CHATS")              # groups where the bot may be used
PUBLIC_MODE = _bool("PUBLIC_MODE", True)     # true = anyone may use the bot in private chat

# ── Paths ────────────────────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
WORK_DIR = Path(os.getenv("WORK_DIR", ROOT / "downloads"))
FONT_DIRS = [ROOT / "assets" / "fonts", DATA_DIR / "fonts", Path("/usr/share/fonts"),
             Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
DB_PATH = DATA_DIR / "bot.db"
# ── Binaries ─────────────────────────────────────────────────────────────────────────────
FFMPEG = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

# ── Performance knobs ────────────────────────────────────────────────────────────────────
# Container-aware: Railway/Docker can expose the host's CPU count (e.g. 32) despite a tiny quota.
CPU_COUNT = effective_cpu_count()
MEMORY_LIMIT_BYTES = memory_limit_bytes()
MEMORY_LIMIT_MB = MEMORY_LIMIT_BYTES // (1024 ** 2) if MEMORY_LIMIT_BYTES else None
LOW_MEMORY_MODE = MEMORY_LIMIT_MB is not None and MEMORY_LIMIT_MB < 2048

# Default to one encoder. Respect low-memory container caps even if ENCODE_SLOTS was set too high.
_requested_encoders = max(1, _int("ENCODE_SLOTS", 1))
if LOW_MEMORY_MODE:
    _requested_encoders = 1
ENCODE_SLOTS = max(1, min(CPU_COUNT, _requested_encoders))

# FFmpeg thread pools consume memory as well as CPU. Cap at 4, and at 1–2 for small RAM limits.
_thread_cap = min(4, max(1, CPU_COUNT // ENCODE_SLOTS))
if MEMORY_LIMIT_MB is not None:
    if MEMORY_LIMIT_MB < 1024:
        _thread_cap = 1
    elif MEMORY_LIMIT_MB < 2048:
        _thread_cap = min(_thread_cap, 2)
FFMPEG_THREADS = max(1, min(_thread_cap, _int("FFMPEG_THREADS", _thread_cap)))
FILTER_THREADS = max(1, min(2, _int("FFMPEG_FILTER_THREADS", 1)))

_transfer_slots_cap = 1 if LOW_MEMORY_MODE else 3
DOWNLOAD_SLOTS = max(1, min(_transfer_slots_cap, _int("DOWNLOAD_SLOTS", _transfer_slots_cap)))
UPLOAD_SLOTS = max(1, min(_transfer_slots_cap, _int("UPLOAD_SLOTS", _transfer_slots_cap)))
LIGHT_SLOTS = max(1, min(1 if LOW_MEMORY_MODE else 3, _int("LIGHT_SLOTS", 1 if LOW_MEMORY_MODE else 3)))
# Telegram transfer parallelism (Pyrogram). Keep modest on small instances.
_tg_transmission_cap = 4 if MEMORY_LIMIT_MB is not None and MEMORY_LIMIT_MB < 1024 else 8
MAX_CONCURRENT_TRANSMISSIONS = max(1, min(_tg_transmission_cap,
                                           _int("MAX_CONCURRENT_TRANSMISSIONS", _tg_transmission_cap)))
TG_WORKERS = max(1, _int("TG_WORKERS", 16))

# ── Limits ───────────────────────────────────────────────────────────────────────────────
MAX_FILE_MB = _int("MAX_FILE_MB", 2000)          # bots can upload up to 2000 MB over MTProto
MAX_QUEUE_PER_USER = _int("MAX_QUEUE_PER_USER", 500)
MAX_PDF_PAGES = _int("MAX_PDF_PAGES", 2000)
ENCODE_TIMEOUT_SEC = _int("ENCODE_TIMEOUT_SEC", 4 * 3600)
PROGRESS_EDIT_INTERVAL = float(os.getenv("PROGRESS_EDIT_INTERVAL", "4"))

# ── Branding ─────────────────────────────────────────────────────────────────────────────
DEFAULT_WATERMARK_TEXT = os.getenv("DEFAULT_WATERMARK_TEXT", "")
DEFAULT_PRESET = os.getenv("DEFAULT_PRESET", "ultrafast").strip().lower()
if DEFAULT_PRESET not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}:
    DEFAULT_PRESET = "ultrafast"
BOT_NAME = os.getenv("BOT_NAME", "ZC Watermark Bot")


def validate() -> list[str]:
    """Return a list of human-readable problems (empty list = OK)."""
    problems = []
    if not API_ID or not API_HASH:
        problems.append("API_ID / API_HASH missing — get them at https://my.telegram.org")
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN missing — create a bot with @BotFather")
    if not OWNER_ID:
        problems.append("OWNER_ID missing — send /id to @userinfobot to find your numeric id")
    if shutil.which(FFMPEG) is None and not Path(FFMPEG).exists():
        problems.append(f"ffmpeg not found ({FFMPEG}) — install it or set FFMPEG_PATH")
    return problems
