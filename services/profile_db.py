"""Profile statistics and built packs, kept in a separate SQLite file.

Tables:
    profile(user_id, total_packs, total_pack_emojis)
    packs(id, user_id, short_name, title, format, included_json,
          settings_json, created_at, updated_at)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import aiosqlite

from utils.logger import get_logger

log = get_logger("profile_db")


class ProfileDB:
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
            raise RuntimeError("ProfileDB not opened")
        return self._conn

    async def _migrate(self) -> None:
        await self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            user_id           INTEGER PRIMARY KEY,
            total_packs       INTEGER NOT NULL DEFAULT 0,
            total_pack_emojis INTEGER NOT NULL DEFAULT 0,
            updated_at        INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS packs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            short_name     TEXT NOT NULL UNIQUE,
            title          TEXT NOT NULL,
            format         TEXT NOT NULL,
            included_json  TEXT NOT NULL,
            settings_json  TEXT NOT NULL,
            created_at     INTEGER NOT NULL,
            updated_at     INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS packs_user ON packs(user_id);
        """)
        await self.conn.commit()
        log.info("profile_db migrated: %s", self.path)

    async def get(self, user_id: int) -> dict:
        cur = await self.conn.execute(
            "SELECT total_packs, total_pack_emojis FROM profile WHERE user_id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return {"total_packs": 0, "total_pack_emojis": 0}
        return {
            "total_packs": int(row["total_packs"] or 0),
            "total_pack_emojis": int(row["total_pack_emojis"] or 0),
        }

    async def add_pack_stats(self, user_id: int, emoji_count: int) -> None:
        now = int(time.time())
        emoji_count = max(0, int(emoji_count))
        await self.conn.execute(
            "INSERT INTO profile(user_id, total_packs, total_pack_emojis, updated_at) "
            "VALUES(?, 1, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "total_packs = total_packs + 1, "
            "total_pack_emojis = total_pack_emojis + excluded.total_pack_emojis, "
            "updated_at = excluded.updated_at",
            (user_id, emoji_count, now),
        )
        await self.conn.commit()

    async def save_pack(
        self,
        *,
        user_id: int,
        short_name: str,
        title: str,
        fmt: str,
        included: list,
        settings: dict,
    ) -> int:
        """Insert a pack record. If short_name already exists (rare race),
        overwrite — Telegram short_names are globally unique so the row must
        belong to the same user."""
        now = int(time.time())
        included_json = json.dumps(_normalize_ids(included), ensure_ascii=False)
        settings_json = json.dumps(settings, ensure_ascii=False)
        await self.conn.execute(
            "INSERT INTO packs("
            "  user_id, short_name, title, format,"
            "  included_json, settings_json, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(short_name) DO UPDATE SET "
            "  title=excluded.title, format=excluded.format,"
            "  included_json=excluded.included_json,"
            "  settings_json=excluded.settings_json,"
            "  updated_at=excluded.updated_at",
            (user_id, short_name, title, fmt, included_json, settings_json, now, now),
        )
        await self.conn.commit()
        cur = await self.conn.execute(
            "SELECT id FROM packs WHERE short_name=?",
            (short_name,),
        )
        row = await cur.fetchone()
        return int(row["id"]) if row else 0

    async def list_user_packs(self, user_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT id, short_name, title, format, included_json,"
            "       created_at, updated_at "
            "FROM packs WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                included = json.loads(r["included_json"]) or []
            except Exception:
                included = []
            out.append(
                {
                    "id": int(r["id"]),
                    "short_name": r["short_name"],
                    "title": r["title"],
                    "format": r["format"],
                    "included": _normalize_ids(included),
                    "count": len(included),
                    "created_at": int(r["created_at"]),
                    "updated_at": int(r["updated_at"]),
                }
            )
        return out

    async def get_pack(self, pack_id: int, user_id: int) -> dict | None:
        cur = await self.conn.execute(
            "SELECT * FROM packs WHERE id=? AND user_id=?",
            (pack_id, user_id),
        )
        r = await cur.fetchone()
        if not r:
            return None
        try:
            included = json.loads(r["included_json"]) or []
        except Exception:
            included = []
        try:
            settings = json.loads(r["settings_json"]) or {}
        except Exception:
            settings = {}
        return {
            "id": int(r["id"]),
            "user_id": int(r["user_id"]),
            "short_name": r["short_name"],
            "title": r["title"],
            "format": r["format"],
            "included": _normalize_ids(included),
            "settings": settings,
            "created_at": int(r["created_at"]),
            "updated_at": int(r["updated_at"]),
        }

    async def rename_pack(
        self,
        pack_id: int,
        user_id: int,
        new_title: str,
    ) -> bool:
        now = int(time.time())
        cur = await self.conn.execute(
            "UPDATE packs SET title=?, updated_at=? WHERE id=? AND user_id=?",
            (new_title, now, pack_id, user_id),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def add_included_tiles(
        self,
        pack_id: int,
        user_id: int,
        new_ids: list,
    ) -> bool:
        """Merge `new_ids` into the included_json list (no duplicates,
        preserve original ordering, append new ones at the end)."""
        cur = await self.conn.execute(
            "SELECT included_json FROM packs WHERE id=? AND user_id=?",
            (pack_id, user_id),
        )
        r = await cur.fetchone()
        if not r:
            return False
        try:
            current = json.loads(r["included_json"]) or []
        except Exception:
            current = []
        current = _normalize_ids(current)
        seen = set(current)
        for nid in _normalize_ids(new_ids):
            if nid not in seen:
                current.append(nid)
                seen.add(nid)
        now = int(time.time())
        await self.conn.execute(
            "UPDATE packs SET included_json=?, updated_at=? WHERE id=? AND user_id=?",
            (json.dumps(current, ensure_ascii=False), now, pack_id, user_id),
        )
        await self.conn.commit()
        return True


def _normalize_ids(ids) -> list:
    """Coerce mixed id list into a canonical form: int for master tiles,
    str ``"new:N"`` for new-tab tiles. Keeps the input order."""
    out: list = []
    for x in ids or []:
        if isinstance(x, int):
            out.append(x)
            continue
        s = str(x)
        if s.startswith("new:"):
            out.append(s)
        else:
            try:
                out.append(int(s))
            except ValueError:
                continue
    return out
