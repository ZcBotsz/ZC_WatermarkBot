import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "0"))
    TEMP_DIR = os.getenv("TEMP_DIR", "./temp")
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./downloads")
    WORKER_COUNT = int(os.getenv("WORKER_COUNT", "3"))
    MAX_QUEUE = int(os.getenv("MAX_QUEUE", "50"))
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))

    DEFAULT_TEXT = os.getenv("DEFAULT_TEXT", "@MyChannel • Join us\nनमस्ते दोस्तों")
    DEFAULT_MODE = os.getenv("DEFAULT_MODE", "bouncing")
    DEFAULT_POSITION = os.getenv("DEFAULT_POSITION", "bottom_right")
    DEFAULT_PRESET = os.getenv("DEFAULT_PRESET", "ultrafast")
    DEFAULT_CRF = int(os.getenv("DEFAULT_CRF", "23"))

    @classmethod
    def init(cls):
        Path(cls.TEMP_DIR).mkdir(parents=True, exist_ok=True)
        Path(cls.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
        Path(f"{cls.TEMP_DIR}/users").mkdir(parents=True, exist_ok=True)
        Path(f"{cls.TEMP_DIR}/jobs").mkdir(parents=True, exist_ok=True)
        Path("./assets").mkdir(exist_ok=True)

class Modes:
    STATIC = "static"
    BOUNCING = "bouncing"
    FLOATING = "floating"
    SCROLLING = "scrolling"
    DIAGONAL = "diagonal"
    TILED = "tiled"
    TRANSPARENT = "transparent"
    CINEMATIC = "cinematic"

    ALL = [STATIC, BOUNCING, FLOATING, SCROLLING, DIAGONAL, TILED, TRANSPARENT, CINEMATIC]
    DESC = {
        STATIC: "Fixed corner with bg box",
        BOUNCING: "DVD logo bounce - anti-removal best",
        FLOATING: "Sine wave float",
        SCROLLING: "News ticker right to left",
        DIAGONAL: "Diagonal moving 45°",
        TILED: "Repeating grid pattern",
        TRANSPARENT: "Ghost low opacity",
        CINEMATIC: "Letterbox top/bottom bars"
    }

class Positions:
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    CENTER = "center"
    ALL = [TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT, CENTER]

    @staticmethod
    def ffmpeg_pos(pos):
        m = {
            "top_left": ("5", "5"),
            "top_right": ("w-text_w-5", "5"),
            "bottom_left": ("5", "h-text_h-5"),
            "bottom_right": ("w-text_w-5", "h-text_h-5"),
            "center": ("(w-text_w)/2", "(h-text_h)/2")
        }
        return m.get(pos, m["bottom_right"])

    @staticmethod
    def overlay_pos(pos):
        m = {
            "top_left": "5:5",
            "top_right": "main_w-overlay_w-5:5",
            "bottom_left": "5:main_h-overlay_h-5",
            "bottom_right": "main_w-overlay_w-5:main_h-overlay_h-5",
            "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2"
        }
        return m.get(pos, m["bottom_right"])

class Quality:
    PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
    PRESET_DESC = {
        "ultrafast": "Fastest, bigger size - 14s for 1080p",
        "superfast": "Very fast",
        "veryfast": "Fast",
        "faster": "Faster than fast",
        "fast": "Fast",
        "medium": "Balanced - 48s for 1080p",
        "slow": "Better compression",
        "slower": "Best compression",
        "veryslow": "Slowest, smallest size - 3m20s"
    }
    CRF_LIST = [18, 20, 23, 26, 28, 30]
    CRF_DESC = {
        18: "High quality, big size",
        20: "High quality",
        23: "Default balanced",
        26: "Medium, smaller",
        28: "Low quality, small size",
        30: "Lowest, tiny size"
    }
    RESOLUTIONS = ["original", "1080p", "720p", "540p", "480p", "360p"]
    CODECS = ["h264", "h265"]
    COLORS = ["white", "black", "red", "blue", "green", "yellow", "cyan", "magenta", "orange"]
