"""Watermark settings: text, font, colour, position."""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from db import DB
from keyboards.cb import CB
from services.color_changer import parse_hex, rgb_to_hex
from services.content import t
from services.screens import S, edit_screen, safe_edit
from services.watermark import _gfont_path
from states import GifSG, WatermarkSG, job_store
from utils.emoji import EmojiID

router = Router(name="watermark")

_GOOGLE_FONTS_URL = "https://fonts.google.com/"
_COLOR_PICKER_URL = "https://htmlcolorcodes.com/"

_POSITIONS = [
    ("Лево сверху", "tl"),
    ("Право сверху", "tr"),
    ("Лево снизу", "bl"),
    ("Право снизу", "br"),
]
_POS_LABELS = {k: v for v, k in _POSITIONS}
_POS_LABELS["center"] = "Центр"


def _wm_main_text(job) -> str:
    wm = job.cfg.watermark
    if not wm.text:
        return "<blockquote><b>Вотермарка не задана. Создайте название чтобы включить.</b></blockquote>"
    status = "вкл" if wm.enabled else "выкл"
    pos = _POS_LABELS.get(wm.position, wm.position)
    return (
        f'<tg-emoji emoji-id="5346198045350459046">©</tg-emoji> '
        f"<b>Вотермарка: <code>{wm.text}</code></b>\n\n"
        f"<blockquote>"
        f"<b>Шрифт: <code>{wm.font_key}</code>\n"
        f"Цвет: <code>{wm.color}</code>\n"
        f"Позиция: <code>{pos}</code>\n"
        f"Статус: <code>{status}</code></b>"
        f"</blockquote>"
    )


def _kb_wm_main(*, enabled: bool, has_text: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Название",
                callback_data=CB.WM_TEXT,
                icon_custom_emoji_id="5873094527165665327",
            ),
            InlineKeyboardButton(
                text="Шрифт",
                callback_data="wm:font_screen",
                icon_custom_emoji_id="5873094527165665327",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Цвет",
                callback_data="wm:color_screen",
                icon_custom_emoji_id="5873094527165665327",
            ),
            InlineKeyboardButton(
                text="Позиция",
                callback_data="wm:pos_screen",
                icon_custom_emoji_id="5873094527165665327",
            ),
        ],
    ]
    if has_text:
        toggle_label = t("wm.btn.toggle_off") if enabled else t("wm.btn.toggle_on")
        rows.append(
            [
                InlineKeyboardButton(
                    text=toggle_label,
                    callback_data=CB.WM_TOGGLE,
                    icon_custom_emoji_id="5873094527165665327",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◁  Назад",
                callback_data=CB.GIF_HOME,
                icon_custom_emoji_id=EmojiID.BACK,
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_wm_pos(current: str) -> InlineKeyboardMarkup:
    """Position picker: 2×2 grid, active option highlighted."""
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for label, key in _POSITIONS:
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"{CB.WM_POS}{key}",
            icon_custom_emoji_id=EmojiID.GEO,
        )
        if current == key:
            btn = InlineKeyboardButton(
                text=label,
                callback_data=f"{CB.WM_POS}{key}",
                icon_custom_emoji_id=EmojiID.GEO,
                style="success",
            )
        pair.append(btn)
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [
            InlineKeyboardButton(
                text="◁  Назад",
                callback_data=CB.WATERMARK,
                icon_custom_emoji_id=EmojiID.BACK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_wm_font() -> InlineKeyboardMarkup:
    """Font picker (Google Fonts web app) and Back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть Google Fonts",
                    web_app=WebAppInfo(url=_GOOGLE_FONTS_URL),
                    icon_custom_emoji_id=EmojiID.LINK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.WATERMARK,
                    icon_custom_emoji_id=EmojiID.BACK,
                )
            ],
        ]
    )


def _kb_wm_color() -> InlineKeyboardMarkup:
    """Colour picker web app and Back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подобрать цвет",
                    web_app=WebAppInfo(url=_COLOR_PICKER_URL),
                    icon_custom_emoji_id=EmojiID.BRUSH,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.WATERMARK,
                    icon_custom_emoji_id=EmojiID.BACK,
                )
            ],
        ]
    )


