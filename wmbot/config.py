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


# ── Telegram credentials (https://my.telegram.org  +  @BotFather) ────────────────────────
API_ID = _int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION_NAME = os.getenv("SESSION_NAME", "wmbot")

# ── Access control ───────────────────────────────────────────────────────────────────────
OWNER_ID = _int("OWNER_ID", 0)
ADMINS = _ids("ADMINS") | ({OWNER_ID} if OWNER_ID else set())
ALLOWED_USERS = _ids("ALLOWED_USERS")        # extra users allowed when PUBLIC_MODE=false
AUTH_CHATS = _ids("AUTH_CHATS")              # groups where the bot may be used
PUBLIC_MODE = _bool("PUBLIC_MODE", False)    # true = anyone may use the bot in private chat

# ── Paths ────────────────────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
WORK_DIR = Path(os.getenv("WORK_DIR", ROOT / "downloads"))
FONT_DIRS = [ROOT / "assets" / "fonts", DATA_DIR / "fonts", Path("/usr/share/fonts"),
             Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
DB_PATH = DATA_DIR / "bot.db"
LOGO_DIR = DATA_DIR / "logos"

# ── Binaries ─────────────────────────────────────────────────────────────────────────────
FFMPEG = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

# ── Performance knobs ────────────────────────────────────────────────────────────────────
CPU_COUNT = os.cpu_count() or 2
# Number of videos encoded at the same time. Each encode uses CPU_COUNT/ENCODE_SLOTS threads.
ENCODE_SLOTS = max(1, _int("ENCODE_SLOTS", 1 if CPU_COUNT <= 4 else 2))
DOWNLOAD_SLOTS = max(1, _int("DOWNLOAD_SLOTS", 3))
UPLOAD_SLOTS = max(1, _int("UPLOAD_SLOTS", 3))
LIGHT_SLOTS = max(1, _int("LIGHT_SLOTS", 3))     # PDFs & photos (cheap, run beside video encodes)
# Telegram transfer parallelism (pyrogram). Higher = faster big files; 8 is a good VPS default.
MAX_CONCURRENT_TRANSMISSIONS = _int("MAX_CONCURRENT_TRANSMISSIONS", 8)
TG_WORKERS = _int("TG_WORKERS", 16)

# ── Limits ───────────────────────────────────────────────────────────────────────────────
MAX_FILE_MB = _int("MAX_FILE_MB", 2000)          # bots can upload up to 2000 MB over MTProto
MAX_QUEUE_PER_USER = _int("MAX_QUEUE_PER_USER", 5)
MAX_PDF_PAGES = _int("MAX_PDF_PAGES", 2000)
ENCODE_TIMEOUT_SEC = _int("ENCODE_TIMEOUT_SEC", 4 * 3600)
PROGRESS_EDIT_INTERVAL = float(os.getenv("PROGRESS_EDIT_INTERVAL", "4"))

# ── Branding ─────────────────────────────────────────────────────────────────────────────
DEFAULT_WATERMARK_TEXT = os.getenv("DEFAULT_WATERMARK_TEXT", "@YourChannel")
BOT_NAME = os.getenv("BOT_NAME", "Watermark Bot")


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
