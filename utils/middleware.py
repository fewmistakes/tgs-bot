"""DI middleware — pushes shared services into every handler call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import Config
from db import DB
from services.billing import Billing
from services.payments.cryptobot import CryptoBot
from services.payments.platega import Platega
from services.profile_db import ProfileDB
from services.promo_db import PromoDB
from services.reports_db import ReportsDB


class DIMiddleware(BaseMiddleware):
    def __init__(
        self,
        cfg: Config,
        db: DB,
        billing: Billing,
        cryptobot: CryptoBot,
        platega: Platega,
        profile_db: ProfileDB,
        promo_db: PromoDB,
        reports_db: ReportsDB,
    ) -> None:
        self._cfg = cfg
        self._db = db
        self._billing = billing
        self._cryptobot = cryptobot
        self._platega = platega
        self._profile_db = profile_db
        self._promo_db = promo_db
        self._reports_db = reports_db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["cfg"] = self._cfg
        data["db"] = self._db
        data["billing"] = self._billing
        data["cryptobot"] = self._cryptobot
        data["platega"] = self._platega
        data["profile_db"] = self._profile_db
        data["promo_db"] = self._promo_db
        data["reports_db"] = self._reports_db
        return await handler(event, data)
