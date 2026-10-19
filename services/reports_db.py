"""Settings for the purchase-report channel, kept apart from bot.db.

Schema: settings(key TEXT PRIMARY KEY, value TEXT NOT NULL). Only `channel_id`
is used today; the key/value layout avoids migrations for future settings.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from utils.logger import get_logger

log = get_logger("reports_db")


class ReportsDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("ReportsDB not opened")
        return self._conn

    async def _migrate(self) -> None:
        await self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        await self.conn.commit()
        log.info("reports_db migrated: %s", self.path)

    async def get_channel(self) -> int | None:
        cur = await self.conn.execute("SELECT value FROM settings WHERE key='channel_id'")
        row = await cur.fetchone()
        if not row:
            return None
        try:
            return int(row["value"])
        except (ValueError, TypeError):
            return None

    async def set_channel(self, channel_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES('channel_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(channel_id),),
        )
        await self.conn.commit()

    async def clear_channel(self) -> None:
        await self.conn.execute("DELETE FROM settings WHERE key='channel_id'")
        await self.conn.commit()
