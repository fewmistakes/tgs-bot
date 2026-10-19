"""Prices and balance operations."""

from __future__ import annotations

from db import DB

KEY_PRICE_RUB = "render_price_rub"  # int
KEY_FREE_RENDERS = "free_renders_default"  # int
KEY_PACK_PRICE = "pack_price_rub"  # int — legacy / full pack price
KEY_PACK_LOGO_PRICE = "pack_logo_price_rub"  # int — legacy / pack-with-logo price
KEY_PACK_SMALL = "pack_small_price_rub"  # int — pack of ≤20 tiles
KEY_PACK_FULL = "pack_full_price_rub"  # int — pack of >20 tiles
KEY_PACK_PASSPORT = "pack_passport_price_rub"  # int — surcharge for passport
KEY_PAINT_EMOJI = "paint_emoji_price_rub"  # int — recolour a single emoji
KEY_PAINT_PACK = "paint_pack_price_rub"  # int — recolour a whole pack

DEFAULT_PACK_PRICE_RUB = 100
DEFAULT_PACK_LOGO_PRICE_RUB = 125
DEFAULT_PACK_SMALL_PRICE_RUB = 30  # ≤ 20 tiles, passport excluded
DEFAULT_PACK_FULL_PRICE_RUB = 50  # > 20 tiles
DEFAULT_PACK_PASSPORT_PRICE_RUB = 25  # passport surcharge, or the price of a passport-only pack
DEFAULT_PACK_SMALL_THRESHOLD = 20
DEFAULT_PAINT_EMOJI_PRICE_RUB = 5
DEFAULT_PAINT_PACK_PRICE_RUB = 25


