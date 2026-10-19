"""Multi-step broadcast: text → media → button → preview → send."""

from __future__ import annotations

import asyncio
import contextlib
from typing import cast

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from db import DB
from keyboards.cb import CB
from keyboards.inline import kb_admin_home
from states import AdminSG, BroadcastSG
from utils.logger import get_logger

router = Router(name="admin_broadcast")
log = get_logger("h.broadcast")


def _is_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.admin_ids


def _build_btn_markup(
    btn_text: str | None,
    btn_url: str | None,
    btn_emoji_id: str | None,
) -> InlineKeyboardMarkup | None:
    if (not btn_text and not btn_emoji_id) or not btn_url:
        return None
    text = btn_text or "·"
    btn = InlineKeyboardButton(text=text, url=btn_url)
    if btn_emoji_id:
        btn.icon_custom_emoji_id = btn_emoji_id
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Отмена",
                    callback_data=CB.ADM_HOME,
                    icon_custom_emoji_id="5256999125634000597",
                ),
            ]
        ]
    )


@router.callback_query(F.data == CB.ADM_BROADCAST)
async def bc_start(callback: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _is_admin(callback.from_user.id, cfg):
        return
    await state.set_state(BroadcastSG.waiting_text)
    msg = await cast(Message, callback.message).edit_text(
        "<blockquote>"
        '<tg-emoji emoji-id="5776182936638329359">📨</tg-emoji> '
        "<b>Шаг 1: Отправь текст сообщения для рассылки</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_kb_cancel(),
    )
    await state.update_data(step1_msg_id=msg.message_id)
    await callback.answer()


@router.message(BroadcastSG.waiting_text)
async def bc_receive_text(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, cfg):
        return
    data = await state.get_data()
    for mid_key in ("step1_msg_id",):
        mid = data.get(mid_key)
        if mid:
            with contextlib.suppress(Exception):
                await bot.delete_message(message.chat.id, mid)
    with contextlib.suppress(Exception):
        await message.delete()

    await state.update_data(
        bc_text=message.text or message.caption,
        bc_entities=message.entities or message.caption_entities,
    )
    await state.set_state(BroadcastSG.waiting_media)
    step_msg = await bot.send_message(
        message.chat.id,
        "<blockquote>"
        '<tg-emoji emoji-id="5776182936638329359">📨</tg-emoji> '
        "<b>Шаг 2: Отправь медиа (фото, видео, GIF) [или] нажми «Пропустить»</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пропустить",
                        callback_data="bc:skip_media",
                        icon_custom_emoji_id="6037622221625626773",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◁  Отмена",
                        callback_data=CB.ADM_HOME,
                        icon_custom_emoji_id="5256999125634000597",
                    )
                ],
            ]
        ),
    )
    await state.update_data(step2_msg_id=step_msg.message_id)


@router.message(BroadcastSG.waiting_media, F.photo | F.video | F.animation)
async def bc_receive_media(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, cfg):
        return
    data = await state.get_data()
    mid = data.get("step2_msg_id")
    if mid:
        with contextlib.suppress(Exception):
            await bot.delete_message(message.chat.id, mid)
    with contextlib.suppress(Exception):
        await message.delete()

    if message.photo:
        await state.update_data(
            bc_photo=message.photo[-1].file_id,
            bc_video=None,
            bc_animation=None,
        )
    elif message.video:
        await state.update_data(
            bc_photo=None,
            bc_video=message.video.file_id,
            bc_animation=None,
        )
    elif message.animation:
        await state.update_data(
            bc_photo=None,
            bc_video=None,
            bc_animation=message.animation.file_id,
        )

    await _ask_button(message.chat.id, state, bot)


@router.callback_query(F.data == "bc:skip_media", BroadcastSG.waiting_media)
async def bc_skip_media(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _is_admin(callback.from_user.id, cfg):
        return
    data = await state.get_data()
    mid = data.get("step2_msg_id")
    if mid:
        with contextlib.suppress(Exception):
            await bot.delete_message(callback.message.chat.id, mid)
    await state.update_data(bc_photo=None, bc_video=None, bc_animation=None)
    await _ask_button(callback.message.chat.id, state, bot)
    await callback.answer()


async def _ask_button(chat_id: int, state: FSMContext, bot: Bot) -> None:
    await state.set_state(BroadcastSG.waiting_btn_text)
    step_msg = await bot.send_message(
        chat_id,
        "<blockquote>"
        '<tg-emoji emoji-id="5776182936638329359">📨</tg-emoji> '
        "<b>Шаг 3: Добавить кнопку со ссылкой?</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Добавить кнопку",
                        callback_data="bc:add_button",
                        icon_custom_emoji_id="5778455936410588193",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Без кнопки",
                        callback_data="bc:skip_button",
                        icon_custom_emoji_id="6037622221625626773",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◁  Отмена",
                        callback_data=CB.ADM_HOME,
                        icon_custom_emoji_id="5256999125634000597",
                    )
                ],
            ]
        ),
    )
    await state.update_data(step3_msg_id=step_msg.message_id)


