import json
import asyncio
from pathlib import Path
from typing import Dict, Any
from .config import Config

class UserDB:
    def __init__(self):
        self.base = Path(f"{Config.TEMP_DIR}/users")
        self.base.mkdir(parents=True, exist_ok=True)
        self.locks: Dict[int, asyncio.Lock] = {}
        self.cache: Dict[int, Dict[str, Any]] = {}

    def _lock(self, uid: int):
        if uid not in self.locks:
            self.locks[uid] = asyncio.Lock()
        return self.locks[uid]

    def _path(self, uid: int):
        return self.base / f"{uid}.json"

    def defaults(self) -> Dict[str, Any]:
        return {
            # text
            "text": Config.DEFAULT_TEXT,
            "mode": Config.DEFAULT_MODE,
            "position": Config.DEFAULT_POSITION,
            # visual
            "opacity": 0.8,
            "color": "white",
            "font_size": 36,
            "shadow": True,
            # background
            "bg_enabled": True,
            "bg_color": "black",
            "bg_opacity": 0.5,
            # border
            "border": True,
            "border_width": 3,
            "border_color": "black",
            # quality
            "crf": Config.DEFAULT_CRF,
            "preset": Config.DEFAULT_PRESET,
            "resolution": "original",
            "codec": "h264",
            # motion
            "speed": 2.0,
            # logo
            "logo_path": None,
            "logo_size": 15,
            "logo_opacity": 0.8,
            "logo_position": "top_left",
            # pdf
            "pdf_mode": "diagonal",
            "pdf_opacity": 0.15,
            "pdf_font_size": 32,
            "pdf_rotation": 45,
            "pdf_color": "red",
            "pdf_bg_opacity": 0.1,
        }

    async def get(self, uid: int) -> Dict[str, Any]:
        if uid in self.cache:
            return self.cache[uid]
        p = self._path(uid)
        if p.exists():
            try:
                data = json.loads(p.read_text())
                merged = self.defaults()
                merged.update(data)
                self.cache[uid] = merged
                return merged
            except:
                pass
        cfg = self.defaults()
        self.cache[uid] = cfg
        return cfg

    async def update(self, uid: int, **kw) -> Dict[str, Any]:
        async with self._lock(uid):
            cfg = await self.get(uid)
            cfg.update(kw)
            self.cache[uid] = cfg
            try:
                self._path(uid).write_text(json.dumps(cfg, indent=2))
            except Exception as e:
                print(f"DB save error: {e}")
            return cfg

    async def reset(self, uid: int):
        async with self._lock(uid):
            cfg = self.defaults()
            self.cache[uid] = cfg
            p = self._path(uid)
            if p.exists():
                p.unlink()
            # also delete logo if exists
            logo = Path(f"{Config.TEMP_DIR}/users/{uid}/logo.png")
            if logo.exists():
                logo.unlink()
            return cfg

db = UserDB()
