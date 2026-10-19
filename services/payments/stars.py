"""Telegram Stars invoice helper.

Stars use the standard Bot Payments API with currency='XTR'. No provider
token, no card details — Telegram handles everything natively.

Flow:
    1. bot.send_invoice(currency='XTR', prices=[LabeledPrice(amount=N)])
    2. User taps "Pay" → Telegram → success
    3. We get pre_checkout_query  → must answer ok=True
    4. We get successful_payment  → credit balance, mark payment paid
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import LabeledPrice


async def send_stars_invoice(
    bot: Bot,
    chat_id: int,
    *,
    title: str,
    description: str,
    payload: str,
    stars: int,
) -> None:
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=int(stars))],
    )
