"""Confirmed-purchase reports to an admin-configured channel.

Used by every code path that flips a payment to `paid` and credits the
balance (CryptoBot recheck, Platega recheck, Platega auto-watcher,
admin YooMoney approval). A single helper keeps the report format
consistent across providers.

No-op when the admin hasn't configured a reports channel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode

from utils.logger import get_logger

log = get_logger("payments.reports")


_PROVIDER_LABELS: dict[str, str] = {
    "platega_sbp": "СБП",
    "cryptobot": "CryptoBot",
    "yoomoney": "ЮMoney",
    "sbp": "СБП",
    "stars": "Telegram Stars",
}

_MSK = timezone(timedelta(hours=3))
_REPORT_EMOJI = '<tg-emoji emoji-id="5870633910337015697">🧾</tg-emoji>'


def _format_msk(ts_unix: int) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts_unix), tz=_MSK)
    except Exception:
        dt = datetime.now(tz=_MSK)
    return dt.strftime("%d.%m.%Y %H:%M MSK")


def _format_report(
    *,
    username: str | None,
    user_id: int,
    amount_kop: int,
    provider: str,
    payment_id: int,
    ts_unix: int,
) -> str:
    user_display = f"@{username}" if username else "—"
    provider_label = _PROVIDER_LABELS.get(provider, provider or "?")
    return (
        f"{_REPORT_EMOJI} <b>Покупка подтверждена</b>\n"
        f"<blockquote>"
        f"<b>Пользователь:</b> <code>{user_display}</code>\n"
        f"<b>ID:</b> <code>{user_id}</code>\n"
        f"<b>Сумма:</b> <code>+{amount_kop / 100:.0f}₽</code>\n"
        f"<b>Способ:</b> <code>{provider_label}</code>\n"
        f"<b>Заявка:</b> <code>#{payment_id}</code>\n"
        f"<b>Дата:</b> <code>{_format_msk(ts_unix)}</code>"
        f"</blockquote>"
    )


async def report_payment_paid(
    bot: Bot,
    db: Any,
    reports_db: Any,
    pay: dict,
) -> None:
    """Send a confirmed-purchase report to the admin-configured channel.
    `pay` is a row from the payments table (already marked paid).

    The channel ID lives in an isolated DB (reports.db) so the channel
    config never touches the main users/balances database. The main DB
    is read-only here — just to look up the user's username."""
    channel_id = await reports_db.get_channel()
    if not channel_id:
        return
    user = await db.get_user(int(pay["user_id"]))
    username = (user or {}).get("username")
    import time as _time

    text = _format_report(
        username=username,
        user_id=int(pay["user_id"]),
        amount_kop=int(pay["amount_kop"]),
        provider=str(pay.get("provider") or ""),
        payment_id=int(pay["id"]),
        ts_unix=int(pay.get("updated_at") or _time.time()),
    )
    try:
        await bot.send_message(
            chat_id=channel_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("failed to send payment report to channel %s", channel_id)
