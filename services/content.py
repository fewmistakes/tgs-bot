"""Access to editable bot strings.

    text = await t("main.text", balance=110)
    label = await btn("main.btn.wallet", balance=110)

Lookups check the `content_overrides` table first and fall back to the
defaults in content.registry. Results are cached in memory; the admin editor
calls invalidate(key) after a change. Missing format placeholders render as
empty strings instead of raising.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from content.registry import ITEMS_BY_KEY

log = logging.getLogger("content")


class _SafeDict(dict):
    """dict subclass that returns "" for missing keys in str.format_map."""

    def __missing__(self, key: str) -> str:
        return ""


class ContentService:
    """Holds DB ref + in-memory cache."""

    def __init__(self, db) -> None:
        self.db = db
        self._cache: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self) -> None:
        """Pull all overrides from DB into memory at startup."""
        async with self._lock:
            cur = await self.db.conn.execute("SELECT key, value FROM content_overrides")
            rows = await cur.fetchall()
            self._cache = {r["key"]: r["value"] for r in rows}
            self._loaded = True
            log.info("loaded %d content overrides", len(self._cache))

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def _raw(self, key: str) -> str:
        """Resolve raw template — override > default."""
        if key in self._cache:
            return self._cache[key]
        item = ITEMS_BY_KEY.get(key)
        return item.default if item else f"[?{key}]"

    def render(self, key: str, **fmt: Any) -> str:
        """Rendered template (safe formatting, missing placeholders → '')."""
        raw = self._raw(key)
        try:
            return raw.format_map(_SafeDict(**fmt))
        except (IndexError, ValueError) as exc:
            log.warning("content[%s] format error: %s", key, exc)
            return raw

    async def set_override(self, key: str, value: str) -> None:
        async with self._lock:
            await self.db.conn.execute(
                "INSERT INTO content_overrides(key, value, updated_at) "
                "VALUES(?, ?, strftime('%s','now')) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (key, value),
            )
            await self.db.conn.commit()
            self._cache[key] = value

    async def clear_override(self, key: str) -> None:
        async with self._lock:
            await self.db.conn.execute("DELETE FROM content_overrides WHERE key=?", (key,))
            await self.db.conn.commit()
            self._cache.pop(key, None)

    def is_overridden(self, key: str) -> bool:
        return key in self._cache


_service: ContentService | None = None


def init(db) -> ContentService:
    global _service
    _service = ContentService(db)
    return _service


def service() -> ContentService:
    if _service is None:
        raise RuntimeError("content service not initialised")
    return _service


# Sync helpers — handlers can call these directly without awaiting since the
# cache is loaded once at startup.
def t(key: str, **fmt: Any) -> str:
    """Render a text template by key."""
    return service().render(key, **fmt)


def btn(key: str, **fmt: Any) -> str:
    """Same as t() — semantically a button label."""
    return service().render(key, **fmt)
