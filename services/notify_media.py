"""Per-event media attachments for outbound notifications.

The admin can attach a GIF / photo / video to any event listed in
``NOTIFY_EVENTS`` from the admin panel. When the bot sends that event's
notification, the attached media becomes the message body and the styled
text becomes the caption. If no media is attached, a plain text message
is sent.

Storage reuses the existing ``media`` table, namespaced under
``notify:<event>`` so it doesn't collide with menu-screen media.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, Message

from db import DB
from utils.logger import get_logger

log = get_logger("notify_media")


# event_key -> human-readable label shown in the admin list
NOTIFY_EVENTS: dict[str, str] = {
    "credit": "Пополнение баланса",
    "payment_approved": "Оплата подтверждена (YooMoney)",
    "payment_rejected": "Оплата отклонена",
    "grant_rub": "Подарок · ₽",
    "grant_free": "Подарок · рендеры",
    "referral": "Реферальный бонус",
    "render_done": "Рендер готов",
    "balance_credited": "Ручное начисление баланса (админ)",
}

_PREFIX = "notify:"


def event_media_key(event_key: str) -> str:
    return f"{_PREFIX}{event_key}"


async def send_event(
    bot: Bot,
    db: DB,
    chat_id: int,
    event_key: str,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """Send a notification for an event.

    If admin attached a GIF/photo/video to this event, it's sent with the
    text as caption. Otherwise a plain HTML message is sent.
    On any send failure falls back to plain text so we never silently drop
    a notification.
    """
    media = await db.get_media(event_media_key(event_key))
    if media:
        kind = media["kind"]
        file_id = media["file_id"]
        try:
            if kind == "animation":
                return await bot.send_animation(
                    chat_id=chat_id,
                    animation=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            if kind == "video":
                return await bot.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            if kind == "photo":
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
        except Exception:
            log.exception(
                "notify event=%s media send failed; falling back to text",
                event_key,
            )
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
    )