class Billing:
    def __init__(self, db: DB, default_price_rub: int, default_free: int) -> None:
        self.db = db
        self._default_price = default_price_rub
        self._default_free = default_free

    async def get_price_kop(self) -> int:
        return await self.db.get_int_setting(KEY_PRICE_RUB, self._default_price) * 100

    async def get_price_rub(self) -> int:
        return await self.db.get_int_setting(KEY_PRICE_RUB, self._default_price)

    async def set_price_rub(self, value: int) -> None:
        await self.db.set_setting(KEY_PRICE_RUB, str(max(1, int(value))))

    async def get_free_default(self) -> int:
        return await self.db.get_int_setting(KEY_FREE_RENDERS, self._default_free)

    async def set_free_default(self, value: int) -> None:
        await self.db.set_setting(KEY_FREE_RENDERS, str(max(0, int(value))))

    async def can_render(self, user_id: int) -> bool:
        return await self.can_render_n(user_id, 1)

    async def can_render_n(self, user_id: int, n: int) -> bool:
        if n <= 0:
            return True
        u = await self.db.get_user(user_id)
        if not u:
            return False
        if u["free_renders"] >= n:
            return True
        needed_paid = n - u["free_renders"]
        price = await self.get_price_kop()
        return u["balance_kop"] >= price * needed_paid

    async def consume_render(self, user_id: int) -> tuple[bool, str]:
        price = await self.get_price_kop()
        return await self.db.consume_render(user_id, price)

    async def add_rub(self, user_id: int, rub: int) -> int:
        return await self.db.add_balance_kop(user_id, rub * 100)

    async def add_kop(self, user_id: int, kop: int) -> int:
        return await self.db.add_balance_kop(user_id, kop)

    async def get_pack_price_rub(self) -> int:
        return await self.db.get_int_setting(KEY_PACK_PRICE, DEFAULT_PACK_PRICE_RUB)

    async def get_pack_logo_price_rub(self) -> int:
        return await self.db.get_int_setting(
            KEY_PACK_LOGO_PRICE,
            DEFAULT_PACK_LOGO_PRICE_RUB,
        )

    async def set_pack_price_rub(self, value: int) -> None:
        await self.db.set_setting(KEY_PACK_PRICE, str(max(0, int(value))))

    async def set_pack_logo_price_rub(self, value: int) -> None:
        await self.db.set_setting(KEY_PACK_LOGO_PRICE, str(max(0, int(value))))

    async def get_pack_small_price_rub(self) -> int:
        return await self.db.get_int_setting(
            KEY_PACK_SMALL,
            DEFAULT_PACK_SMALL_PRICE_RUB,
        )

    async def get_pack_full_price_rub(self) -> int:
        return await self.db.get_int_setting(
            KEY_PACK_FULL,
            DEFAULT_PACK_FULL_PRICE_RUB,
        )

    async def get_pack_passport_price_rub(self) -> int:
        return await self.db.get_int_setting(
            KEY_PACK_PASSPORT,
            DEFAULT_PACK_PASSPORT_PRICE_RUB,
        )

    async def set_pack_small_price_rub(self, value: int) -> None:
        await self.db.set_setting(KEY_PACK_SMALL, str(max(0, int(value))))

    async def set_pack_full_price_rub(self, value: int) -> None:
        await self.db.set_setting(KEY_PACK_FULL, str(max(0, int(value))))

    async def set_pack_passport_price_rub(self, value: int) -> None:
        await self.db.set_setting(KEY_PACK_PASSPORT, str(max(0, int(value))))

    async def quote_pack_price_rub(
        self,
        *,
        count: int,
        has_passport: bool,
    ) -> int:
        """Compute the total RUB price for a pack of `count` tiles
        (excluding the passport itself; `has_passport` is a separate flag).

        Pricing tiers (per user spec):
          - passport only (count=0, has_passport=True):   25₽
          - 1..20 tiles, no passport:                     30₽
          - 1..20 tiles, with passport:                   30₽ + 25₽ = 55₽
          - 21+ tiles, no passport:                       50₽
          - 21+ tiles, with passport:                     50₽ + 25₽ = 75₽
        """
        passport_rub = await self.get_pack_passport_price_rub()
        if count <= 0:
            return passport_rub if has_passport else 0
        threshold = DEFAULT_PACK_SMALL_THRESHOLD
        if count <= threshold:
            base = await self.get_pack_small_price_rub()
        else:
            base = await self.get_pack_full_price_rub()
        return base + (passport_rub if has_passport else 0)

    async def charge_pack(
        self,
        user_id: int,
        *,
        with_logo: bool = False,  # legacy: ignored when count/has_passport given
        count: int | None = None,
        has_passport: bool = False,
    ) -> tuple[bool, int, int]:
        """Try to deduct pack price. Returns (ok, balance_kop_after, price_kop).

        New tiered model: pass `count` (selected tile count, passport NOT
        counted) and `has_passport`. Old call sites that pass only
        `with_logo` keep using the legacy fixed price.
        """
        if count is not None:
            price_rub = await self.quote_pack_price_rub(
                count=count,
                has_passport=has_passport,
            )
        else:
            price_rub = (
                await self.get_pack_logo_price_rub()
                if with_logo
                else await self.get_pack_price_rub()
            )
        price_kop = price_rub * 100
        u = await self.db.get_user(user_id)
        if u is None or u["balance_kop"] < price_kop:
            balance = u["balance_kop"] if u else 0
            return False, balance, price_kop
        new_balance = await self.db.add_balance_kop(user_id, -price_kop)
        return True, new_balance, price_kop

    async def refund_pack(self, user_id: int, price_kop: int) -> int:
        return await self.db.add_balance_kop(user_id, price_kop)

    async def get_paint_emoji_price_rub(self) -> int:
        return await self.db.get_int_setting(KEY_PAINT_EMOJI, DEFAULT_PAINT_EMOJI_PRICE_RUB)

    async def get_paint_pack_price_rub(self) -> int:
        return await self.db.get_int_setting(KEY_PAINT_PACK, DEFAULT_PAINT_PACK_PRICE_RUB)

    async def charge_paint(self, user_id: int, mode: str) -> tuple[bool, int, int]:
        """mode: 'emoji' | 'pack'. Returns (ok, balance_kop_after, price_kop)."""
        price_rub = (
            await self.get_paint_pack_price_rub()
            if mode == "pack"
            else await self.get_paint_emoji_price_rub()
        )
        price_kop = price_rub * 100
        u = await self.db.get_user(user_id)
        if u is None or u["balance_kop"] < price_kop:
            balance = u["balance_kop"] if u else 0
            return False, balance, price_kop
        new_balance = await self.db.add_balance_kop(user_id, -price_kop)
        return True, new_balance, price_kop

    async def refund_paint(self, user_id: int, price_kop: int) -> int:
        return await self.db.add_balance_kop(user_id, price_kop)
