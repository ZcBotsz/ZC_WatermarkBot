"""SQLite persistence (zero setup): per-user settings, bans, allow-list, stats."""
from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite

from . import config
from .settings import WMSettings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, name TEXT, settings TEXT DEFAULT '{}',
    banned INTEGER DEFAULT 0, allowed INTEGER DEFAULT 0, created REAL, last_seen REAL,
    jobs INTEGER DEFAULT 0, bytes_in INTEGER DEFAULT 0,
    plan TEXT DEFAULT 'free', plan_expires REAL DEFAULT NULL);
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, title TEXT, added REAL);
CREATE TABLE IF NOT EXISTS daily_usage(
    user_id INTEGER NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, day));
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, name TEXT, size_in INTEGER, size_out INTEGER,
    secs REAL, ok INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS channel_automation(
    owner_user_id INTEGER PRIMARY KEY,
    source_chat_id INTEGER, source_title TEXT DEFAULT '',
    target_chat_id INTEGER, target_title TEXT DEFAULT '',
    running INTEGER NOT NULL DEFAULT 0,
    backfill_mode INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS automation_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL DEFAULT 0,
    source_chat_id INTEGER NOT NULL, source_message_id INTEGER NOT NULL,
    origin_chat_id INTEGER NOT NULL, origin_message_id INTEGER NOT NULL,
    target_chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL, file_name TEXT NOT NULL DEFAULT '', file_size INTEGER NOT NULL DEFAULT 0,
    file_id TEXT NOT NULL DEFAULT '', as_document INTEGER NOT NULL DEFAULT 0,
    caption_html TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(owner_user_id, source_chat_id, source_message_id));