@router.callback_query(F.data == "bc:add_button", BroadcastSG.waiting_btn_text)
async def bc_add_button(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _is_admin(callback.from_user.id, cfg):
        return
    data = await state.get_data()
    mid = data.get("step3_msg_id")
    if mid:
        with contextlib.suppress(Exception):
            await bot.delete_message(callback.message.chat.id, mid)
    step_msg = await bot.send_message(
        callback.message.chat.id,
        "<blockquote>"
        '<tg-emoji emoji-id="5776182936638329359">📨</tg-emoji> '
        "<b>Шаг 4: Введи текст кнопки</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_kb_cancel(),
    )
    await state.update_data(step4_msg_id=step_msg.message_id)
    await callback.answer()


@router.message(BroadcastSG.waiting_btn_text)
async def bc_receive_btn_text(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, cfg):
        return
    data = await state.get_data()

    btn_text = message.text or ""
    btn_emoji_id: str | None = None

    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji" and entity.custom_emoji_id:
                btn_emoji_id = entity.custom_emoji_id
                btn_text = (
                    btn_text[: entity.offset] + btn_text[entity.offset + entity.length :]
                ).strip()
                break

    if not btn_text and not btn_emoji_id:
        err = await message.answer(
            "<blockquote>"
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Текст кнопки не может быть пустым</b>"
            "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        await state.update_data(step4_err_id=err.message_id)
        return

    prev_err = data.get("step4_err_id")
    if prev_err:
        with contextlib.suppress(Exception):
            await bot.delete_message(message.chat.id, prev_err)

    await state.update_data(bc_btn_text=btn_text, bc_btn_emoji_id=btn_emoji_id)

    mid = data.get("step4_msg_id")
    if mid:
        with contextlib.suppress(Exception):
            await bot.delete_message(message.chat.id, mid)
    with contextlib.suppress(Exception):
        await message.delete()

    await state.set_state(BroadcastSG.waiting_btn_url)
    step_msg = await bot.send_message(
        message.chat.id,
        "<blockquote>"
        '<tg-emoji emoji-id="5776182936638329359">📨</tg-emoji> '
        "<b>Шаг 5: Введи URL для кнопки</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_kb_cancel(),
    )
    await state.update_data(step5_msg_id=step_msg.message_id)


@router.message(BroadcastSG.waiting_btn_url)
async def bc_receive_btn_url(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id, cfg):
        return
    data = await state.get_data()
    mid = data.get("step5_msg_id")
    if mid:
        with contextlib.suppress(Exception):
            await bot.delete_message(message.chat.id, mid)
    with contextlib.suppress(Exception):
        await message.delete()
    await state.update_data(bc_btn_url=message.text)
    await _show_preview(message.chat.id, state, bot)


@router.callback_query(F.data == "bc:skip_button", BroadcastSG.waiting_btn_text)
async def bc_skip_button(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _is_admin(callback.from_user.id, cfg):
        return
    data = await state.get_data()
    mid = data.get("step3_msg_id")
    if mid:
        with contextlib.suppress(Exception):
            await bot.delete_message(callback.message.chat.id, mid)
    await state.update_data(bc_btn_text=None, bc_btn_url=None, bc_btn_emoji_id=None)
    await _show_preview(callback.message.chat.id, state, bot)
    await callback.answer()


async def _show_preview(chat_id: int, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    bc_text = data.get("bc_text")
    bc_entities = data.get("bc_entities")
    bc_photo = data.get("bc_photo")
    bc_video = data.get("bc_video")
    bc_animation = data.get("bc_animation")
    btn_text = data.get("bc_btn_text")
    btn_url = data.get("bc_btn_url")
    btn_emoji_id = data.get("bc_btn_emoji_id")

    reply_markup = _build_btn_markup(btn_text, btn_url, btn_emoji_id)

    header = await bot.send_message(
        chat_id,
        "<blockquote>"
        '<tg-emoji emoji-id="6037397706505195857">👁</tg-emoji> '
        "<b>Предпросмотр рассылки:</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
    )

    if bc_photo:
        preview = await bot.send_photo(
            chat_id,
            bc_photo,
            caption=bc_text,
            caption_entities=bc_entities,
            reply_markup=reply_markup,
            parse_mode=None,
        )
    elif bc_video:
        preview = await bot.send_video(
            chat_id,
            bc_video,
            caption=bc_text,
            caption_entities=bc_entities,
            reply_markup=reply_markup,
            parse_mode=None,
        )
    elif bc_animation:
        preview = await bot.send_animation(
            chat_id,
            bc_animation,
            caption=bc_text,
            caption_entities=bc_entities,
            reply_markup=reply_markup,
            parse_mode=None,
        )
    else:
        preview = await bot.send_message(
            chat_id,
            bc_text or " ",
            entities=bc_entities,
            reply_markup=reply_markup,
            parse_mode=None,
        )

    confirm_msg = await bot.send_message(
        chat_id,
        "<blockquote>"
        '<tg-emoji emoji-id="6030848053177486888">❓</tg-emoji> '
        "<b>Отправить рассылку?</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отправить",
                        callback_data="bc:confirm",
                        icon_custom_emoji_id="5870633910337015697",
                    ),
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data="bc:cancel",
                        icon_custom_emoji_id="5870657884844462243",
                    ),
                ]
            ]
        ),
    )
    await state.update_data(
        preview_header_id=header.message_id,
        preview_msg_id=preview.message_id,
        confirm_msg_id=confirm_msg.message_id,
    )


