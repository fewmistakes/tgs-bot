"""Render flow: receive sticker/premium-emoji/TGS → check billing → render."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import cast

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import Config
from db import DB
from keyboards.cb import CB
from keyboards.inline import kb_main
from services.billing import Billing
from services.content import t
from services.pipeline import output_extension, render_async
from services.screens import S, edit_screen
from services.sticker_fetcher import fetch_all_tgs_from_message, fetch_tgs_from_message
from states import GifSG, PreviewSG, job_store
from utils.emoji import E, EmojiID
from utils.logger import get_logger

router = Router(name="convert")
log = get_logger("h.convert")

_RENDER_E = '<tg-emoji emoji-id="5346149211572304778">🔄</tg-emoji>'
_CATCH_E = '<tg-emoji emoji-id="5886451926995833684">🎉</tg-emoji>'


def _render_status_html() -> str:
    return f"<blockquote>{_RENDER_E} <b>рендерю</b></blockquote>"


def _catch_status_html() -> str:
    return f"<blockquote>{_CATCH_E} <b>лови</b></blockquote>"


def _preview_status_html() -> str:
    return f"<blockquote>{_RENDER_E} <b>готовлю предосмотр</b></blockquote>"


_STICKER_FILTER = F.sticker | F.document | (F.entities & F.text)


@router.callback_query(F.data == CB.PREVIEW)
async def open_preview(callback: CallbackQuery, state: FSMContext, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    await state.set_state(PreviewSG.waiting_tgs)
    bot_username = cfg.bot_username or "converter_zabot"
    text = t("preview.text", bot_username=bot_username)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.GIF_HOME,
                    icon_custom_emoji_id=EmojiID.BACK,
                ),
            ]
        ]
    )
    msg = cast(Message, callback.message)
    await edit_screen(msg, db, S.PREVIEW, text, keyboard=kb)
    await callback.answer()


@router.message(PreviewSG.waiting_tgs, _STICKER_FILTER)
async def receive_for_preview(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None:
        return

    status = await message.reply(
        t("convert.downloading"),
        parse_mode=ParseMode.HTML,
    )
    tgs_path, err = await fetch_tgs_from_message(message, bot, Path("data/tmp"))
    if err or not tgs_path:
        with contextlib.suppress(Exception):
            await status.delete()
        await message.reply(
            f"<blockquote>{E.CROSS} <b>{err or 'Не получилось получить TGS'}</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    job = job_store.get(message.from_user.id)
    job.cleanup_uploads()
    job.tgs_path = tgs_path
    await state.set_state(GifSG.idle)

    await status.edit_text(
        _preview_status_html(),
        parse_mode=ParseMode.HTML,
    )

    out_path = Path("data/tmp") / (
        f"prev_{message.from_user.id}_"
        f"{int(asyncio.get_running_loop().time())}{output_extension(job.cfg.fmt)}"
    )
    try:
        await render_async(
            tgs_path=job.tgs_path,
            user_cfg=job.cfg,
            cfg=cfg,
            out_path=out_path,
            bg_path=job.bg_path,
            bg_kind=job.bg_kind,
            preview_watermark=f"@{cfg.bot_username or 'bot'}",
        )
    except Exception as exc:
        log.exception("preview render failed for user=%d", message.from_user.id)
        with contextlib.suppress(Exception):
            await status.delete()
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Не получилось: "
            f"<code>{type(exc).__name__}</code></b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        job.cleanup_uploads()
        return

    with contextlib.suppress(Exception):
        await status.edit_text(_catch_status_html(), parse_mode=ParseMode.HTML)

    await message.answer_animation(
        FSInputFile(out_path, filename=f"preview{output_extension(job.cfg.fmt)}"),
    )
    with contextlib.suppress(OSError):
        out_path.unlink()
    job.cleanup_uploads()


def _render_progress_html(i: int, n: int) -> str:
    if n <= 1:
        return _render_status_html()
    return f"<blockquote>{_RENDER_E} <b>рендерю {i}/{n}</b></blockquote>"


async def _refund_one(
    db: DB,
    billing: Billing,
    user_id: int,
    paid_with: str,
) -> None:
    """Reverse a single consume_render() so a failed item costs nothing."""
    if paid_with == "free":
        await db.add_free_renders(user_id, +1)
    elif paid_with == "balance":
        price = await billing.get_price_kop()
        await db.add_balance_kop(user_id, +price)


@router.message(GifSG.idle, _STICKER_FILTER)
async def receive_for_render(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
    billing: Billing,
) -> None:
    if message.from_user is None:
        return
    uid = message.from_user.id

    user = await db.get_user(uid)
    if user is None:
        return

    status = await message.reply(
        t("convert.downloading"),
        parse_mode=ParseMode.HTML,
    )

    tgs_paths, fetch_errors = await fetch_all_tgs_from_message(
        message,
        bot,
        Path("data/tmp"),
    )
    if not tgs_paths:
        with contextlib.suppress(Exception):
            await status.delete()
        err_text = fetch_errors[0] if fetch_errors else "Не получилось получить TGS"
        await message.reply(
            f"<blockquote>{E.CROSS} <b>{err_text}</b></blockquote>", parse_mode=ParseMode.HTML
        )
        return

    n = len(tgs_paths)

    if not await billing.can_render_n(uid, n):
        price = await billing.get_price_rub()
        balance_rub = user["balance_kop"] / 100
        with contextlib.suppress(Exception):
            await status.delete()
        await message.reply(
            t("convert.no_funds", balance=f"{balance_rub:.0f}", price=price)
            + (f"\n<b>Нужно на {n} рендеров.</b>" if n > 1 else ""),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main(balance_rub=balance_rub, free=user["free_renders"]),
        )
        for p in tgs_paths:
            with contextlib.suppress(OSError):
                p.unlink()
        return

    job = job_store.get(uid)
    job.cleanup_uploads()

    rendered = 0
    failures: list[str] = []
    out_to_send: list[Path] = []

    for i, tgs_path in enumerate(tgs_paths, 1):
        with contextlib.suppress(Exception):
            await status.edit_text(
                _render_progress_html(i, n),
                parse_mode=ParseMode.HTML,
            )

        ok, paid_with = await billing.consume_render(uid)
        if not ok:
            failures.append(f"средства закончились на {i}/{n}")
            with contextlib.suppress(OSError):
                tgs_path.unlink()
            break

        out_path = Path("data/tmp") / (
            f"out_{uid}_{i}_{int(asyncio.get_running_loop().time())}{output_extension(job.cfg.fmt)}"
        )

        try:
            await render_async(
                tgs_path=tgs_path,
                user_cfg=job.cfg,
                cfg=cfg,
                out_path=out_path,
                bg_path=job.bg_path,
                bg_kind=job.bg_kind,
            )
        except Exception as exc:
            log.exception("render %d/%d failed for user=%d — refunding", i, n, uid)
            await _refund_one(db, billing, uid, paid_with)
            failures.append(f"{i}/{n}: {type(exc).__name__}")
            with contextlib.suppress(OSError):
                tgs_path.unlink()
            continue

        price_kop = await billing.get_price_kop()
        cost = 0 if paid_with == "free" else price_kop
        await db.log_conversion(uid, job.cfg.fmt, cost, paid_with)

        rendered += 1
        out_to_send.append(out_path)
        with contextlib.suppress(OSError):
            tgs_path.unlink()

    # Only send the final "done" message if something actually rendered.
    if rendered > 0:
        try:
            if rendered == n:
                await status.edit_text(_catch_status_html(), parse_mode=ParseMode.HTML)
            else:
                await status.edit_text(
                    f"<blockquote>{_CATCH_E} <b>лови {rendered}/{n}</b></blockquote>",
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            pass
    else:
        with contextlib.suppress(Exception):
            await status.delete()

    for idx, out_path in enumerate(out_to_send, 1):
        try:
            await message.answer_animation(
                FSInputFile(out_path, filename=f"render_{idx}{output_extension(job.cfg.fmt)}"),
            )
        except Exception:
            log.exception("send render %d failed", idx)
        with contextlib.suppress(OSError):
            out_path.unlink()

    # Surface any per-item issues (failed renders, fetch warnings) in a
    # single trailing message so the user knows what was skipped.
    issues = failures + fetch_errors
    if issues and rendered < n:
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Пропущено:</b>\n"
            + "\n".join(f"• {x}" for x in issues)
            + "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
    elif fetch_errors and rendered > 0:
        # Renders succeeded but fetch had warnings (e.g. truncated batch)
        await message.reply(
            f"<blockquote>{E.CROSS} <b>Заметки:</b>\n"
            + "\n".join(f"• {x}" for x in fetch_errors)
            + "</blockquote>",
            parse_mode=ParseMode.HTML,
        )

    job.cleanup_uploads()


@router.message(_STICKER_FILTER)
async def sticker_in_other_state(message: Message, state: FSMContext) -> None:
    await message.reply(t("errors.elsewhere"), parse_mode=ParseMode.HTML)
