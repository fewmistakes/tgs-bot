"""Platega cashier API client (SBP via QR).

Docs: https://docs.platega.io/

Auth — two headers on every request:
    X-MerchantId: <merchant uuid>
    X-Secret:     <api key>

We only need:
    - POST /transaction/process     — open an SBP transaction, get pay URL
    - GET  /transaction/{id}        — poll status (PENDING/CONFIRMED/...)
"""

from __future__ import annotations

from typing import Any

import aiohttp

from utils.logger import get_logger

log = get_logger("platega")

# paymentMethod codes from Platega's PaymentMethodInt enum
PAYMENT_METHOD_SBP = 2


class Platega:
    BASE_URL = "https://app.platega.io"

    def __init__(self, merchant_id: str, secret: str) -> None:
        self.merchant_id = (merchant_id or "").strip()
        self.secret = (secret or "").strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.merchant_id and self.secret)

    def _headers(self) -> dict[str, str]:
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
            "Content-Type": "application/json",
        }

    async def create_sbp(
        self,
        amount_rub: float,
        description: str,
        *,
        payload: str = "",
        return_url: str = "",
        failed_url: str = "",
    ) -> dict:
        """Open an SBP-QR transaction. Returns dict with `transactionId`,
        `redirect` (pay URL), `status`, `expiresIn`."""
        if not self.is_configured:
            raise RuntimeError("Platega is not configured")
        body: dict[str, Any] = {
            "paymentMethod": PAYMENT_METHOD_SBP,
            "paymentDetails": {"amount": float(amount_rub), "currency": "RUB"},
            "description": description,
        }
        if return_url:
            body["return"] = return_url
        if failed_url:
            body["failedUrl"] = failed_url
        if payload:
            body["payload"] = payload

        url = f"{self.BASE_URL}/transaction/process"
        async with (
            aiohttp.ClientSession() as s,
            s.post(
                url,
                json=body,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r,
        ):
            data = await r.json(content_type=None)
            if r.status >= 400:
                log.error("Platega create_sbp HTTP %s: %s", r.status, data)
                raise RuntimeError(f"Platega create failed: HTTP {r.status} {data}")
            return data

    async def get_transaction(self, transaction_id: str) -> dict | None:
        if not self.is_configured:
            return None
        url = f"{self.BASE_URL}/transaction/{transaction_id}"
        async with (
            aiohttp.ClientSession() as s,
            s.get(
                url,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r,
        ):
            if r.status == 404:
                return None
            try:
                data = await r.json(content_type=None)
            except Exception:
                return None
            if r.status >= 400:
                log.error("Platega get_tx HTTP %s: %s", r.status, data)
                return None
            return data


# Background poller — auto-confirms Platega SBP payments without
# requiring the user to press a "check payment" button.
async def watch_payments(
    *,
    bot: Any,
    db: Any,
    billing: Any,
    platega: Platega,
    reports_db: Any = None,
    interval_sec: float = 8.0,
) -> None:
    """Loop forever. For each pending platega_sbp payment, ask Platega
    for its current status; if CONFIRMED — credit balance and notify
    the user; if CANCELED / CHARGEBACKED — mark expired."""
    import asyncio

    from services.notify_media import send_event
    from services.notify_texts import credit_text
    from services.payments.reports import report_payment_paid

    if not platega.is_configured:
        log.info("Platega watcher not started — credentials are empty")
        return

    log.info("Platega watcher started (poll every %.1fs)", interval_sec)
    while True:
        try:
            pending = await db.list_pending_payments("platega_sbp")
            for pay in pending:
                tx_id = (pay.get("external_id") or "").strip()
                if not tx_id:
                    continue
                tx = await platega.get_transaction(tx_id)
                if not tx:
                    continue
                status = (tx.get("status") or "").upper()
                if status == "CONFIRMED":
                    await db.update_payment_status(pay["id"], "paid")
                    new_bal = await billing.add_kop(
                        pay["user_id"],
                        pay["amount_kop"],
                    )
                    log.info(
                        "platega: credited user=%s pid=%s amount=%dk",
                        pay["user_id"],
                        pay["id"],
                        pay["amount_kop"],
                    )
                    if reports_db is not None:
                        try:
                            fresh = await db.get_payment(pay["id"]) or pay
                            await report_payment_paid(bot, db, reports_db, fresh)
                        except Exception:
                            log.exception("platega: failed to report to channel")
                    try:
                        await send_event(
                            bot,
                            db,
                            pay["user_id"],
                            "credit",
                            credit_text(
                                pay["amount_kop"] / 100,
                                new_bal / 100,
                            ),
                        )
                    except Exception:
                        log.exception("platega: failed to notify user")
                elif status in ("CANCELED", "CHARGEBACKED"):
                    await db.update_payment_status(pay["id"], "expired")
                    log.info(
                        "platega: tx %s ended with status=%s",
                        tx_id,
                        status,
                    )
        except asyncio.CancelledError:
            log.info("Platega watcher cancelled")
            raise
        except Exception:
            log.exception("Platega watcher iteration failed")
        await asyncio.sleep(interval_sec)