@router.callback_query(F.data == "bc:confirm")
async def bc_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _is_admin(callback.from_user.id, cfg):
        return
    data = await state.get_data()
    chat_id = callback.message.chat.id

    for mid in (
        data.get("confirm_msg_id"),
        data.get("preview_msg_id"),
        data.get("preview_header_id"),
    ):
        if mid:
            with contextlib.suppress(Exception):
                await bot.delete_message(chat_id, mid)

    bc_text = data.get("bc_text")
    bc_entities = data.get("bc_entities")
    bc_photo = data.get("bc_photo")
    bc_video = data.get("bc_video")
    bc_animation = data.get("bc_animation")
    btn_text = data.get("bc_btn_text")
    btn_url = data.get("bc_btn_url")
    btn_emoji_id = data.get("bc_btn_emoji_id")
    await state.clear()
    await state.set_state(AdminSG.idle)

    reply_markup = _build_btn_markup(btn_text, btn_url, btn_emoji_id)
    user_ids = await db.all_user_ids()
    success = failed = 0

    status_msg = await bot.send_message(
        chat_id,
        "<blockquote>"
        '<tg-emoji emoji-id="6039573425268201570">📤</tg-emoji> '
        f"<b>Рассылка начата… 0/{len(user_ids)}</b>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
    )

    for i, uid in enumerate(user_ids, 1):
        try:
            if bc_photo:
                await bot.send_photo(
                    uid,
                    bc_photo,
                    caption=bc_text,
                    caption_entities=bc_entities,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            elif bc_video:
                await bot.send_video(
                    uid,
                    bc_video,
                    caption=bc_text,
                    caption_entities=bc_entities,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            elif bc_animation:
                await bot.send_animation(
                    uid,
                    bc_animation,
                    caption=bc_text,
                    caption_entities=bc_entities,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            else:
                await bot.send_message(
                    uid,
                    bc_text or " ",
                    entities=bc_entities,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
        if i % 25 == 0:
            with contextlib.suppress(Exception):
                await status_msg.edit_text(
                    "<blockquote>"
                    '<tg-emoji emoji-id="6039573425268201570">📤</tg-emoji> '
                    f"<b>Отправлено {i}/{len(user_ids)}…</b>"
                    "</blockquote>",
                    parse_mode=ParseMode.HTML,
                )

    try:
        await status_msg.edit_text(
            '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
            "<b>Рассылка завершена</b>\n"
            "<blockquote>"
            f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
            f"<b>Успешно: {success}</b>\n"
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f"<b>Ошибок: {failed}</b>"
            "</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="◁  Назад",
                            callback_data=CB.ADM_HOME,
                            icon_custom_emoji_id="5256999125634000597",
                        )
                    ]
                ]
            ),
        )
    except Exception:
        log.exception("failed to update broadcast status")
    await callback.answer()


@router.callback_query(F.data == "bc:cancel")
async def bc_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _is_admin(callback.from_user.id, cfg):
        return
    data = await state.get_data()
    chat_id = callback.message.chat.id
    for mid in (
        data.get("confirm_msg_id"),
        data.get("preview_msg_id"),
        data.get("preview_header_id"),
    ):
        if mid:
            with contextlib.suppress(Exception):
                await bot.delete_message(chat_id, mid)
    await state.clear()
    await state.set_state(AdminSG.idle)
    from services.content import t

    await bot.send_message(
        chat_id,
        t("admin.home"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin_home(),
    )
    await callback.answer()