def _kb_emc_color() -> InlineKeyboardMarkup:
    """Emoji colour: picker web app, reset, Back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подобрать цвет",
                    web_app=WebAppInfo(url=_COLOR_PICKER_URL),
                    icon_custom_emoji_id=EmojiID.BRUSH,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Убрать цвет",
                    callback_data=CB.EMC_OFF,
                    icon_custom_emoji_id=EmojiID.TRASH,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.GIF_HOME,
                    icon_custom_emoji_id=EmojiID.BACK,
                )
            ],
        ]
    )


@router.callback_query(F.data == CB.WATERMARK)
async def open_wm(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.WATERMARK,
        _wm_main_text(job),
        keyboard=_kb_wm_main(
            enabled=job.cfg.watermark.enabled,
            has_text=bool(job.cfg.watermark.text),
        ),
    )
    await callback.answer()


# Text
@router.callback_query(F.data == CB.WM_TEXT)
async def wm_text_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(WatermarkSG.waiting_text)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.WATERMARK,
                    icon_custom_emoji_id=EmojiID.BACK,
                )
            ]
        ]
    )
    txt = "<blockquote><b>Введи название вотермарки на любом языке</b></blockquote>"
    await safe_edit(cast(Message, callback.message), txt, keyboard=kb)
    await callback.answer()


@router.message(WatermarkSG.waiting_text, F.text)
async def wm_text_set(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    text = message.text.strip()[:64]
    if not text:
        await message.reply(t("errors.bad_number"), parse_mode=ParseMode.HTML)
        return
    job = job_store.get(message.from_user.id)
    job.cfg.watermark.text = text
    job.cfg.watermark.enabled = True
    await state.set_state(GifSG.idle)
    await message.reply(t("wm.text_set", text=text), parse_mode=ParseMode.HTML)


# Font
@router.callback_query(F.data == "wm:font_screen")
async def wm_font_screen(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(WatermarkSG.waiting_font)
    kb = _kb_wm_font()
    txt = (
        "<blockquote><b>Введи название шрифта как на Google Fonts:\n"
        "Seymour One, Rubik Spray Paint, Montserrat, Press Start 2P</b></blockquote>"
    )
    await safe_edit(cast(Message, callback.message), txt, keyboard=kb)
    await callback.answer()


@router.message(WatermarkSG.waiting_font, F.text)
async def wm_font_set(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    font_name = message.text.strip()[:64]
    if not font_name:
        await message.reply(t("errors.bad_number"), parse_mode=ParseMode.HTML)
        return

    # Resolve immediately so the user finds out *now* if the font name is
    # bogus, instead of silently rendering with the system fallback.
    status = await message.reply(
        f"<blockquote>"
        f'<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        f"<b>Ищу шрифт «{font_name}» на Google Fonts…</b>"
        f"</blockquote>",
        parse_mode=ParseMode.HTML,
    )
    loop = asyncio.get_running_loop()
    path = await loop.run_in_executor(None, _gfont_path, font_name)
    with contextlib.suppress(Exception):
        await status.delete()

    if not path:
        await message.reply(
            f"<blockquote>"
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f"<b>Не нашёл шрифт «{font_name}». "
            f"Проверь название как на Google Fonts.</b>"
            f"</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    job = job_store.get(message.from_user.id)
    job.cfg.watermark.font_key = font_name
    await state.set_state(GifSG.idle)
    await message.reply(t("wm.font_set", font=font_name), parse_mode=ParseMode.HTML)


# Colour
@router.callback_query(F.data == "wm:color_screen")
async def wm_color_screen(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(WatermarkSG.waiting_color)
    kb = _kb_wm_color()
    txt = (
        "<blockquote><b>Введи HEX-цвет вотермарки:\n"
        "FFFFFF — белый\nF2E9E4 — серый\n000000 — чёрный</b></blockquote>"
    )
    await safe_edit(cast(Message, callback.message), txt, keyboard=kb)
    await callback.answer()


@router.message(WatermarkSG.waiting_color, F.text)
async def wm_color_set(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    rgb = parse_hex(message.text)
    if not rgb:
        await message.reply(t("errors.bad_hex"), parse_mode=ParseMode.HTML)
        return
    job = job_store.get(message.from_user.id)
    job.cfg.watermark.color = rgb_to_hex(rgb).upper()
    await state.set_state(GifSG.idle)
    await message.reply(t("wm.color_set", hex=job.cfg.watermark.color), parse_mode=ParseMode.HTML)


# Position
@router.callback_query(F.data == "wm:pos_screen")
async def wm_pos_screen(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    txt = "<blockquote><b>Выбери позицию вотермарки:</b></blockquote>"
    kb = _kb_wm_pos(job.cfg.watermark.position)
    await safe_edit(cast(Message, callback.message), txt, keyboard=kb)
    await callback.answer()


@router.callback_query(F.data.startswith(CB.WM_POS))
async def wm_pos(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.data is None:
        return
    pos = callback.data[len(CB.WM_POS) :]
    if pos not in {"tl", "tr", "bl", "br", "center"}:
        await callback.answer()
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.watermark.position = pos  # type: ignore[assignment]
    await callback.answer(f"Позиция: {_POS_LABELS.get(pos, pos)}")
    await wm_pos_screen(callback, db)


# Toggle / Clear
@router.callback_query(F.data == CB.WM_TOGGLE)
async def wm_toggle(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None:
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.watermark.enabled = not job.cfg.watermark.enabled
    await callback.answer("Включена" if job.cfg.watermark.enabled else "Выключена")
    await open_wm(callback, db)


@router.callback_query(F.data == CB.WM_CLEAR)
async def wm_clear(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None:
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.watermark.text = ""
    job.cfg.watermark.enabled = False
    await callback.answer("Очищено")
    await open_wm(callback, db)


# CB.EMC_OFF itself is handled in settings.py; only the keyboard lives here.
def get_emc_kb() -> InlineKeyboardMarkup:
    return _kb_emc_color()
