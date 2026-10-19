"""Settings handlers: bg colour, resolution, format, media, emoji colour."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import cast

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PhotoSize

from config import Config
from db import DB
from keyboards.cb import CB
from keyboards.inline import (
    kb_back,
    kb_bg_picker,
    kb_emoji_color,
    kb_format,
    kb_media,
    kb_resolution,
)
from services.color_changer import parse_hex, rgb_to_hex
from services.content import t
from services.screens import S, edit_screen, safe_edit
from states import (
    BgColorSG,
    EmojiColorSG,
    GifSG,
    MediaSG,
    ResolutionSG,
    job_store,
)
from utils.emoji import E, EmojiID

router = Router(name="settings")


@router.callback_query(F.data == CB.BG_COLOR)
async def open_bg(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    text = t("bg.text", current=job.cfg.bg_color)
    await edit_screen(
        cast(Message, callback.message), db, S.BG_COLOR, text, keyboard=kb_bg_picker()
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.BG_PRESET))
async def bg_preset(callback: CallbackQuery, db: DB, cfg: Config, billing) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    hex_part = callback.data[len(CB.BG_PRESET) :]
    rgb = parse_hex(hex_part)
    if not rgb:
        await callback.answer("Некорректный цвет", show_alert=True)
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.bg_color = rgb_to_hex(rgb).upper()
    await callback.answer(f"Фон: {job.cfg.bg_color}")
    await open_bg(callback, None, db)  # type: ignore[arg-type]


@router.callback_query(F.data == CB.BG_CUSTOM)
async def bg_custom_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(BgColorSG.waiting_hex)
    await safe_edit(
        cast(Message, callback.message),
        t("bg.prompt"),
        keyboard=kb_back(CB.BG_COLOR),
    )
    await callback.answer()


@router.message(BgColorSG.waiting_hex, F.text)
async def bg_custom_hex(message: Message, state: FSMContext, db: DB) -> None:
    if message.from_user is None or message.text is None:
        return
    rgb = parse_hex(message.text)
    if not rgb:
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Не похоже на HEX. "
            f"Пример: <code>#FF3344</code></b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    job = job_store.get(message.from_user.id)
    job.cfg.bg_color = rgb_to_hex(rgb).upper()
    await state.set_state(GifSG.idle)
    await message.reply(
        f"<blockquote>{E.CHECK} <b>Фон: <code>{job.cfg.bg_color}</code></b></blockquote>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == CB.RESOLUTION)
async def open_res(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    text = t("res.text", w=job.cfg.width, h=job.cfg.height)
    await edit_screen(
        cast(Message, callback.message), db, S.RESOLUTION, text, keyboard=kb_resolution()
    )
    await callback.answer()


@router.callback_query(F.data == CB.RES_CUSTOM)
async def res_custom_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(ResolutionSG.waiting_custom)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await safe_edit(
        cast(Message, callback.message),
        '<tg-emoji emoji-id="5778479949572738874">↔️</tg-emoji> '
        "<b>Введи разрешение</b>\n"
        "<blockquote><b>Формат: 1920x530 или 1920×530</b></blockquote>",
        keyboard=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◁  Назад",
                        callback_data=CB.RESOLUTION,
                        icon_custom_emoji_id=EmojiID.BACK,
                    ),
                ]
            ]
        ),
    )
    await callback.answer()


@router.message(ResolutionSG.waiting_custom, F.text)
async def res_custom_set(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    import re

    m = re.match(r"(\d+)\s*[x×хХ]\s*(\d+)", message.text.strip())
    if not m:
        await message.reply(
            "<blockquote>"
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Формат: 1920x530 или 1920×530</b>"
            "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    w, h = int(m.group(1)), int(m.group(2))
    if w < 100 or h < 100 or w > 7680 or h > 7680:
        await message.reply(
            "<blockquote>"
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Разрешение вне допустимого диапазона (100–7680)</b>"
            "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    # libx264 requires even dimensions.
    w = w if w % 2 == 0 else w - 1
    h = h if h % 2 == 0 else h - 1
    job = job_store.get(message.from_user.id)
    job.cfg.width, job.cfg.height = w, h
    await state.set_state(GifSG.idle)
    await message.reply(
        f"<blockquote>"
        f'<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        f"<b>Разрешение: <code>{w}×{h}</code></b>"
        f"</blockquote>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == CB.FORMAT)
async def open_fmt(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    text = t("fmt.text")
    await edit_screen(
        cast(Message, callback.message), db, S.FORMAT, text, keyboard=kb_format(job.cfg.fmt)
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.FMT_SET))
async def fmt_set(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    fmt = callback.data[len(CB.FMT_SET) :]
    if fmt not in ("mp4", "gif"):
        await callback.answer()
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.fmt = fmt  # type: ignore[assignment]
    await callback.answer(f"Формат: {fmt.upper()}")
    await open_fmt(callback, db)


async def _send_media_screen(
    callback: CallbackQuery,
    db: DB,
    *,
    prefer_attach: bool = True,
) -> None:
    """Render the media screen. If user has media set, send the user's
    media as the message attachment so they can see what's chosen."""
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    has = job.bg_kind != "none"
    text = (
        "<blockquote>"
        '<tg-emoji emoji-id="5904340783611254165">🖼</tg-emoji> '
        "<b>Своя медиа — для рендера</b>"
        "</blockquote>"
    )
    msg = cast(Message, callback.message)

    if prefer_attach and has and job.bg_path and job.bg_path.exists():
        from aiogram.types import FSInputFile

        with contextlib.suppress(Exception):
            await msg.delete()
        if job.bg_kind == "video":
            await msg.bot.send_video(
                chat_id=msg.chat.id,
                video=FSInputFile(job.bg_path),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_media(has_media=True),
            )
        else:
            await msg.bot.send_photo(
                chat_id=msg.chat.id,
                photo=FSInputFile(job.bg_path),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_media(has_media=True),
            )
        return

    await edit_screen(msg, db, S.MEDIA, text, keyboard=kb_media(has_media=has))


