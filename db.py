"""
Async SQLite layer.

Tables:
    users        — user_id, balance_kop (kopecks), free_renders, lang, created_at
    configs      — saved render presets per user
    conversions  — completed render history (for stats)
    payments     — payment requests (cryptobot/yoomoney/stars)
    settings     — k/v store for runtime-editable values (price, free renders)
    media        — admin-uploaded GIFs/photos for menu screens, by key

Money is stored as integer kopecks to avoid float drift.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from utils.logger import get_logger

log = get_logger("db")


class DB:
    """Thin async wrapper around aiosqlite. One persistent connection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DB not opened")
        return self._conn

    async def _migrate(self) -> None:
        c = self.conn
        await c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            balance_kop  INTEGER NOT NULL DEFAULT 0,
            free_renders INTEGER NOT NULL DEFAULT 0,
            total_renders INTEGER NOT NULL DEFAULT 0,
            total_spent_kop INTEGER NOT NULL DEFAULT 0,
            referrer_id  INTEGER,
            referrals_count INTEGER NOT NULL DEFAULT 0,
            created_at   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS configs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            name      TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS configs_user ON configs(user_id);

        CREATE TABLE IF NOT EXISTS conversions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            format    TEXT NOT NULL,
            cost_kop  INTEGER NOT NULL,
            paid_with TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS conv_user ON conversions(user_id);
        CREATE INDEX IF NOT EXISTS conv_created ON conversions(created_at);

        CREATE TABLE IF NOT EXISTS payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            provider   TEXT NOT NULL,
            amount_kop INTEGER NOT NULL,
            status     TEXT NOT NULL,
            external_id TEXT,
            extra_json TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS pay_user ON payments(user_id);
        CREATE INDEX IF NOT EXISTS pay_status ON payments(status);
        CREATE INDEX IF NOT EXISTS pay_external ON payments(external_id);

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS media (
            screen_key  TEXT PRIMARY KEY,
            file_id     TEXT NOT NULL,
            kind        TEXT NOT NULL,
            updated_at  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS content_overrides (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """)
        # Idempotent migrations for databases created before these columns existed.
        cur = await c.execute("PRAGMA table_info(users)")
        cols = {r["name"] for r in await cur.fetchall()}
        if "referrer_id" not in cols:
            await c.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
        if "referrals_count" not in cols:
            await c.execute(
                "ALTER TABLE users ADD COLUMN referrals_count INTEGER NOT NULL DEFAULT 0"
            )
        await c.commit()
        log.info("db migrated: %s", self.path)

    async def upsert_user(
        self,
        user_id: int,
        username: str | None,
        default_free: int,
        starter_balance_kop: int = 3000,
    ) -> tuple[bool, dict]:
        """Returns (created, user_row_as_dict). Thread-safe via INSERT OR IGNORE."""
        c = self.conn
        now = int(time.time())

        cur = await c.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, username, balance_kop, free_renders, "
            "total_renders, total_spent_kop, created_at) "
            "VALUES (?, ?, ?, 0, 0, 0, ?)",
            (user_id, username, starter_balance_kop, now),
        )
        await c.commit()
        created = (cur.rowcount or 0) > 0

        # Touch username if changed (only on existing users)
        if not created and username:
            await c.execute(
                "UPDATE users SET username=? WHERE user_id=? AND (username IS NULL OR username!=?)",
                (username, user_id, username),
            )
            await c.commit()

        cur2 = await c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur2.fetchone()
        return created, dict(row)  # type: ignore[arg-type]

    async def get_user(self, user_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def add_balance_kop(self, user_id: int, delta_kop: int) -> int:
        await self.conn.execute(
            "UPDATE users SET balance_kop = balance_kop + ? WHERE user_id=?",
            (delta_kop, user_id),
        )
        await self.conn.commit()
        u = await self.get_user(user_id)
        return u["balance_kop"] if u else 0

    async def add_free_renders(self, user_id: int, delta: int) -> int:
        await self.conn.execute(
            "UPDATE users SET free_renders = MAX(0, free_renders + ?) WHERE user_id=?",
            (delta, user_id),
        )
        await self.conn.commit()
        u = await self.get_user(user_id)
        return u["free_renders"] if u else 0

    async def grant_to_all(self, free: int = 0, balance_kop: int = 0) -> int:
        """Returns affected row count."""
        c = self.conn
        cur = await c.execute(
            "UPDATE users SET free_renders = free_renders + ?, balance_kop = balance_kop + ?",
            (free, balance_kop),
        )
        await c.commit()
        return cur.rowcount or 0

    async def all_user_ids(self) -> list[int]:
        cur = await self.conn.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def set_referrer(self, user_id: int, referrer_id: int) -> bool:
        """Set referrer for a user IF not yet set and ref != self.
        Returns True if set (i.e. credit-eligible event)."""
        if user_id == referrer_id:
            return False
        c = self.conn
        cur = await c.execute(
            "UPDATE users SET referrer_id=? WHERE user_id=? AND referrer_id IS NULL",
            (referrer_id, user_id),
        )
        await c.commit()
        if (cur.rowcount or 0) == 0:
            return False
        await c.execute(
            "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?",
            (referrer_id,),
        )
        await c.commit()
        return True

    async def get_referrals_count(self, user_id: int) -> int:
        u = await self.get_user(user_id)
        if not u:
            return 0
        return int(u.get("referrals_count") or 0)

    async def consume_render(self, user_id: int, price_kop: int) -> tuple[bool, str]:
        """Atomically deduct one render. Returns (ok, paid_with).
        paid_with: 'free' | 'balance' | '' (failed)
        """
        c = self.conn
        u = await self.get_user(user_id)
        if u is None:
            return False, ""
        if u["free_renders"] > 0:
            await c.execute(
                "UPDATE users SET free_renders = free_renders - 1, "
                "total_renders = total_renders + 1 WHERE user_id=?",
                (user_id,),
            )
            await c.commit()
            return True, "free"
        if u["balance_kop"] >= price_kop:
            await c.execute(
                "UPDATE users SET balance_kop = balance_kop - ?, "
                "total_renders = total_renders + 1, "
                "total_spent_kop = total_spent_kop + ? WHERE user_id=?",
                (price_kop, price_kop, user_id),
            )
            await c.commit()
            return True, "balance"
        return False, ""

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        cur = await self.conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def get_int_setting(self, key: str, default: int) -> int:
        v = await self.get_setting(key)
        if v is None:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    async def list_configs(self, user_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM configs WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_config(self, config_id: int, user_id: int) -> dict | None:
        cur = await self.conn.execute(
            "SELECT * FROM configs WHERE id=? AND user_id=?",
            (config_id, user_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_config_by_id(self, config_id: int) -> dict | None:
        """Get config by id regardless of owner — for sharing."""
        cur = await self.conn.execute(
            "SELECT * FROM configs WHERE id=?",
            (config_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def save_config(self, user_id: int, name: str, data: dict) -> int:
        cur = await self.conn.execute(
            "INSERT INTO configs(user_id, name, data_json, created_at) VALUES(?, ?, ?, ?)",
            (user_id, name, json.dumps(data, ensure_ascii=False), int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid or 0

    async def update_config_data(
        self,
        config_id: int,
        user_id: int,
        data: dict,
    ) -> bool:
        """Replace the JSON blob of an existing preset (e.g. after copying
        bg media into the persistent config dir post-insert)."""
        cur = await self.conn.execute(
            "UPDATE configs SET data_json=? WHERE id=? AND user_id=?",
            (json.dumps(data, ensure_ascii=False), config_id, user_id),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def delete_config(self, config_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM configs WHERE id=? AND user_id=?",
            (config_id, user_id),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def log_conversion(self, user_id: int, fmt: str, cost_kop: int, paid_with: str) -> None:
        await self.conn.execute(
            "INSERT INTO conversions(user_id, format, cost_kop, paid_with, created_at) "
            "VALUES(?, ?, ?, ?, ?)",
            (user_id, fmt, cost_kop, paid_with, int(time.time())),
        )
        await self.conn.commit()

    async def stats(self) -> dict:
        c = self.conn

        async def scalar(q: str, *a: Any) -> int:
            cur = await c.execute(q, a)
            row = await cur.fetchone()
            return int(row[0] or 0) if row else 0

        now = int(time.time())
        day = now - 86400
        week = now - 7 * 86400
        month = now - 30 * 86400

        return {
            "users_total": await scalar("SELECT COUNT(*) FROM users"),
            "users_today": await scalar("SELECT COUNT(*) FROM users WHERE created_at>=?", day),
            "renders_total": await scalar("SELECT COUNT(*) FROM conversions"),
            "renders_today": await scalar(
                "SELECT COUNT(*) FROM conversions WHERE created_at>=?", day
            ),
            "renders_week": await scalar(
                "SELECT COUNT(*) FROM conversions WHERE created_at>=?", week
            ),
            "renders_month": await scalar(
                "SELECT COUNT(*) FROM conversions WHERE created_at>=?", month
            ),
            "revenue_total_kop": await scalar(
                "SELECT COALESCE(SUM(amount_kop),0) FROM payments WHERE status='paid'"
            ),
            "revenue_today_kop": await scalar(
                "SELECT COALESCE(SUM(amount_kop),0) FROM payments WHERE status='paid' AND created_at>=?",
                day,
            ),
            "revenue_month_kop": await scalar(
                "SELECT COALESCE(SUM(amount_kop),0) FROM payments WHERE status='paid' AND created_at>=?",
                month,
            ),
            "balance_total_kop": await scalar("SELECT COALESCE(SUM(balance_kop),0) FROM users"),
        }

    async def create_payment(
        self,
        user_id: int,
        provider: str,
        amount_kop: int,
        external_id: str | None = None,
        extra: dict | None = None,
    ) -> int:
        now = int(time.time())
        cur = await self.conn.execute(
            "INSERT INTO payments(user_id, provider, amount_kop, status, "
            "external_id, extra_json, created_at, updated_at) "
            "VALUES(?, ?, ?, 'pending', ?, ?, ?, ?)",
            (
                user_id,
                provider,
                amount_kop,
                external_id,
                json.dumps(extra or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await self.conn.commit()
        return cur.lastrowid or 0

    async def get_payment(self, pay_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM payments WHERE id=?", (pay_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_payment_by_external(self, provider: str, external_id: str) -> dict | None:
        cur = await self.conn.execute(
            "SELECT * FROM payments WHERE provider=? AND external_id=?",
            (provider, external_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_payment_status(
        self, pay_id: int, status: str, external_id: str | None = None
    ) -> None:
        if external_id is not None:
            await self.conn.execute(
                "UPDATE payments SET status=?, external_id=?, updated_at=? WHERE id=?",
                (status, external_id, int(time.time()), pay_id),
            )
        else:
            await self.conn.execute(
                "UPDATE payments SET status=?, updated_at=? WHERE id=?",
                (status, int(time.time()), pay_id),
            )
        await self.conn.commit()

    async def list_pending_payments(self, provider: str) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM payments WHERE provider=? AND status='pending' ORDER BY id DESC",
            (provider,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_payment_channel(self) -> int | None:
        v = await self.get_setting("payment_channel_id")
        try:
            return int(v) if v else None
        except ValueError:
            return None

    async def set_payment_channel(self, channel_id: int) -> None:
        await self.set_setting("payment_channel_id", str(channel_id))

    async def get_yoomoney_url(self, default: str = "") -> str:
        v = await self.get_setting("yoomoney_url")
        return v if v else default

    async def set_yoomoney_url(self, url: str) -> None:
        await self.set_setting("yoomoney_url", url.strip())

    async def get_sbp_url(self, default: str = "") -> str:
        v = await self.get_setting("sbp_url")
        return v if v else default

    async def set_sbp_url(self, url: str) -> None:
        await self.set_setting("sbp_url", url.strip())

    async def get_media(self, screen_key: str) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM media WHERE screen_key=?", (screen_key,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_media(self, screen_key: str, file_id: str, kind: str) -> None:
        await self.conn.execute(
            "INSERT INTO media(screen_key, file_id, kind, updated_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(screen_key) DO UPDATE SET "
            "file_id=excluded.file_id, kind=excluded.kind, updated_at=excluded.updated_at",
            (screen_key, file_id, kind, int(time.time())),
        )
        await self.conn.commit()

    async def delete_media(self, screen_key: str) -> None:
        await self.conn.execute("DELETE FROM media WHERE screen_key=?", (screen_key,))
        await self.conn.commit()

    async def list_media(self) -> list[dict]:
        cur = await self.conn.execute("SELECT * FROM media ORDER BY screen_key")
        return [dict(r) for r in await cur.fetchall()]