CREATE INDEX IF NOT EXISTS idx_automation_items_status_id ON automation_items(status, id);
"""


class DB:
    def __init__(self):
        self.conn: aiosqlite.Connection | None = None
        self._cache: dict[int, WMSettings] = {}
        self._banned: set[int] = set()
        self._allowed: set[int] = set()
        self._chats: set[int] = set()

    async def start(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(config.DB_PATH)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(_SCHEMA)
        async with self.conn.execute("PRAGMA table_info(users)") as cur:
            columns = {r["name"] async for r in cur}
        if "plan" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")
        if "plan_expires" not in columns:
            await self.conn.execute("ALTER TABLE users ADD COLUMN plan_expires REAL DEFAULT NULL")
        await self._migrate_automation_tables()
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

    # ── source-to-target channel automation (one independent setup per user) ─────────────
    async def _table_columns(self, table: str) -> set[str]:
        async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
            return {r["name"] async for r in cur}

    async def _table_sql(self, table: str) -> str:
        async with self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)) as cur:
            row = await cur.fetchone()
        return (row["sql"] or "") if row else ""

    async def _migrate_automation_tables(self):
        """Upgrade the old single (owner-only) automation tables to per-user rows.

        Existing channel choices and queued items are preserved and attributed to
        OWNER_ID. Safe to run on every startup; new-schema databases skip instantly.
        """
        owner = int(config.OWNER_ID or 0)
        cfg_sql = (await self._table_sql("channel_automation")).upper()
        if "OWNER_USER_ID" not in cfg_sql:
            await self.conn.execute(
                "CREATE TABLE IF NOT EXISTS channel_automation_new(owner_user_id INTEGER PRIMARY KEY,"
                " source_chat_id INTEGER, source_title TEXT DEFAULT '', target_chat_id INTEGER,"
                " target_title TEXT DEFAULT '', running INTEGER NOT NULL DEFAULT 0,"
                " backfill_mode INTEGER NOT NULL DEFAULT 0)")
            await self.conn.execute(
                "INSERT OR IGNORE INTO channel_automation_new(owner_user_id,source_chat_id,source_title,"
                "target_chat_id,target_title,running,backfill_mode) "
                "SELECT ?, source_chat_id, COALESCE(source_title,''), target_chat_id, COALESCE(target_title,''),"
                " running, backfill_mode FROM channel_automation",
                (owner,))
            await self.conn.execute("DROP TABLE channel_automation")
            await self.conn.execute("ALTER TABLE channel_automation_new RENAME TO channel_automation")
        item_cols = await self._table_columns("automation_items")
        if item_cols and "owner_user_id" not in item_cols:
            await self.conn.execute("ALTER TABLE automation_items ADD COLUMN owner_user_id INTEGER NOT NULL DEFAULT 0")
            await self.conn.execute("UPDATE automation_items SET owner_user_id=? WHERE owner_user_id=0", (owner,))
        item_sql = (await self._table_sql("automation_items")).replace(" ", "").upper()
        if "UNIQUE(OWNER_USER_ID" not in item_sql:
            cols = ("id,owner_user_id,source_chat_id,source_message_id,origin_chat_id,origin_message_id,"
                    "target_chat_id,kind,file_name,file_size,file_id,as_document,caption_html,status,error,"
                    "attempts,added_at,updated_at")
            await self.conn.execute(
                "CREATE TABLE automation_items_new(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " owner_user_id INTEGER NOT NULL DEFAULT 0, source_chat_id INTEGER NOT NULL,"
                " source_message_id INTEGER NOT NULL, origin_chat_id INTEGER NOT NULL,"
                " origin_message_id INTEGER NOT NULL, target_chat_id INTEGER NOT NULL,"
                " kind TEXT NOT NULL, file_name TEXT NOT NULL DEFAULT '', file_size INTEGER NOT NULL DEFAULT 0,"
                " file_id TEXT NOT NULL DEFAULT '', as_document INTEGER NOT NULL DEFAULT 0,"
                " caption_html TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',"
                " error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,"
                " added_at REAL NOT NULL, updated_at REAL NOT NULL,"
                " UNIQUE(owner_user_id, source_chat_id, source_message_id))")
            await self.conn.execute(
                f"INSERT OR IGNORE INTO automation_items_new({cols}) SELECT {cols} FROM automation_items")
            await self.conn.execute("DROP TABLE automation_items")
            await self.conn.execute("ALTER TABLE automation_items_new RENAME TO automation_items")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_items_status_id ON automation_items(status, id)")
        await self.conn.commit()

    @staticmethod
    def _automation_owner(owner_user_id: int | None) -> int:
        return int(owner_user_id) if owner_user_id else int(config.OWNER_ID or 0)

    async def automation_config(self, owner_user_id: int | None = None) -> dict:
        owner = self._automation_owner(owner_user_id)
        async with self.conn.execute("SELECT * FROM channel_automation WHERE owner_user_id=?", (owner,)) as cur:
            row = await cur.fetchone()
        if row is None:
            await self.conn.execute("INSERT OR IGNORE INTO channel_automation(owner_user_id) VALUES(?)", (owner,))
            await self.conn.commit()
            return {"owner_user_id": owner, "source_chat_id": None, "source_title": "",
                    "target_chat_id": None, "target_title": "", "running": 0, "backfill_mode": 0}
        return dict(row)

    async def automation_configs(self) -> list[dict]:
        """Every user's automation setup (used to route channel posts and run the worker)."""
        async with self.conn.execute("SELECT * FROM channel_automation") as cur:
            return [dict(row) async for row in cur]

    async def set_automation_config(self, owner_user_id: int | None = None, **values):
        allowed = {"source_chat_id", "source_title", "target_chat_id", "target_title", "running", "backfill_mode"}
        if not values or values.keys() - allowed:
            raise ValueError("Invalid channel automation setting")
        owner = self._automation_owner(owner_user_id)
        await self.automation_config(owner)
        columns = ", ".join(f"{key}=?" for key in values)
        await self.conn.execute(f"UPDATE channel_automation SET {columns} WHERE owner_user_id=?",
                                (*values.values(), owner))
        await self.conn.commit()

    async def enqueue_automation_item(self, *, owner_user_id: int | None = None, source_chat_id: int, source_message_id: int,
                                     origin_chat_id: int, origin_message_id: int, target_chat_id: int,
                                     kind: str, file_name: str, file_size: int, caption_html: str,
                                     file_id: str = "", as_document: bool = False) -> bool:
        now = time.time()
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO automation_items(owner_user_id,source_chat_id,source_message_id,origin_chat_id,origin_message_id,"
            "target_chat_id,kind,file_name,file_size,file_id,as_document,caption_html,status,added_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)",
            (self._automation_owner(owner_user_id), source_chat_id, source_message_id, origin_chat_id, origin_message_id, target_chat_id, kind,
             file_name, max(0, int(file_size or 0)), file_id or "", int(bool(as_document)), caption_html or "", now, now))
        inserted = cur.rowcount == 1
        await cur.close()
        await self.conn.commit()
        return inserted

    async def claim_automation_item(self, owner_user_id: int | None = None) -> dict | None:
        """Claim one user's oldest queued item. A single automation worker calls this method."""
        if owner_user_id:
            query = "SELECT * FROM automation_items WHERE status='queued' AND owner_user_id=? ORDER BY source_message_id, id LIMIT 1"
            params: tuple = (int(owner_user_id),)
        else:
            query = "SELECT * FROM automation_items WHERE status='queued' ORDER BY source_message_id, id LIMIT 1"
            params = ()
        async with self.conn.execute(query, params) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        now = time.time()
        await self.conn.execute("UPDATE automation_items SET status='processing', attempts=attempts+1, updated_at=? WHERE id=? AND status='queued'",
                                (now, row["id"]))
        await self.conn.commit()
        async with self.conn.execute("SELECT * FROM automation_items WHERE id=?", (row["id"],)) as cur:
            claimed = await cur.fetchone()
        return dict(claimed) if claimed else None

    async def finish_automation_item(self, item_id: int, status: str, error: str = ""):
        if status not in {"done", "failed", "skipped", "queued"}:
            raise ValueError("Invalid automation item status")
        await self.conn.execute("UPDATE automation_items SET status=?, error=?, updated_at=? WHERE id=?",
                                (status, error[:700], time.time(), item_id))
        await self.conn.commit()

    async def automation_counts(self, owner_user_id: int | None = None) -> dict[str, int]:
        counts = {key: 0 for key in ("queued", "processing", "done", "failed", "skipped")}
        if owner_user_id:
            query = "SELECT status, COUNT(*) AS n FROM automation_items WHERE owner_user_id=? GROUP BY status"
            params: tuple = (int(owner_user_id),)
        else:
            query = "SELECT status, COUNT(*) AS n FROM automation_items GROUP BY status"
            params = ()
        async with self.conn.execute(query, params) as cur:
            async for row in cur:
                counts[row["status"]] = row["n"]
        return counts

    async def recent_automation_items(self, limit: int = 8, owner_user_id: int | None = None) -> list[dict]:
        if owner_user_id:
            query = "SELECT * FROM automation_items WHERE owner_user_id=? ORDER BY id DESC LIMIT ?"
            params: tuple = (int(owner_user_id), max(1, min(20, int(limit))))
        else:
            query = "SELECT * FROM automation_items ORDER BY id DESC LIMIT ?"
            params = (max(1, min(20, int(limit))),)
        async with self.conn.execute(query, params) as cur:
            return [dict(row) async for row in cur]

    async def retry_automation_failed(self, owner_user_id: int | None = None) -> int:
        if owner_user_id:
            cur = await self.conn.execute("UPDATE automation_items SET status='queued', error='', updated_at=? WHERE status='failed' AND owner_user_id=?",
                                          (time.time(), int(owner_user_id)))
        else:
            cur = await self.conn.execute("UPDATE automation_items SET status='queued', error='', updated_at=? WHERE status='failed'",
                                          (time.time(),))
        count = cur.rowcount
        await cur.close()
        await self.conn.commit()
        return count

    async def skip_automation_failed(self, owner_user_id: int | None = None) -> int:
        if owner_user_id:
            cur = await self.conn.execute("UPDATE automation_items SET status='skipped', updated_at=? WHERE status='failed' AND owner_user_id=?",
                                          (time.time(), int(owner_user_id)))
        else:
            cur = await self.conn.execute("UPDATE automation_items SET status='skipped', updated_at=? WHERE status='failed'",
                                          (time.time(),))
        count = cur.rowcount
        await cur.close()
        await self.conn.commit()
        return count

    async def recover_automation_items(self):
        """Requeue work interrupted by a process restart; completed items stay deduplicated."""
        await self.conn.execute("UPDATE automation_items SET status='queued', error='Interrupted by restart', updated_at=? WHERE status='processing'",
                                (time.time(),))
        await self.conn.commit()

    # ── plans and daily usage ───────────────────────────────────────────────────────────
    @staticmethod
    def _today_key() -> str:
        return datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()

    async def plan_state(self, user_id: int) -> tuple[str, float | None]:
        async with self.conn.execute("SELECT plan, plan_expires FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return "free", None
        plan = row["plan"] or "free"
        expires = row["plan_expires"]
        if plan != "free" and expires is not None and expires <= time.time():
            await self.conn.execute("UPDATE users SET plan='free', plan_expires=NULL WHERE user_id=?", (user_id,))
            await self.conn.commit()
            return "free", None
        return plan, expires

    async def set_plan(self, user_id: int, plan: str, days: int | None = 30) -> float | None:
        from .plans import PLANS
        plan = plan.strip().lower()
        if plan not in PLANS:
            raise ValueError("Unknown plan")
        now = time.time()
        expires = None
        if plan != "free" and days is not None and days > 0:
            current_plan, current_expiry = await self.plan_state(user_id)
            base = current_expiry if current_plan == plan and current_expiry and current_expiry > now else now
            expires = base + days * 86400
        await self.conn.execute(
            "INSERT INTO users(user_id,created,last_seen,plan,plan_expires) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, plan_expires=excluded.plan_expires",
            (user_id, now, now, plan, expires))
        await self.conn.commit()
        return expires

    async def daily_used(self, user_id: int, day: str | None = None) -> int:
        day = day or self._today_key()
        async with self.conn.execute("SELECT used FROM daily_usage WHERE user_id=? AND day=?", (user_id, day)) as cur:
            row = await cur.fetchone()
        return int(row["used"]) if row else 0

    async def reserve_daily_job(self, user_id: int, limit: int | None) -> tuple[bool, str, int]:
        """Atomically reserve one daily job; used to make the public bot's quotas race-safe."""
        day = self._today_key()
        await self.conn.execute("INSERT OR IGNORE INTO daily_usage(user_id,day,used) VALUES(?,?,0)", (user_id, day))
        if limit is None:
            cur = await self.conn.execute("UPDATE daily_usage SET used=used+1 WHERE user_id=? AND day=?", (user_id, day))
        else:
            cur = await self.conn.execute(
                "UPDATE daily_usage SET used=used+1 WHERE user_id=? AND day=? AND used<?",
                (user_id, day, max(0, int(limit))))
        reserved = cur.rowcount == 1
        await cur.close()
        await self.conn.commit()
        return reserved, day, await self.daily_used(user_id, day)

    async def refund_daily_job(self, user_id: int, day: str):
        await self.conn.execute(
            "UPDATE daily_usage SET used=MAX(0,used-1) WHERE user_id=? AND day=?", (user_id, day))
        await self.conn.commit()

    async def user_profile(self, user_id: int) -> dict | None:
        async with self.conn.execute(
            "SELECT user_id,name,created,last_seen,jobs,bytes_in,banned,allowed,plan,plan_expires FROM users WHERE user_id=?",
            (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        plan, expires = await self.plan_state(user_id)
        result = dict(row)
        result["plan"], result["plan_expires"] = plan, expires
        result["daily_used"] = await self.daily_used(user_id)
        return result

    async def recent_users(self, limit: int = 20) -> list[dict]:
        async with self.conn.execute(
            "SELECT user_id,name,last_seen,jobs,plan,plan_expires FROM users ORDER BY last_seen DESC LIMIT ?",
            (max(1, min(100, int(limit))),)) as cur:
            return [dict(r) async for r in cur]

    # ── stats ───────────────────────────────────────────────────────────────────────────
    async def record_job(self, user_id: int, kind: str, name: str, size_in: int, size_out: int, secs: float, ok: bool):
        await self.conn.execute("INSERT INTO jobs(user_id,kind,name,size_in,size_out,secs,ok,ts) VALUES(?,?,?,?,?,?,?,?)",
                                (user_id, kind, name[:120], size_in, size_out, secs, int(ok), time.time()))
        await self.conn.execute("UPDATE users SET jobs=jobs+1, bytes_in=bytes_in+? WHERE user_id=?", (size_in, user_id))
        await self.conn.commit()

    async def stats(self) -> dict:
        out = {}
        now, day = time.time(), self._today_key()
        async with self.conn.execute("SELECT COUNT(*) c FROM users") as cur:
            out["users"] = (await cur.fetchone())["c"]
        async with self.conn.execute("SELECT COUNT(*) c FROM users WHERE last_seen>=?", (now - 86400,)) as cur:
            out["active_24h"] = (await cur.fetchone())["c"]
        async with self.conn.execute("SELECT COALESCE(SUM(used),0) c FROM daily_usage WHERE day=?", (day,)) as cur:
            out["jobs_reserved_today"] = (await cur.fetchone())["c"]
        async with self.conn.execute(
                "SELECT plan, COUNT(*) c FROM users WHERE plan!='free' AND (plan_expires IS NULL OR plan_expires>?) GROUP BY plan",
                (now,)) as cur:
            out["plans"] = {r["plan"]: r["c"] async for r in cur}
        async with self.conn.execute(
                "SELECT kind, COUNT(*) c, SUM(ok) ok, SUM(size_in) sin, SUM(size_out) sout, AVG(secs) avg FROM jobs GROUP BY kind") as cur:
            out["kinds"] = [dict(r) async for r in cur]
        async with self.conn.execute("SELECT COUNT(*) c, COALESCE(SUM(ok),0) ok FROM jobs") as cur:
            row = await cur.fetchone()
            out["jobs_total"], out["jobs_success"] = row["c"], row["ok"]
        return out


db = DB()
