"""
Screens — every key UI screen has an admin-uploadable GIF/photo header.

The admin can attach a GIF/photo to each `screen_key` via the admin panel.
When the bot displays that screen, it sends the media + caption + keyboard.
If no media is set, the bot falls back to a plain text message.

This gives the bot the polished "Striver Converter" feel from the screenshots
without hard-coding any media in the codebase.
"""

from __future__ import annotations

import contextlib

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup,
    Message,
)

from db import DB
from utils.logger import get_logger

log = get_logger("screens")


class S:
    MAIN_MENU = "main"
    WALLET = "wallet"
    PAYMENT_OK = "payment_ok"
    BG_COLOR = "bg_color"
    RESOLUTION = "resolution"
    FORMAT = "format"
    MEDIA = "media"
    EMOJI_COLOR = "emoji_color"
    CONFIGS = "configs"
    WATERMARK = "watermark"
    PREVIEW = "preview"
    SUCCESS = "success"
    ADMIN = "admin"
    INFO = "info"
    PACK = "pack"
    PACK_NICK = "pack_nick"
    PACK_FONT = "pack_font"
    PACK_FORMAT = "pack_format"
    PACK_PAINT = "pack_paint"
    GIF_HOME = "gif_home"
    OTHER = "other"
    PROMO = "promo"


SCREEN_LABELS: dict[str, str] = {
    S.MAIN_MENU: "Главное меню",
    S.WALLET: "Кошелёк",
    S.PAYMENT_OK: "Успешная оплата",
    S.BG_COLOR: "Цвет фона",
    S.RESOLUTION: "Разрешение",
    S.FORMAT: "Формат вывода",
    S.MEDIA: "Своя медиа",
    S.EMOJI_COLOR: "Цвет эмодзи",
    S.CONFIGS: "Конфиги",
    S.WATERMARK: "Вотермарка",
    S.PREVIEW: "Предосмотр",
    S.SUCCESS: "Готовая GIF",
    S.ADMIN: "Админ-панель",
    S.INFO: "Информация",
    S.PACK: "Создать пак · меню",
    S.PACK_NICK: "Создать пак · ник",
    S.PACK_FONT: "Создать пак · шрифт",
    S.PACK_FORMAT: "Создать пак · формат",
    S.PACK_PAINT: "Создать пак · покраска",
    S.GIF_HOME: "Создание GIF",
    S.OTHER: "Прочее",
    S.PROMO: "Промокод",
}


async def send_screen(
    bot: Bot,
    db: DB,
    chat_id: int,
    screen_key: str,
    text: str,
    *,
    keyboard: InlineKeyboardMarkup | None = None,
) -> Message:
    """Send a screen with its admin-configured cover (or plain text fallback)."""
    media = await db.get_media(screen_key)
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
                    reply_markup=keyboard,
                )
            if kind == "video":
                return await bot.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            if kind == "photo":
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
        except Exception:
            log.exception("failed to send media for screen=%s, falling back to text", screen_key)
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def safe_edit(
    msg: Message,
    text: str,
    *,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit a callback message in-place, regardless of whether it has media.

    - Plain text message → edit_text
    - Message with photo/video/animation → edit_caption (media stays attached)
    - On any other failure → delete + send fresh text message

    Use this in callback handlers when transitioning to a sub-screen prompt
    that doesn't have its own admin-uploaded media — the section's media
    remains visible and the user doesn't see a "message can't be edited" error.
    """
    has_media = bool(
        getattr(msg, "animation", None)
        or getattr(msg, "video", None)
        or getattr(msg, "photo", None)
    )
    try:
        if has_media:
            await msg.edit_caption(
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        else:
            await msg.edit_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        return
    except Exception as exc:
        if "message is not modified" in str(exc):
            return
        log.exception("safe_edit failed for chat=%s msg=%s", msg.chat.id, msg.message_id)

    with contextlib.suppress(Exception):
        await msg.delete()
    bot = msg.bot
    if bot is not None:
        await bot.send_message(
            chat_id=msg.chat.id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def edit_screen(
    msg: Message,
    db: DB,
    screen_key: str,
    text: str,
    *,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit existing screen message in-place when possible.

    Strategy:
    - text msg  + no media  → edit_text
    - text msg  + has media → edit_message_media (swap in-place, no flicker)
    - media msg + no media  → edit_caption (drop media) or edit_text after delete
    - media msg + has media → edit_caption
    """
    media_rec = await db.get_media(screen_key)

    has_media_now = bool(
        getattr(msg, "animation", None)
        or getattr(msg, "video", None)
        or getattr(msg, "photo", None)
    )

    # Text message, screen needs media: resend.
    if media_rec and not has_media_now:
        with contextlib.suppress(Exception):
            await msg.delete()
        await send_screen(msg.bot, db, msg.chat.id, screen_key, text, keyboard=keyboard)  # type: ignore[union-attr]
        return

    # Media message, screen is text-only: resend.
    if has_media_now and not media_rec:
        with contextlib.suppress(Exception):
            await msg.delete()
        await send_screen(msg.bot, db, msg.chat.id, screen_key, text, keyboard=keyboard)  # type: ignore[union-attr]
        return

    # Media to media: always resend so the right GIF/photo is shown.
    if has_media_now:
        with contextlib.suppress(Exception):
            await msg.delete()
        await send_screen(msg.bot, db, msg.chat.id, screen_key, text, keyboard=keyboard)  # type: ignore[union-attr]
        return

    # Text to text: edit in place.
    try:
        await msg.edit_text(text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    except Exception as exc:
        err_str = str(exc)
        if "message is not modified" in err_str:
            return  # content unchanged
        log.exception("edit_text failed for screen=%s", screen_key)

    # Anything else: resend.
    with contextlib.suppress(Exception):
        await msg.delete()
    await send_screen(msg.bot, db, msg.chat.id, screen_key, text, keyboard=keyboard)  # type: ignore[arg-type]
