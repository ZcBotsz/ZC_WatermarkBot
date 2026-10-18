"""SQLite persistence (zero setup): per-user settings, bans, allow-list, stats."""
from __future__ import annotations

import json
import time

import aiosqlite
from PIL import Image

from . import config
from .render import load_logo
from .settings import WMSettings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, name TEXT, settings TEXT DEFAULT '{}', has_logo INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0, allowed INTEGER DEFAULT 0, created REAL, last_seen REAL,
    jobs INTEGER DEFAULT 0, bytes_in INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, title TEXT, added REAL);
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, name TEXT, size_in INTEGER, size_out INTEGER,
    secs REAL, ok INTEGER, ts REAL);
"""


class DB:
    def __init__(self):
        self.conn: aiosqlite.Connection | None = None
        self._cache: dict[int, WMSettings] = {}
        self._logo_cache: dict[int, tuple[float, Image.Image]] = {}
        self._banned: set[int] = set()
        self._allowed: set[int] = set()
        self._chats: set[int] = set()

    async def start(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.LOGO_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(config.DB_PATH)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(_SCHEMA)
        await self.conn.commit()
        async with self.conn.execute("SELECT user_id, banned, allowed FROM users WHERE banned=1 OR allowed=1") as cur:
            async for r in cur:
                (self._banned if r["banned"] else self._allowed).add(r["user_id"])
        async with self.conn.execute("SELECT chat_id FROM chats") as cur:
            self._chats = {r["chat_id"] async for r in cur}

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ── users / settings ────────────────────────────────────────────────────────────────
    async def touch_user(self, user_id: int, name: str = ""):
        now = time.time()
        await self.conn.execute(
            "INSERT INTO users(user_id,name,created,last_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen, name=excluded.name",
            (user_id, name, now, now))
        await self.conn.commit()

    async def get_settings(self, user_id: int) -> WMSettings:
        if user_id in self._cache:
            return self._cache[user_id]
        async with self.conn.execute("SELECT settings FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        s = WMSettings.from_dict(json.loads(row["settings"]) if row and row["settings"] else {})
        self._cache[user_id] = s
        return s

    async def save_settings(self, user_id: int, s: WMSettings):
        self._cache[user_id] = s
        await self.conn.execute(
            "INSERT INTO users(user_id,settings,created,last_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings",
            (user_id, json.dumps(s.to_dict()), time.time(), time.time()))
        await self.conn.commit()

    # ── logo ────────────────────────────────────────────────────────────────────────────
    def logo_path(self, user_id: int):
        return config.LOGO_DIR / f"{user_id}.png"

    def get_logo(self, user_id: int) -> Image.Image | None:
        p = self.logo_path(user_id)
        if not p.exists():
            return None
        mt = p.stat().st_mtime
        c = self._logo_cache.get(user_id)
        if c and c[0] == mt:
            return c[1]
        im = load_logo(p)
        if im is not None:
            self._logo_cache[user_id] = (mt, im)
        return im

    def delete_logo(self, user_id: int):
        self.logo_path(user_id).unlink(missing_ok=True)
        self._logo_cache.pop(user_id, None)

    # ── access control ──────────────────────────────────────────────────────────────────
    def is_banned(self, uid: int) -> bool:
        return uid in self._banned

    def is_allowed_user(self, uid: int) -> bool:
        return uid in config.ADMINS or uid in config.ALLOWED_USERS or uid in self._allowed

    def is_auth_chat(self, cid: int) -> bool:
        return cid in config.AUTH_CHATS or cid in self._chats

    async def set_flag(self, uid: int, flag: str, value: bool):
        assert flag in ("banned", "allowed")
        await self.conn.execute(
            f"INSERT INTO users(user_id,{flag},created) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET {flag}=excluded.{flag}",
            (uid, int(value), time.time()))
        await self.conn.commit()
        target = self._banned if flag == "banned" else self._allowed
        (target.add if value else target.discard)(uid)

    async def set_chat(self, cid: int, title: str, value: bool):
        if value:
            await self.conn.execute("INSERT OR REPLACE INTO chats VALUES(?,?,?)", (cid, title, time.time()))
            self._chats.add(cid)
        else:
            await self.conn.execute("DELETE FROM chats WHERE chat_id=?", (cid,))
            self._chats.discard(cid)
        await self.conn.commit()

    async def all_user_ids(self) -> list[int]:
        async with self.conn.execute("SELECT user_id FROM users WHERE banned=0") as cur:
            return [r["user_id"] async for r in cur]

    # ── stats ───────────────────────────────────────────────────────────────────────────
    async def record_job(self, user_id: int, kind: str, name: str, size_in: int, size_out: int, secs: float, ok: bool):
        await self.conn.execute("INSERT INTO jobs(user_id,kind,name,size_in,size_out,secs,ok,ts) VALUES(?,?,?,?,?,?,?,?)",
                                (user_id, kind, name[:120], size_in, size_out, secs, int(ok), time.time()))
        await self.conn.execute("UPDATE users SET jobs=jobs+1, bytes_in=bytes_in+? WHERE user_id=?", (size_in, user_id))
        await self.conn.commit()

    async def stats(self) -> dict:
        out = {}
        async with self.conn.execute("SELECT COUNT(*) c FROM users") as cur:
            out["users"] = (await cur.fetchone())["c"]
        async with self.conn.execute(
                "SELECT kind, COUNT(*) c, SUM(ok) ok, SUM(size_in) sin, SUM(size_out) sout, AVG(secs) avg FROM jobs GROUP BY kind") as cur:
            out["kinds"] = [dict(r) async for r in cur]
        return out


db = DB()
