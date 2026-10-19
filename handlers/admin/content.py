"""Admin content editor — edit every editable text/button/media in the bot."""

from __future__ import annotations

from typing import cast

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from content.registry import (
    CATEGORY_LABELS,
    ITEMS_BY_KEY,
    items_for_category,
)
from keyboards.cb import CB
from services.content import service as content_service
from utils.emoji import EmojiID

router = Router(name="admin_content")


class ContentEditSG(StatesGroup):
    waiting_value = State()


def _ensure_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.admin_ids


def _btn(text: str, cb: str, emoji: str | None = None) -> InlineKeyboardButton:
    kw: dict = {"text": text, "callback_data": cb}
    if emoji:
        kw["icon_custom_emoji_id"] = emoji
    return InlineKeyboardButton(**kw)


@router.callback_query(F.data == "a:content")
async def adm_content_home(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return

    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for cat_key, cat_label in CATEGORY_LABELS.items():
        items = items_for_category(cat_key)
        edited = sum(1 for it in items if content_service().is_overridden(it.key))
        marker = f" · {edited}/{len(items)}" if edited else ""
        pair.append(_btn(f"{cat_label}{marker}", f"a:c:cat:{cat_key}", emoji=EmojiID.WRITE))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK)])

    await cast(Message, callback.message).edit_text(
        "<b>✍ Тексты и кнопки</b>\n\n"
        "<i>Выбери раздел. Цифры показывают сколько строк изменено.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("a:c:cat:"))
async def adm_content_category(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    cat_key = callback.data[len("a:c:cat:") :]
    label = CATEGORY_LABELS.get(cat_key, cat_key)
    items = items_for_category(cat_key)

    rows: list[list[InlineKeyboardButton]] = []
    for it in items:
        marker = "✓" if content_service().is_overridden(it.key) else "—"
        # Truncate very long keys for visual sanity
        short = it.key.split(".", 1)[1] if "." in it.key else it.key
        rows.append(
            [
                _btn(f"{marker}  {short}", f"a:c:k:{it.key}", emoji=EmojiID.PENCIL),
            ]
        )
    rows.append([_btn("◁  Назад", "a:content", emoji=EmojiID.BACK)])

    await cast(Message, callback.message).edit_text(
        f"<b>✍ {label}</b>\n\n<i>Жми на строку чтобы посмотреть и отредактировать.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("a:c:k:"))
async def adm_content_view(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    key = callback.data[len("a:c:k:") :]
    item = ITEMS_BY_KEY.get(key)
    if not item:
        await callback.answer("Нет такого ключа", show_alert=True)
        return

    is_over = content_service().is_overridden(key)
    current = content_service()._raw(key)
    placeholders = ", ".join(f"{{{p}}}" for p in item.placeholders) or "—"

    import html

    safe_current = html.escape(current)
    safe_default = html.escape(item.default)

    text = (
        f"<b>✍ {key}</b>\n\n"
        f"<b>Раздел:</b> <code>{item.category}</code>\n"
        f"<b>Описание:</b> <i>{item.description or '—'}</i>\n"
        f"<b>Плейсхолдеры:</b> <code>{placeholders}</code>\n"
        f"<b>Сейчас:</b> {'изменено' if is_over else 'по умолчанию'}\n\n"
        f"<b>Текущий текст:</b>\n"
        f"<pre>{safe_current[:600]}</pre>"
    )
    if is_over:
        text += f"\n<b>Дефолтный:</b>\n<pre>{safe_default[:600]}</pre>"

    rows = [
        [_btn("Изменить", f"a:c:edit:{key}", emoji=EmojiID.PENCIL)],
    ]
    if is_over:
        rows.append([_btn("Сбросить к дефолту", f"a:c:reset:{key}", emoji=EmojiID.TRASH)])
    rows.append([_btn("◁  Назад", f"a:c:cat:{item.category}", emoji=EmojiID.BACK)])

    await cast(Message, callback.message).edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("a:c:edit:"))
async def adm_content_edit_prompt(callback: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    key = callback.data[len("a:c:edit:") :]
    item = ITEMS_BY_KEY.get(key)
    if not item:
        await callback.answer()
        return

    placeholders = ", ".join(f"{{{p}}}" for p in item.placeholders) or "нет"
    await state.set_state(ContentEditSG.waiting_value)
    await state.update_data(content_key=key)
    await cast(Message, callback.message).answer(
        f"<b>✍ Новый текст для <code>{key}</code></b>\n\n"
        f"<i>Плейсхолдеры: {placeholders}\n"
        f"HTML-теги поддерживаются. Пришли .</i>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(ContentEditSG.waiting_value, F.text)
async def adm_content_edit_save(message: Message, state: FSMContext, cfg: Config) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    data = await state.get_data()
    key = data.get("content_key")
    if not key:
        await message.reply("❌ Потерял ключ")
        return
    new_value = message.html_text or message.text  # preserve HTML formatting if user used it

    await content_service().set_override(key, new_value)
    await state.clear()
    await message.reply(
        f"✅ <b>Обновлено</b> <code>{key}</code>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("a:c:reset:"))
async def adm_content_reset(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    key = callback.data[len("a:c:reset:") :]
    await content_service().clear_override(key)
    await callback.answer("Сброшено к дефолту")
    callback.data = f"a:c:k:{key}"
    await adm_content_view(callback, cfg)
