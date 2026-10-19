"""Promo codes, kept in a separate SQLite file.

Tables:
    promocodes(id, code, balance_kop, max_uses, used_count, created_by, created_at)
    promocode_uses(promo_id, user_id, used_at)  PK(promo_id, user_id)

try_consume() checks the limit and records the user atomically; crediting the
balance is up to the caller.
"""

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from utils.logger import get_logger

log = get_logger("promo_db")


class PromoDB:
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
            raise RuntimeError("PromoDB not opened")
        return self._conn

    async def _migrate(self) -> None:
        await self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS promocodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT NOT NULL UNIQUE,
            balance_kop INTEGER NOT NULL,
            max_uses    INTEGER NOT NULL,
            used_count  INTEGER NOT NULL DEFAULT 0,
            created_by  INTEGER,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS promo_code ON promocodes(code);

        CREATE TABLE IF NOT EXISTS promocode_uses (
            promo_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            used_at    INTEGER NOT NULL,
            PRIMARY KEY (promo_id, user_id)
        );
        """)
        await self.conn.commit()
        log.info("promo_db migrated: %s", self.path)

    async def create(
        self,
        code: str,
        balance_kop: int,
        max_uses: int,
        created_by: int | None = None,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO promocodes(code, balance_kop, max_uses, used_count, "
            "created_by, created_at) VALUES(?, ?, ?, 0, ?, ?)",
            (code, max(1, int(balance_kop)), max(1, int(max_uses)), created_by, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid or 0

    async def get(self, code: str) -> dict | None:
        cur = await self.conn.execute(
            "SELECT * FROM promocodes WHERE LOWER(code)=LOWER(?)",
            (code,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list(self, limit: int = 50) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM promocodes ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def delete(self, promo_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM promocodes WHERE id=?",
            (promo_id,),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def try_consume(
        self,
        code: str,
        user_id: int,
    ) -> tuple[str, dict | None]:
        """Atomically record a promo code activation for the user.

        Returns (status, promo). On 'ok' the caller credits promo["balance_kop"] via
        the main DB. Other statuses: 'not_found', 'exhausted' (no activations left),
        'used' (this user already activated it).
        """
        c = self.conn
        promo = await self.get(code)
        if promo is None:
            return "not_found", None
        if promo["used_count"] >= promo["max_uses"]:
            return "exhausted", promo
        try:
            await c.execute(
                "INSERT INTO promocode_uses(promo_id, user_id, used_at) VALUES(?, ?, ?)",
                (promo["id"], user_id, int(time.time())),
            )
        except Exception:
            return "used", promo
        cur = await c.execute(
            "UPDATE promocodes SET used_count = used_count + 1 "
            "WHERE id=? AND used_count < max_uses",
            (promo["id"],),
        )
        if (cur.rowcount or 0) == 0:
            # Lost the race for the last slot: roll the use back.
            await c.execute(
                "DELETE FROM promocode_uses WHERE promo_id=? AND user_id=?",
                (promo["id"], user_id),
            )
            await c.commit()
            return "exhausted", promo
        await c.commit()
        return "ok", promo

    async def revert_consume(self, promo_id: int, user_id: int) -> None:
        """Undo an activation when crediting the balance failed."""
        c = self.conn
        cur = await c.execute(
            "DELETE FROM promocode_uses WHERE promo_id=? AND user_id=?",
            (promo_id, user_id),
        )
        if (cur.rowcount or 0) > 0:
            await c.execute(
                "UPDATE promocodes SET used_count = MAX(0, used_count - 1) WHERE id=?",
                (promo_id,),
            )
        await c.commit()
