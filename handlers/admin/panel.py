"""Admin panel: stats, prices, user balance, screen GIFs, grants.

Style:
    - All texts wrapped in <blockquote><b>...</b></blockquote>
    - Callback handlers use edit_text in place
    - Text-input prompts edit the same anchor message; result clears the
      typed user message and re-edits the anchor.
"""

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
from keyboards.inline import (
    kb_admin_back,
    kb_admin_gif_actions,
    kb_admin_gifs,
    kb_admin_grant,
    kb_admin_notify,
    kb_admin_notify_actions,
    kb_admin_prices,
    kb_admin_user_card,
    kb_admin_users,
)
from services.billing import Billing
from services.content import service as content_service
from services.notify_media import NOTIFY_EVENTS, event_media_key, send_event
from services.notify_texts import credit_text, grant_free_text, grant_rub_text
from services.promo_db import PromoDB
from services.reports_db import ReportsDB
from services.screens import SCREEN_LABELS
from states import AdminSG
from utils.emoji import E, EmojiID
from utils.logger import get_logger

router = Router(name="admin")
log = get_logger("h.admin")


def _ensure_admin(user_id: int, cfg: Config) -> bool:
    return user_id in cfg.admin_ids


async def _edit_or_send(
    msg: Message,
    text: str,
    kb: InlineKeyboardMarkup,
) -> Message:
    try:
        return await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        with contextlib.suppress(Exception):
            await msg.delete()
        return await msg.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _set_anchor_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    kb: InlineKeyboardMarkup,
) -> None:
    """Edit the current admin message into a prompt and remember its id."""
    msg = cast(Message, callback.message)
    edited = await _edit_or_send(msg, text, kb)
    await state.update_data(anchor_msg_id=edited.message_id, anchor_chat_id=edited.chat.id)


async def _finish_prompt(
    message: Message,
    state: FSMContext,
    bot: Bot,
    text: str,
    kb: InlineKeyboardMarkup,
) -> None:
    """After a state-text input, delete user input and edit anchor message."""
    data = await state.get_data()
    anchor_id = data.get("anchor_msg_id")
    chat_id = data.get("anchor_chat_id") or message.chat.id
    with contextlib.suppress(Exception):
        await message.delete()
    if anchor_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=anchor_id,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            await state.set_state(AdminSG.idle)
            return
        except Exception:
            pass
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await state.set_state(AdminSG.idle)