@router.callback_query(F.data == CB.MEDIA)
async def open_media(callback: CallbackQuery, db: DB) -> None:
    await _send_media_screen(callback, db, prefer_attach=True)
    await callback.answer()


@router.callback_query(F.data == CB.MEDIA_UPLOAD)
async def media_upload_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(MediaSG.waiting_media)
    await safe_edit(
        cast(Message, callback.message),
        t("media.prompt"),
        keyboard=kb_back(),
    )
    await callback.answer()


@router.callback_query(F.data == CB.MEDIA_DELETE)
async def media_delete(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None:
        return
    job = job_store.get(callback.from_user.id)
    job.cleanup_bg()
    await callback.answer("Медиа убрана")
    await _send_media_screen(callback, db, prefer_attach=False)


@router.message(MediaSG.waiting_media, F.photo)
async def media_photo(message: Message, state: FSMContext, bot: Bot, cfg: Config, db: DB) -> None:
    if message.from_user is None or not message.photo:
        return
    photo: PhotoSize = max(message.photo, key=lambda p: (p.width or 0) * (p.height or 0))
    job = job_store.get(message.from_user.id)
    job.cleanup_bg()
    target = Path("data/tmp") / f"bg_{message.from_user.id}_{photo.file_unique_id}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    await bot.download(photo, destination=target)
    job.bg_path, job.bg_kind = target, "image"
    await state.set_state(GifSG.idle)
    await message.reply(
        f"<blockquote>{E.CHECK} <b>Медиа принята.</b></blockquote>", parse_mode=ParseMode.HTML
    )


@router.message(MediaSG.waiting_media, F.video | F.animation)
async def media_video(message: Message, state: FSMContext, bot: Bot, cfg: Config, db: DB) -> None:
    if message.from_user is None:
        return
    media = message.video or message.animation
    if media is None:
        return
    if media.file_size and media.file_size > cfg.max_bg_size_mb * 1024 * 1024:
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Слишком большой файл "
            f"(лимит {cfg.max_bg_size_mb} МБ).</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    job = job_store.get(message.from_user.id)
    job.cleanup_bg()
    target = Path("data/tmp") / f"bg_{message.from_user.id}_{media.file_unique_id}.mp4"
    await bot.download(media, destination=target)
    job.bg_path, job.bg_kind = target, "video"
    await state.set_state(GifSG.idle)
    await message.reply(
        f"<blockquote>{E.CHECK} <b>Медиа принята (видео).</b></blockquote>",
        parse_mode=ParseMode.HTML,
    )


@router.message(MediaSG.waiting_media, F.document)
async def media_document(
    message: Message, state: FSMContext, bot: Bot, cfg: Config, db: DB
) -> None:
    if message.from_user is None or message.document is None:
        return
    doc = message.document
    name = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    is_image = mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp"))
    is_video = mime.startswith("video/") or name.endswith((".mp4", ".mov", ".webm", ".gif"))
    if not (is_image or is_video):
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Не подходит формат.</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    if doc.file_size and doc.file_size > cfg.max_bg_size_mb * 1024 * 1024:
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Лимит {cfg.max_bg_size_mb} МБ.</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    job = job_store.get(message.from_user.id)
    job.cleanup_bg()
    suffix = ".mp4" if is_video else ".jpg"
    target = Path("data/tmp") / f"bg_{message.from_user.id}_{doc.file_unique_id}{suffix}"
    await bot.download(doc, destination=target)
    job.bg_path, job.bg_kind = target, "video" if is_video else "image"
    await state.set_state(GifSG.idle)
    await message.reply(
        f"<blockquote>{E.CHECK} <b>Медиа принята.</b></blockquote>", parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == CB.EMOJI_COLOR)
async def open_emc(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    job = job_store.get(callback.from_user.id)
    has = job.cfg.emoji_color_mode == "tint" and bool(job.cfg.emoji_color)
    # The palette button is already visible, so start accepting HEX input right away.
    await state.set_state(EmojiColorSG.waiting_hex)
    text = t("emoji.text")
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.EMOJI_COLOR,
        text,
        keyboard=kb_emoji_color(has_color=has),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.EMC_PRESET))
async def emc_preset(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    rgb = parse_hex(callback.data[len(CB.EMC_PRESET) :])
    if not rgb:
        await callback.answer()
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.emoji_color = rgb_to_hex(rgb).upper()
    job.cfg.emoji_color_mode = "tint"
    await callback.answer(f"Цвет: {job.cfg.emoji_color}")
    await open_emc(callback, state, db)


@router.message(EmojiColorSG.waiting_hex, F.text)
async def emc_custom_hex(message: Message, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return
    rgb = parse_hex(message.text)
    if not rgb:
        await message.reply(t("errors.bad_hex"), parse_mode=ParseMode.HTML)
        return
    job = job_store.get(message.from_user.id)
    job.cfg.emoji_color = rgb_to_hex(rgb).upper()
    job.cfg.emoji_color_mode = "tint"
    await state.set_state(GifSG.idle)
    await message.reply(t("emoji.applied", hex=job.cfg.emoji_color), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == CB.EMC_OFF)
async def emc_off(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None:
        return
    job = job_store.get(callback.from_user.id)
    job.cfg.emoji_color = None
    job.cfg.emoji_color_mode = "off"
    await callback.answer("Покраска выключена")
    await open_emc(callback, state, db)
