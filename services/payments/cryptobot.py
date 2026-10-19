"""Crypto Pay (CryptoBot) API client.

Docs: https://help.crypt.bot/crypto-pay-api

We only need:
    - createInvoice  — make a paywall URL for the user
    - getInvoices    — poll status to detect "paid"
"""

from __future__ import annotations

from typing import Any

import aiohttp

from utils.logger import get_logger

log = get_logger("cryptobot")


class CryptoBot:
    BASE_MAINNET = "https://pay.crypt.bot/api"
    BASE_TESTNET = "https://testnet-pay.crypt.bot/api"

    def __init__(self, token: str, testnet: bool = False) -> None:
        self.token = token
        self.base = self.BASE_TESTNET if testnet else self.BASE_MAINNET

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    async def _call(self, method: str, **payload: Any) -> dict:
        if not self.token:
            raise RuntimeError("CryptoBot token is not configured")
        headers = {"Crypto-Pay-API-Token": self.token}
        url = f"{self.base}/{method}"
        async with (
            aiohttp.ClientSession() as s,
            s.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as r,
        ):
            data = await r.json()
        if not data.get("ok"):
            log.error("CryptoBot %s failed: %s", method, data)
            raise RuntimeError(f"CryptoBot {method} failed: {data}")
        return data["result"]

    async def create_invoice(
        self,
        amount: float,
        asset: str = "USDT",
        description: str = "Top-up",
        payload: str = "",
        expires_in: int = 3600,
    ) -> dict:
        """Returns the invoice dict including pay_url and invoice_id."""
        return await self._call(
            "createInvoice",
            currency_type="crypto",
            asset=asset,
            amount=str(amount),
            description=description,
            payload=payload,
            expires_in=expires_in,
            allow_comments=False,
            allow_anonymous=True,
        )

    async def get_invoice(self, invoice_id: int) -> dict | None:
        try:
            res = await self._call("getInvoices", invoice_ids=[int(invoice_id)])
        except RuntimeError:
            return None
        items = res.get("items") or []
        return items[0] if items else None

    async def get_exchange_rates(self) -> list[dict]:
        try:
            return await self._call("getExchangeRates") or []  # type: ignore[no-any-return]
        except RuntimeError:
            return []

    async def rub_per_unit(self, asset: str) -> float | None:
        """Returns RUB price of 1 unit of `asset` (e.g. USDT, TON)."""
        rates = await self.get_exchange_rates()
        for r in rates:
            if r.get("source") == asset and r.get("target") == "RUB":
                try:
                    return float(r["rate"])
                except (KeyError, ValueError):
                    pass
        return None