async def _finish_prompt_with_preview(
    message: Message,
    state: FSMContext,
    bot: Bot,
    caption: str,
    kb: InlineKeyboardMarkup,
    file_id: str,
    kind: str,
) -> None:
    """After saving media — delete the user's upload + the old text anchor
    and post the saved media back as the new anchor (with caption + kb).
    This both validates that the file_id actually works (TG will reject
    bad file_ids) and lets the admin see what got saved.

    Falls back to a text-only confirmation if the media send fails.
    """
    data = await state.get_data()
    chat_id = data.get("anchor_chat_id") or message.chat.id
    anchor_id = data.get("anchor_msg_id")
    with contextlib.suppress(Exception):
        await message.delete()
    if anchor_id:
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id, anchor_id)

    common = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
        "reply_markup": kb,
    }
    new_msg: Message | None = None
    try:
        if kind == "animation":
            new_msg = await bot.send_animation(animation=file_id, **common)
        elif kind == "video":
            new_msg = await bot.send_video(video=file_id, **common)
        elif kind == "photo":
            new_msg = await bot.send_photo(photo=file_id, **common)
        else:
            new_msg = await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
    except Exception as exc:
        log.exception("media preview failed kind=%s file_id=%s", kind, file_id)
        warn = (
            f"{caption}\n<blockquote>{E.CROSS} "
            f"<b>Превью не отрисовалось:</b> <code>{exc}</code>\n"
            f"<b>Файл сохранён, но возможно битый — попробуй другой.</b>"
            f"</blockquote>"
        )
        new_msg = await bot.send_message(
            chat_id=chat_id,
            text=warn,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    if new_msg is not None:
        await state.update_data(anchor_msg_id=new_msg.message_id, anchor_chat_id=new_msg.chat.id)
    await state.set_state(AdminSG.idle)


def _classify_media(message: Message) -> tuple[str, str]:
    """Inspect a message and return (file_id, kind). kind ∈
    {animation, video, photo, ""}. Empty kind ⇒ unsupported.

    Handles all common ways a GIF can arrive in TG: native animation,
    .gif uploaded as Document (mime image/gif), .mp4 GIF, regular video,
    photo. Stickers are intentionally not supported — TG can't render
    them as a cover with caption."""
    if message.animation:
        return message.animation.file_id, "animation"
    if message.video:
        return message.video.file_id, "video"
    if message.photo:
        return message.photo[-1].file_id, "photo"
    if message.document:
        mime = (message.document.mime_type or "").lower()
        name = (message.document.file_name or "").lower()
        if "gif" in mime or name.endswith(".gif"):
            return message.document.file_id, "animation"
        if mime.startswith("video/") or name.endswith((".mp4", ".webm", ".mov")):
            return message.document.file_id, "animation"
        if mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            return message.document.file_id, "photo"
        # Unknown mime/filename — default to animation so it at least tries
        # to play (TG will fall back to gallery preview if it can't).
        return message.document.file_id, "animation"
    return "", ""


@router.callback_query(F.data == CB.ADM_STATS)
async def adm_stats(callback: CallbackQuery, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    s = await db.stats()
    text = (
        f"{E.STATS} <b>Статистика</b>\n"
        f"<blockquote>"
        f"{E.PEOPLE} <b>Пользователи</b>\n"
        f"<b>Всего:</b> <code>{s['users_total']}</code>\n"
        f"<b>За сутки:</b> <code>{s['users_today']}</code>"
        f"</blockquote>\n"
        f"<blockquote>"
        f"{E.GROWTH} <b>Рендеры</b>\n"
        f"<b>Всего:</b> <code>{s['renders_total']}</code>\n"
        f"<b>За сутки:</b> <code>{s['renders_today']}</code>\n"
        f"<b>За неделю:</b> <code>{s['renders_week']}</code>\n"
        f"<b>За месяц:</b> <code>{s['renders_month']}</code>"
        f"</blockquote>\n"
        f"<blockquote>"
        f"{E.MONEY} <b>Выручка</b>\n"
        f"<b>Всего:</b> <code>{s['revenue_total_kop'] / 100:.0f}₽</code>\n"
        f"<b>За сутки:</b> <code>{s['revenue_today_kop'] / 100:.0f}₽</code>\n"
        f"<b>За месяц:</b> <code>{s['revenue_month_kop'] / 100:.0f}₽</code>"
        f"</blockquote>\n"
        f"<blockquote>"
        f"{E.WALLET} <b>На балансах:</b> "
        f"<code>{s['balance_total_kop'] / 100:.0f}₽</code>"
        f"</blockquote>"
    )
    await _edit_or_send(cast(Message, callback.message), text, kb_admin_back())
    await callback.answer()


@router.callback_query(F.data == CB.ADM_USERS)
async def adm_users(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    text = (
        f"{E.PEOPLE} <b>Пользователи</b>\n"
        f"<blockquote>"
        f"<b>Найди пользователя по ID или @username — "
        f"посмотри карточку и поправь баланс.</b>"
        f"</blockquote>"
    )
    await _edit_or_send(cast(Message, callback.message), text, kb_admin_users())
    await callback.answer()


@router.callback_query(F.data == CB.ADM_USER_FIND)
async def adm_user_find(callback: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_user_id_balance)
    text = (
        f"{E.WRITE} <b>Поиск пользователя</b>\n"
        f"<blockquote><b>Введи user_id [или] @username</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_user_id_balance, F.text)
async def adm_user_lookup(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    q = message.text.strip().lstrip("@")
    user_id = None
    if q.lstrip("-").isdigit():
        user_id = int(q)
    else:
        cur = await db.conn.execute(
            "SELECT user_id FROM users WHERE LOWER(username)=LOWER(?)", (q,)
        )
        row = await cur.fetchone()
        if row:
            user_id = row["user_id"]

    if user_id is None:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Пользователь не найден</b></blockquote>",
            kb_admin_back(),
        )
        return

    user = await db.get_user(user_id)
    if not user:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Нет в базе</b></blockquote>",
            kb_admin_back(),
        )
        return

    text = (
        f"{E.PEOPLE} <b>Карточка пользователя</b>\n"
        f"<blockquote>"
        f"<b>ID:</b> <code>{user['user_id']}</code>\n"
        f"<b>Username:</b> <code>@{user['username'] or '—'}</code>\n"
        f"<b>Баланс:</b> <code>{user['balance_kop'] / 100:.2f}₽</code>\n"
        f"<b>Бесплатных:</b> <code>{user['free_renders']}</code>\n"
        f"<b>Рендеров:</b> <code>{user['total_renders']}</code>\n"
        f"<b>Потрачено:</b> <code>{user['total_spent_kop'] / 100:.2f}₽</code>"
        f"</blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_user_card(user_id))


@router.callback_query(F.data.startswith("adm:bal:"))
async def adm_balance_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    try:
        target_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    await state.set_state(AdminSG.waiting_balance_delta)
    await state.update_data(target_user_id=target_id)
    text = (
        f"{E.WRITE} <b>Изменение баланса</b>\n"
        f"<blockquote>"
        f"<b>Введи число со знаком:</b>\n"
        f"<b>+100 — начислить, -50 — списать.</b>"
        f"</blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_balance_delta, F.text)
async def adm_balance_apply(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
    bot: Bot,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    txt = message.text.strip().replace(" ", "")
    try:
        delta_rub = int(txt)
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return

    data = await state.get_data()
    target_id = int(data.get("target_user_id", 0))
    if not target_id:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Потерял id</b></blockquote>",
            kb_admin_back(),
        )
        return

    new_balance = await db.add_balance_kop(target_id, delta_rub * 100)
    text = (
        f"{E.CHECK} <b>Баланс изменён</b>\n"
        f"<blockquote>"
        f"<b>User:</b> <code>{target_id}</code>\n"
        f"<b>Δ:</b> <code>{delta_rub:+d}₽</code>\n"
        f"<b>Текущий:</b> <code>{new_balance / 100:.2f}₽</code>"
        f"</blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())

    # Notify the user with a random text variant plus any admin-attached
    # media for the "balance_credited" event.
    try:
        if delta_rub > 0:
            await send_event(
                bot,
                db,
                target_id,
                "balance_credited",
                credit_text(delta_rub, new_balance / 100),
            )
    except Exception:
        pass


@router.callback_query(F.data == CB.ADM_PRICES)
async def adm_prices(
    callback: CallbackQuery,
    db: DB,
    cfg: Config,
    billing: Billing,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    price = await billing.get_price_rub()
    pack_small = await billing.get_pack_small_price_rub()
    pack_full = await billing.get_pack_full_price_rub()
    pack_passport = await billing.get_pack_passport_price_rub()
    free = await billing.get_free_default()
    text = (
        f"{E.MONEY} <b>Цены</b>\n"
        f"<blockquote>"
        f"<b>Рендер:</b> <code>{price}₽</code>\n"
        f"<b>Пак ≤20 тайлов:</b> <code>{pack_small}₽</code>\n"
        f"<b>Пак >20 тайлов:</b> <code>{pack_full}₽</code>\n"
        f"<b>Доплата за паспорт:</b> <code>{pack_passport}₽</code>\n"
        f"<b>Бесплатных при старте:</b> <code>{free}</code>"
        f"</blockquote>"
    )
    await _edit_or_send(cast(Message, callback.message), text, kb_admin_prices())
    await callback.answer()


@router.callback_query(F.data == CB.ADM_PRICE_SET)
async def adm_price_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_price)
    text = (
        f"{E.WRITE} <b>Цена рендера</b>\n"
        f"<blockquote><b>Введи новую цену рендера в ₽</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_price, F.text)
async def adm_price_apply(
    message: Message,
    state: FSMContext,
    bot: Bot,
    billing: Billing,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        v = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    if v < 1:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Минимум 1</b></blockquote>",
            kb_admin_back(),
        )
        return
    await billing.set_price_rub(v)
    text = (
        f"{E.CHECK} <b>Цена обновлена</b>\n"
        f"<blockquote><b>Рендер: <code>{v}₽</code></b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_PACK_PRICE_SET)
async def adm_pack_price_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_pack_price)
    text = (
        f"{E.WRITE} <b>Цена пака ≤20 тайлов</b>\n"
        f"<blockquote><b>Введи новую цену пака с количеством "
        f"тайлов до 20 включительно (₽).</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_pack_price, F.text)
async def adm_pack_price_apply(
    message: Message,
    state: FSMContext,
    bot: Bot,
    billing: Billing,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        v = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    if v < 0:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Минимум 0</b></blockquote>",
            kb_admin_back(),
        )
        return
    # The tiered key is what the mini-app and billing read; the legacy key is
    # mirrored so the in-chat flow charges the same price.
    await billing.set_pack_small_price_rub(v)
    await billing.set_pack_price_rub(v)
    text = (
        f"{E.CHECK} <b>Цена обновлена</b>\n"
        f"<blockquote><b>Пак ≤20 тайлов: <code>{v}₽</code></b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_PACK_LOGO_PRICE_SET)
async def adm_pack_logo_price_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_pack_logo_price)
    text = (
        f"{E.WRITE} <b>Цена пака >20 тайлов</b>\n"
        f"<blockquote><b>Введи новую цену пака, если в нём "
        f"больше 20 тайлов (₽).</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_pack_logo_price, F.text)
async def adm_pack_logo_price_apply(
    message: Message,
    state: FSMContext,
    bot: Bot,
    billing: Billing,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        v = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    if v < 0:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Минимум 0</b></blockquote>",
            kb_admin_back(),
        )
        return
    await billing.set_pack_full_price_rub(v)
    await billing.set_pack_logo_price_rub(v)
    text = (
        f"{E.CHECK} <b>Цена обновлена</b>\n"
        f"<blockquote><b>Пак >20 тайлов: <code>{v}₽</code></b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_PACK_PASSPORT_PRICE_SET)
async def adm_pack_passport_price_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_pack_passport_price)
    text = (
        f"{E.WRITE} <b>Доплата за паспорт</b>\n"
        f"<blockquote><b>Введи сумму доплаты за добавление "
        f"паспорта в пак (₽).</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_pack_passport_price, F.text)
async def adm_pack_passport_price_apply(
    message: Message,
    state: FSMContext,
    bot: Bot,
    billing: Billing,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        v = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    if v < 0:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Минимум 0</b></blockquote>",
            kb_admin_back(),
        )
        return
    await billing.set_pack_passport_price_rub(v)
    text = (
        f"{E.CHECK} <b>Цена обновлена</b>\n"
        f"<blockquote><b>Доплата за паспорт: <code>{v}₽</code></b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_FREE_SET)
async def adm_free_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_free_renders)
    text = (
        f"{E.WRITE} <b>Бесплатные рендеры</b>\n"
        f"<blockquote><b>Сколько давать новым пользователям?</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_free_renders, F.text)
async def adm_free_apply(
    message: Message,
    state: FSMContext,
    bot: Bot,
    billing: Billing,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        v = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    await billing.set_free_default(max(0, v))
    text = (
        f"{E.CHECK} <b>Обновлено</b>\n"
        f"<blockquote><b>Бесплатных при старте: <code>{v}</code></b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_GRANT)
async def adm_grant(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    text = (
        f"{E.GIFT} <b>Подарок всем</b>\n"
        f"<blockquote>"
        f"<b>Начислить всем пользователям бесплатные рендеры [или] ₽.</b>"
        f"</blockquote>"
    )
    await _edit_or_send(cast(Message, callback.message), text, kb_admin_grant())
    await callback.answer()


@router.callback_query(F.data == CB.ADM_GRANT_FREE)
async def adm_grant_free_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_grant_amount)
    await state.update_data(grant_kind="free")
    text = (
        f"{E.WRITE} <b>Подарок всем — рендеры</b>\n"
        f"<blockquote><b>Сколько рендеров начислить каждому?</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.callback_query(F.data == CB.ADM_GRANT_RUB)
async def adm_grant_rub_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_grant_amount)
    await state.update_data(grant_kind="rub")
    text = (
        f"{E.WRITE} <b>Подарок всем — рубли</b>\n"
        f"<blockquote><b>Сколько ₽ начислить каждому?</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_grant_amount, F.text)
async def adm_grant_apply(
    message: Message,
    state: FSMContext,
    db: DB,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        v = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    data = await state.get_data()
    kind = data.get("grant_kind", "free")
    if kind == "free":
        affected = await db.grant_to_all(free=v)
    else:
        affected = await db.grant_to_all(balance_kop=v * 100)

    # Edit anchor → show "starting" status, then update with results
    state_data = await state.get_data()
    anchor_id = state_data.get("anchor_msg_id")
    chat_id = state_data.get("anchor_chat_id") or message.chat.id
    with contextlib.suppress(Exception):
        await message.delete()

    back_kb = kb_admin_back()

    async def _update_anchor(html: str) -> None:
        if anchor_id:
            try:
                await bot.edit_message_text(
                    html,
                    chat_id=chat_id,
                    message_id=anchor_id,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb,
                )
                return
            except Exception:
                pass  # anchor was deleted or is too old to edit; fall back to a new message
        await bot.send_message(chat_id, html, parse_mode=ParseMode.HTML, reply_markup=back_kb)

    await _update_anchor(
        f"{E.CHECK} <b>Начислено всем</b>\n"
        f"<blockquote>"
        f"<b>Затронуто аккаунтов: <code>{affected}</code></b>\n"
        f"<b>Уведомляю пользователей…</b>"
        f"</blockquote>"
    )

    # Best-effort broadcast — re-roll the variant per recipient and
    # attach admin-set notify media (if any) for grant_free / grant_rub.
    user_ids = await db.all_user_ids()
    event_key = "grant_free" if kind == "free" else "grant_rub"
    sent = 0
    for uid in user_ids:
        try:
            text_user = grant_free_text(v) if kind == "free" else grant_rub_text(v)
            await send_event(bot, db, uid, event_key, text_user)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.04)

    await state.set_state(AdminSG.idle)
    await _update_anchor(
        f"{E.CHECK} <b>Подарок выдан</b>\n"
        f"<blockquote>"
        f"<b>Затронуто аккаунтов: <code>{affected}</code></b>\n"
        f"<b>Уведомлены: <code>{sent}/{len(user_ids)}</code></b>"
        f"</blockquote>"
    )


@router.callback_query(F.data == CB.ADM_GIFS)
async def adm_gifs(callback: CallbackQuery, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    media = await db.list_media()
    media_map = {m["screen_key"]: "set" for m in media}
    text = (
        f"{E.MEDIA} <b>GIF на экранах</b>\n"
        f"<blockquote>"
        f"<b>Прикрепи свою анимацию к каждому экрану — "
        f"она будет показана как обложка.</b>"
        f"</blockquote>"
    )
    await _edit_or_send(cast(Message, callback.message), text, kb_admin_gifs(media_map))
    await callback.answer()


@router.callback_query(F.data.startswith(CB.ADM_GIF_SET))
async def adm_gif_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    screen_key = callback.data[len(CB.ADM_GIF_SET) :]
    if screen_key not in SCREEN_LABELS:
        await callback.answer()
        return
    media = await db.get_media(screen_key)
    has = bool(media)
    await state.set_state(AdminSG.waiting_screen_media)
    await state.update_data(screen_key=screen_key)
    body = ""
    if has:
        body = f"<b>Сейчас:</b> <code>{media['kind']}</code>\n"
    text = (
        f"{E.UPLOAD} <b>Экран: {SCREEN_LABELS[screen_key]}</b>\n"
        f"<blockquote>"
        f"{body}"
        f"<b>Пришли GIF, видео [или] фото — оно станет обложкой этого экрана.</b>"
        f"</blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_gif_actions(screen_key, has_media=has))
    await callback.answer()


@router.callback_query(F.data.startswith(CB.ADM_GIF_DEL))
async def adm_gif_del(callback: CallbackQuery, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    screen_key = callback.data[len(CB.ADM_GIF_DEL) :]
    await db.delete_media(screen_key)
    await callback.answer("Удалено")
    await adm_gifs(callback, db, cfg)


@router.message(AdminSG.waiting_screen_media, F.animation | F.video | F.photo | F.document)
async def adm_screen_media_save(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    data = await state.get_data()
    screen_key = data.get("screen_key")
    if not screen_key:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Потерял экран</b></blockquote>",
            kb_admin_back(),
        )
        return

    file_id, kind = _classify_media(message)
    if not file_id:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не понял формат — пришли GIF, видео или фото</b></blockquote>",
            kb_admin_back(),
        )
        return

    await db.set_media(screen_key, file_id, kind)
    caption = (
        f"{E.CHECK} <b>Обложка установлена</b>\n"
        f"<blockquote><b>Экран:</b> {SCREEN_LABELS[screen_key]}\n"
        f"<b>Тип:</b> <code>{kind}</code></blockquote>"
    )
    await _finish_prompt_with_preview(
        message,
        state,
        bot,
        caption,
        kb_admin_back(),
        file_id,
        kind,
    )


@router.callback_query(F.data == "adm:channel:set")
async def adm_channel_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return

    current = await db.get_payment_channel()
    current_str = f"<code>{current}</code>" if current else "не задан"

    await state.set_state(AdminSG.waiting_channel_id)
    text = (
        f"{E.BELL} <b>Канал уведомлений об оплате</b>\n"
        f"<blockquote>"
        f"<b>Сейчас:</b> {current_str}\n"
        f"<b>Перешли боту любое сообщение из нужного канала, "
        f"[или] введи ID канала вручную.</b>\n"
        f"<b>Пример:</b> <code>-1001234567890</code>\n"
        f"<b>Бот должен быть администратором канала!</b>"
        f"</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.ADM_HOME,
                    icon_custom_emoji_id="5256999125634000597",
                )
            ]
        ]
    )
    await _set_anchor_prompt(callback, state, text, kb)
    await callback.answer()


@router.message(AdminSG.waiting_channel_id)
async def adm_channel_save(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
    bot: Bot,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return

    channel_id: int | None = None
    if message.forward_from_chat:
        channel_id = message.forward_from_chat.id
    elif message.text:
        txt = message.text.strip()
        if txt.lstrip("-").isdigit():
            channel_id = int(txt)

    if channel_id is None:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не понял. Перешли сообщение "
            f"из канала [или] введи ID.</b></blockquote>",
            kb_admin_back(),
        )
        return

    try:
        chat = await bot.get_chat(channel_id)
        member = await bot.get_chat_member(channel_id, (await bot.get_me()).id)
        if member.status not in ("administrator", "creator"):
            await _finish_prompt(
                message,
                state,
                bot,
                f"<blockquote>{E.CROSS} <b>Бот не администратор "
                f"в этом канале. Добавь бота админом и попробуй снова.</b></blockquote>",
                kb_admin_back(),
            )
            return
    except Exception as exc:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не удалось проверить канал:</b> "
            f"<code>{exc}</code></blockquote>",
            kb_admin_back(),
        )
        return

    await db.set_payment_channel(channel_id)
    text = (
        f"{E.CHECK} <b>Канал сохранён</b>\n"
        f"<blockquote>"
        f"<b>Название:</b> <code>{chat.title}</code>\n"
        f"<b>ID:</b> <code>{channel_id}</code>"
        f"</blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == "adm:report:set")
async def adm_report_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    reports_db: ReportsDB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return

    current = await reports_db.get_channel()
    current_str = f"<code>{current}</code>" if current else "не задан"

    await state.set_state(AdminSG.waiting_report_channel_id)
    text = (
        f"{E.STATS} <b>Канал отчётов по покупкам</b>\n"
        f"<blockquote>"
        f"<b>Сейчас:</b> {current_str}\n"
        f"<b>Сюда будут приходить отчёты по каждой подтверждённой "
        f"покупке: юзернейм, ID, сумма, способ, дата.</b>\n"
        f"<b>Перешли любое сообщение из нужного канала, [или] "
        f"введи ID канала вручную.</b>\n"
        f"<b>Пример:</b> <code>-1001234567890</code>\n"
        f"<b>Бот должен быть админом канала.</b>"
        f"</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.ADM_HOME,
                    icon_custom_emoji_id="5256999125634000597",
                )
            ]
        ]
    )
    await _set_anchor_prompt(callback, state, text, kb)
    await callback.answer()


@router.message(AdminSG.waiting_report_channel_id)
async def adm_report_channel_save(
    message: Message,
    state: FSMContext,
    reports_db: ReportsDB,
    cfg: Config,
    bot: Bot,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return

    channel_id: int | None = None
    if message.forward_from_chat:
        channel_id = message.forward_from_chat.id
    elif message.text:
        txt = message.text.strip()
        if txt.lstrip("-").isdigit():
            channel_id = int(txt)

    if channel_id is None:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не понял. Перешли сообщение "
            f"из канала [или] введи ID.</b></blockquote>",
            kb_admin_back(),
        )
        return

    try:
        chat = await bot.get_chat(channel_id)
        member = await bot.get_chat_member(channel_id, (await bot.get_me()).id)
        if member.status not in ("administrator", "creator"):
            await _finish_prompt(
                message,
                state,
                bot,
                f"<blockquote>{E.CROSS} <b>Бот не администратор "
                f"в этом канале. Добавь бота админом и попробуй снова.</b></blockquote>",
                kb_admin_back(),
            )
            return
    except Exception as exc:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не удалось проверить канал:</b> "
            f"<code>{exc}</code></blockquote>",
            kb_admin_back(),
        )
        return

    await reports_db.set_channel(channel_id)
    text = (
        f"{E.CHECK} <b>Канал отчётов сохранён</b>\n"
        f"<blockquote>"
        f"<b>Название:</b> <code>{chat.title}</code>\n"
        f"<b>ID:</b> <code>{channel_id}</code>\n"
        f"<b>Все подтверждённые покупки будут публиковаться сюда.</b>"
        f"</blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_YM_URL)
async def adm_ym_url_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    current = await db.get_yoomoney_url(cfg.yoomoney_quickpay_url)
    current_str = f"<code>{current}</code>" if current else "не задана"
    await state.set_state(AdminSG.waiting_ym_url)
    text = (
        f'<tg-emoji emoji-id="5879814368572478751">💳</tg-emoji> <b>Ссылка для оплаты рублями</b>\n'
        f"<blockquote>"
        f"<b>Сейчас:</b> {current_str}\n"
        f"<b>Введи новую ссылку YooMoney (yoomoney.ru/to/...) или любую другую ссылку для перевода.</b>"
        f"</blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


def _kb_promo_list(promos: list[dict]) -> InlineKeyboardMarkup:
    from keyboards.inline import _two_col

    rows: list[list[InlineKeyboardButton]] = []
    # One [label | ✕] row per promo code.
    for p in promos:
        label = f"{p['code']} · {int(p['balance_kop']) // 100}₽ · {p['used_count']}/{p['max_uses']}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=CB.NOOP,
                    icon_custom_emoji_id="5346044461614924803",
                ),
                InlineKeyboardButton(
                    text="✕",
                    callback_data=f"{CB.ADM_PROMO_DEL}{p['id']}",
                ),
            ]
        )
    trailing = [
        InlineKeyboardButton(
            text="Создать промокод",
            callback_data=CB.ADM_PROMO_NEW,
            icon_custom_emoji_id=EmojiID.WRITE,
        ),
        InlineKeyboardButton(
            text="◁  Назад",
            callback_data=CB.ADM_HOME,
            icon_custom_emoji_id=EmojiID.BACK,
        ),
    ]
    rows.extend(_two_col(trailing))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _promo_list_text(promos: list[dict]) -> str:
    if not promos:
        body = "<b>Пусто. Жми «Создать промокод».</b>"
    else:
        body = f"<b>Активных промиков: <code>{len(promos)}</code>\nТапни ✕ чтобы удалить.</b>"
    return (
        f'<tg-emoji emoji-id="5346044461614924803">🎟</tg-emoji> '
        f"<b>Промокоды</b>\n"
        f"<blockquote>{body}</blockquote>"
    )


@router.callback_query(F.data == CB.ADM_PROMO)
async def adm_promo_list(callback: CallbackQuery, cfg: Config, promo_db: PromoDB) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    promos = await promo_db.list()
    await _edit_or_send(
        cast(Message, callback.message),
        _promo_list_text(promos),
        _kb_promo_list(promos),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.ADM_PROMO_DEL))
async def adm_promo_del(callback: CallbackQuery, cfg: Config, promo_db: PromoDB) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    try:
        pid = int(callback.data[len(CB.ADM_PROMO_DEL) :])
    except ValueError:
        await callback.answer()
        return
    await promo_db.delete(pid)
    await callback.answer("Удалён")
    await adm_promo_list(callback, cfg, promo_db)


@router.callback_query(F.data == CB.ADM_PROMO_NEW)
async def adm_promo_new_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    await state.set_state(AdminSG.waiting_promo_balance)
    text = (
        f"{E.WRITE} <b>Новый промокод</b>\n"
        f"<blockquote><b>Шаг 1/2 — введи сумму в ₽, "
        f"которая будет начисляться за активацию.</b></blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_promo_balance, F.text)
async def adm_promo_balance(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        rub = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    if rub < 1:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Минимум 1₽</b></blockquote>",
            kb_admin_back(),
        )
        return
    await state.update_data(promo_balance_rub=rub)
    await state.set_state(AdminSG.waiting_promo_uses)

    # delete user's input + edit anchor with step 2 prompt
    data = await state.get_data()
    anchor_id = data.get("anchor_msg_id")
    chat_id = data.get("anchor_chat_id") or message.chat.id
    with contextlib.suppress(Exception):
        await message.delete()
    text = (
        f"{E.WRITE} <b>Новый промокод</b>\n"
        f"<blockquote><b>Сумма: <code>{rub}₽</code>\n"
        f"Шаг 2/2 — сколько раз можно активировать?</b></blockquote>"
    )
    if anchor_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=anchor_id,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_admin_back(),
            )
            return
        except Exception:
            pass
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb_admin_back())


def _gen_promo_code(length: int = 8) -> str:
    import secrets
    import string

    alphabet = string.ascii_uppercase + string.digits
    # No 0/O/1/I: easy to misread.
    alphabet = "".join(c for c in alphabet if c not in "0O1I")
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.message(AdminSG.waiting_promo_uses, F.text)
async def adm_promo_uses(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
    promo_db: PromoDB,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    try:
        uses = int(message.text.strip())
    except ValueError:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не число</b></blockquote>",
            kb_admin_back(),
        )
        return
    if uses < 1:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Минимум 1 активация</b></blockquote>",
            kb_admin_back(),
        )
        return
    data = await state.get_data()
    rub = int(data.get("promo_balance_rub") or 0)
    if rub < 1:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Потерял сумму, начни заново</b></blockquote>",
            kb_admin_back(),
        )
        return

    # Retry on the (unlikely) collision.
    code = ""
    for _ in range(10):
        cand = _gen_promo_code(8)
        try:
            await promo_db.create(
                cand,
                rub * 100,
                uses,
                created_by=message.from_user.id,
            )
            code = cand
            break
        except Exception:
            continue
    if not code:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не удалось сгенерировать код, попробуй ещё раз</b></blockquote>",
            kb_admin_back(),
        )
        return

    text = (
        f"{E.CHECK} <b>Промокод создан</b>\n"
        f"<blockquote><b>Код: <code>{code}</code>\n"
        f"Сумма: <code>{rub}₽</code>\n"
        f"Активаций: <code>{uses}</code>\n"
        f"1 юзер = 1 активация.</b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.message(AdminSG.waiting_ym_url, F.text)
async def adm_ym_url_save(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    url = message.text.strip()
    if not url.startswith("http"):
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не похоже на ссылку. Введи URL, начинающийся с https://</b></blockquote>",
            kb_admin_back(),
        )
        return
    await db.set_yoomoney_url(url)
    text = f"{E.CHECK} <b>Ссылка обновлена</b>\n<blockquote><b><code>{url}</code></b></blockquote>"
    await _finish_prompt(message, state, bot, text, kb_admin_back())


@router.callback_query(F.data == CB.ADM_NOTIFY)
async def adm_notify_list(
    callback: CallbackQuery,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    media_map: dict[str, str] = {}
    for ev in NOTIFY_EVENTS:
        if await db.get_media(event_media_key(ev)):
            media_map[ev] = "set"
    text = (
        f"{E.MEDIA} <b>Медиа уведомлений</b>\n"
        f"<blockquote>"
        f"<b>Прикрепи GIF / фото / видео к любому событию — "
        f"оно будет идти как обложка с текстом-подписью. "
        f"Без медиа — обычное текстовое сообщение.</b>"
        f"</blockquote>"
    )
    await _edit_or_send(
        cast(Message, callback.message),
        text,
        kb_admin_notify(NOTIFY_EVENTS, media_map),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.ADM_NOTIFY_SET))
async def adm_notify_set_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    event_key = callback.data[len(CB.ADM_NOTIFY_SET) :]
    if event_key not in NOTIFY_EVENTS:
        await callback.answer()
        return
    media = await db.get_media(event_media_key(event_key))
    has = bool(media)
    await state.set_state(AdminSG.waiting_notify_media)
    await state.update_data(notify_event=event_key)
    body = ""
    if has:
        body = f"<b>Сейчас:</b> <code>{media['kind']}</code>\n"
    text = (
        f"{E.UPLOAD} <b>Событие: {NOTIFY_EVENTS[event_key]}</b>\n"
        f"<blockquote>"
        f"{body}"
        f"<b>Пришли GIF, видео [или] фото. Стикеры не подходят "
        f"— Telegram не разрешает к ним подпись.</b>"
        f"</blockquote>"
    )
    await _set_anchor_prompt(
        callback,
        state,
        text,
        kb_admin_notify_actions(event_key, has_media=has),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.ADM_NOTIFY_DEL))
async def adm_notify_del(
    callback: CallbackQuery,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    event_key = callback.data[len(CB.ADM_NOTIFY_DEL) :]
    if event_key not in NOTIFY_EVENTS:
        await callback.answer()
        return
    await db.delete_media(event_media_key(event_key))
    await callback.answer("Удалено")
    await adm_notify_list(callback, db, cfg)


@router.message(AdminSG.waiting_notify_media, F.animation | F.video | F.photo | F.document)
async def adm_notify_media_save(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    data = await state.get_data()
    event_key = data.get("notify_event")
    if not event_key or event_key not in NOTIFY_EVENTS:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Потерял событие</b></blockquote>",
            kb_admin_back(),
        )
        return

    file_id, kind = _classify_media(message)
    if not file_id:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не понял формат — "
            f"пришли GIF, фото или видео</b></blockquote>",
            kb_admin_back(),
        )
        return

    await db.set_media(event_media_key(event_key), file_id, kind)
    caption = (
        f"{E.CHECK} <b>Медиа прикреплена</b>\n"
        f"<blockquote>"
        f"<b>Событие:</b> {NOTIFY_EVENTS[event_key]}\n"
        f"<b>Тип:</b> <code>{kind}</code>"
        f"</blockquote>"
    )
    await _finish_prompt_with_preview(
        message,
        state,
        bot,
        caption,
        kb_admin_notify_actions(event_key, has_media=True),
        file_id,
        kind,
    )


def _kb_admin_examples() -> InlineKeyboardMarkup:
    from keyboards.inline import _two_col

    buttons = [
        InlineKeyboardButton(
            text="С ником",
            callback_data="a:ex:edit:pack.example.nick_url",
            icon_custom_emoji_id=EmojiID.WRITE,
        ),
        InlineKeyboardButton(
            text="С лого",
            callback_data="a:ex:edit:pack.example.logo_url",
            icon_custom_emoji_id=EmojiID.WRITE,
        ),
        InlineKeyboardButton(
            text="Стикер-пак",
            callback_data="a:ex:edit:pack.example.sticker_url",
            icon_custom_emoji_id=EmojiID.WRITE,
        ),
        InlineKeyboardButton(
            text="◁  Назад",
            callback_data=CB.ADM_HOME,
            icon_custom_emoji_id=EmojiID.BACK,
        ),
    ]
    return InlineKeyboardMarkup(inline_keyboard=_two_col(buttons))


@router.callback_query(F.data == CB.ADM_EXAMPLES)
async def adm_examples_home(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    nick = content_service().render("pack.example.nick_url")
    logo = content_service().render("pack.example.logo_url")
    sticker = content_service().render("pack.example.sticker_url")
    text = (
        f"{E.LINK} <b>Ссылки на примеры паков</b>\n"
        f"<blockquote>"
        f"<b>С ником:</b> <code>{nick}</code>\n"
        f"<b>С лого:</b> <code>{logo}</code>\n"
        f"<b>Стикер-пак:</b> <code>{sticker}</code>"
        f"</blockquote>"
    )
    await _edit_or_send(
        cast(Message, callback.message),
        text,
        _kb_admin_examples(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("a:ex:edit:"))
async def adm_example_edit_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.message is None or callback.data is None:
        return
    if not _ensure_admin(callback.from_user.id, cfg):
        return
    key = callback.data[len("a:ex:edit:") :]
    current = content_service().render(key)
    label_map = {
        "pack.example.nick_url": "С ником",
        "pack.example.logo_url": "С лого",
        "pack.example.sticker_url": "Стикер-пак",
    }
    label = label_map.get(key, key)
    await state.set_state(AdminSG.waiting_example_url)
    await state.update_data(example_key=key)
    text = (
        f"{E.WRITE} <b>Редактировать ссылку: {label}</b>\n"
        f"<blockquote>"
        f"<b>Сейчас:</b> <code>{current}</code>\n"
        f"<b>Введи новую ссылку на пак (t.me/addemoji/... или t.me/addstickers/...)</b>"
        f"</blockquote>"
    )
    await _set_anchor_prompt(callback, state, text, kb_admin_back())
    await callback.answer()


@router.message(AdminSG.waiting_example_url, F.text)
async def adm_example_url_save(
    message: Message,
    state: FSMContext,
    bot: Bot,
    cfg: Config,
) -> None:
    if message.from_user is None or not _ensure_admin(message.from_user.id, cfg):
        return
    if message.text is None:
        return
    data = await state.get_data()
    key = data.get("example_key")
    if not key:
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Ошибка: ключ не найден</b></blockquote>",
            kb_admin_back(),
        )
        return
    url = message.text.strip()
    if not url.startswith("http"):
        await _finish_prompt(
            message,
            state,
            bot,
            f"<blockquote>{E.CROSS} <b>Не похоже на ссылку. Введи URL, начинающийся с https://</b></blockquote>",
            kb_admin_back(),
        )
        return
    await content_service().set_override(key, url)
    label_map = {
        "pack.example.nick_url": "С ником",
        "pack.example.logo_url": "С лого",
        "pack.example.sticker_url": "Стикер-пак",
    }
    label = label_map.get(key, key)
    text = (
        f"{E.CHECK} <b>Ссылка обновлена</b>\n"
        f"<blockquote><b>{label}:</b> <code>{url}</code></b></blockquote>"
    )
    await _finish_prompt(message, state, bot, text, kb_admin_back())
