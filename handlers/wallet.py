"""Wallet top-ups: SBP via Platega and CryptoBot are confirmed automatically, manual transfers go through admin review."""

from __future__ import annotations

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
    kb_admin_payment_review,
    kb_wallet,
)
from services.billing import Billing
from services.notify_media import send_event
from services.notify_texts import credit_text, payment_approved_text, payment_rejected_text
from services.payments.cryptobot import CryptoBot
from services.payments.platega import Platega
from services.payments.reports import report_payment_paid
from services.reports_db import ReportsDB
from services.screens import S, edit_screen, safe_edit
from states import MainSG, WalletSG
from utils.emoji import E, EmojiID
from utils.logger import get_logger

router = Router(name="wallet")
log = get_logger("h.wallet")


def _wallet_text(balance_rub: float, price_rub: int, free: int) -> str:
    return (
        f'<tg-emoji emoji-id="5769126056262898415">👛</tg-emoji> '
        f"<b>Ваш баланс: {balance_rub:.1f}₽</b>\n"
        f'<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> '
        f"<b>Цена рендера: {price_rub}₽</b>\n\n"
        f"<blockquote>"
        f'<tg-emoji emoji-id="5893057118545646106">↕️</tg-emoji> '
        f"<b>Выбери способ пополнения:</b>"
        f"</blockquote>"
    )


@router.callback_query(F.data == CB.WALLET)
async def open_wallet(callback: CallbackQuery, db: DB, billing: Billing) -> None:
    if callback.from_user is None or callback.message is None:
        return
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    text = _wallet_text(
        user["balance_kop"] / 100,
        await billing.get_price_rub(),
        user["free_renders"],
    )
    await edit_screen(cast(Message, callback.message), db, S.WALLET, text, keyboard=kb_wallet())
    await callback.answer()


@router.callback_query(F.data == CB.PAY_SBP)
async def open_sbp(
    callback: CallbackQuery,
    state: FSMContext,
    cfg: Config,
    platega: Platega,
) -> None:
    if callback.message is None:
        return
    if not platega.is_configured:
        await callback.answer("СБП не настроен", show_alert=True)
        return
    await state.set_state(WalletSG.waiting_amount_sbp)
    await safe_edit(
        cast(Message, callback.message),
        '<tg-emoji emoji-id="5318972874726339331">🪙</tg-emoji> '
        "<b>СБП</b>\n\n"
        "<blockquote><b>Оплата через СБП по QR-коду / ссылке. "
        "Зачисление автоматическое.</b></blockquote>\n"
        "<blockquote><b>Введи сумму пополнения в ₽:</b></blockquote>",
        keyboard=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◁  Назад",
                        callback_data=CB.WALLET,
                        icon_custom_emoji_id=EmojiID.BACK,
                    ),
                ]
            ]
        ),
    )
    await callback.answer()


@router.message(WalletSG.waiting_amount_sbp, F.text)
async def sbp_amount_entered(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
    platega: Platega,
) -> None:
    if message.from_user is None or message.text is None:
        return
    try:
        amount_rub = int(message.text.strip())
    except ValueError:
        await message.reply(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Введи число, например: 100</b>",
            parse_mode=ParseMode.HTML,
        )
        return
    min_amount = max(1, cfg.sbp_min_amount)
    if amount_rub < min_amount:
        await message.reply(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            f"<b>Минимальная сумма — {min_amount}₽</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        tx = await platega.create_sbp(
            amount_rub=amount_rub,
            description=f"Пополнение баланса на {amount_rub}₽",
            payload=f"u{message.from_user.id}",
        )
    except Exception as exc:
        log.exception("Platega create_sbp failed")
        await message.reply(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Ошибка кассы: {exc}</b>',
            parse_mode=ParseMode.HTML,
        )
        return

    transaction_id = str(tx.get("transactionId") or "")
    pay_url = tx.get("redirect") or ""
    if not transaction_id or not pay_url:
        log.error("Platega returned no transactionId/redirect: %s", tx)
        await message.reply(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Касса вернула пустой ответ. Попробуй позже.</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    await db.create_payment(
        message.from_user.id,
        "platega_sbp",
        amount_rub * 100,
        external_id=transaction_id,
        extra={"rub": amount_rub, "expires_in": tx.get("expiresIn", "")},
    )
    await state.set_state(MainSG.idle)
    await message.answer(
        f'<tg-emoji emoji-id="5318972874726339331">🪙</tg-emoji> '
        f"<b>СБП</b>\n\n"
        f"<blockquote>"
        f"<b>К оплате: <code>{amount_rub}₽</code></b>\n"
        f"<b>Перейди по ссылке и оплати по QR или из приложения банка. "
        f"Баланс пополнится автоматически сразу после оплаты.</b>"
        f"</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Оплатить",
                        url=pay_url,
                        icon_custom_emoji_id="5318972874726339331",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◁  Назад",
                        callback_data=CB.WALLET,
                        icon_custom_emoji_id=EmojiID.BACK,
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data == CB.PAY_CRYPTO)
async def open_crypto(callback: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if callback.message is None:
        return
    if not cfg.cryptobot_token:
        await callback.answer("CryptoBot не настроен", show_alert=True)
        return
    await state.set_state(WalletSG.waiting_amount_cryptobot)
    await safe_edit(
        cast(Message, callback.message),
        '<tg-emoji emoji-id="5345833033259834487">👾</tg-emoji> '
        "<b>CryptoBot</b>\n\n"
        "<blockquote><b>Оплата в USDT по текущему курсу. "
        "Зачисление автоматическое.</b></blockquote>",
        keyboard=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Ввести сумму",
                        callback_data="pay:crypto:enter",
                        icon_custom_emoji_id="5904462880941545555",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◁  Назад",
                        callback_data=CB.WALLET,
                        icon_custom_emoji_id=EmojiID.BACK,
                    )
                ],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pay:crypto:enter")
async def crypto_enter_amount(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(WalletSG.waiting_amount_cryptobot)
    await safe_edit(
        cast(Message, callback.message),
        "<blockquote>"
        '<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji> '
        "<b>Введите сумму для пополнения</b>"
        "</blockquote>",
        keyboard=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◁  Назад",
                        callback_data=CB.PAY_CRYPTO,
                        icon_custom_emoji_id=EmojiID.BACK,
                    ),
                ]
            ]
        ),
    )
    await callback.answer()


@router.message(WalletSG.waiting_amount_cryptobot, F.text)
async def crypto_amount_entered(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: DB,
    cfg: Config,
    cryptobot: CryptoBot,
) -> None:
    if message.from_user is None or message.text is None:
        return
    try:
        amount_rub = int(message.text.strip())
    except ValueError:
        await message.reply(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Введи число, например: 100</b>",
            parse_mode=ParseMode.HTML,
        )
        return
    if amount_rub < 10:
        await message.reply(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Минимальная сумма — 10₽</b>',
            parse_mode=ParseMode.HTML,
        )
        return

    rub_per_usdt = await cryptobot.rub_per_unit("USDT")
    if not rub_per_usdt or rub_per_usdt <= 0:
        await message.reply(
            '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
            "<b>Не удалось получить курс USDT</b>",
            parse_mode=ParseMode.HTML,
        )
        return
    usdt_amount = round(amount_rub / rub_per_usdt, 2)
    try:
        inv = await cryptobot.create_invoice(
            amount=usdt_amount,
            asset="USDT",
            description=f"Пополнение баланса на {amount_rub}₽",
            payload=f"u{message.from_user.id}",
        )
    except Exception as exc:
        log.exception("CryptoBot invoice failed")
        await message.reply(
            f'<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Ошибка: {exc}</b>',
            parse_mode=ParseMode.HTML,
        )
        return

    pid = await db.create_payment(
        message.from_user.id,
        "cryptobot",
        amount_rub * 100,
        external_id=str(inv.get("invoice_id")),
        extra={"asset": "USDT", "amount": usdt_amount, "rub": amount_rub, "rate": rub_per_usdt},
    )
    await state.set_state(MainSG.idle)
    await message.answer(
        f"<blockquote>"
        f"<b>Перейдите по ссылке для оплаты</b>\n"
        f"К оплате: <code>{usdt_amount} USDT</code> (~{amount_rub}₽)"
        f"</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Оплатить",
                        url=inv.get("pay_url") or inv.get("bot_invoice_url", ""),
                        icon_custom_emoji_id="5346267671065281783",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Проверить оплату",
                        callback_data=f"{CB.PAY_RECHECK}{pid}",
                        icon_custom_emoji_id=EmojiID.LOADING,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◁  Назад",
                        callback_data=CB.WALLET,
                        icon_custom_emoji_id=EmojiID.BACK,
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith(CB.PAY_CONFIRM))
async def pay_confirm(
    callback: CallbackQuery,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    try:
        pid = int(callback.data[len(CB.PAY_CONFIRM) :])
    except ValueError:
        await callback.answer()
        return
    pay = await db.get_payment(pid)
    if not pay or pay["user_id"] != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    if pay["status"] != "pending":
        await callback.answer(f"Статус: {pay['status']}", show_alert=True)
        return

    await db.update_payment_status(pid, "awaiting_admin")
    await callback.answer("Заявка отправлена")
    if callback.message:
        with contextlib.suppress(Exception):
            await safe_edit(
                cast(Message, callback.message),
                "<blockquote><b>Заявка отправлена.\n"
                "Пришли скриншот подтверждения оплаты.</b></blockquote>",
                keyboard=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="◁  В меню",
                                callback_data=CB.MAIN,
                                icon_custom_emoji_id=EmojiID.BACK,
                            ),
                        ]
                    ]
                ),
            )

    await bot.send_message(
        callback.from_user.id,
        "<blockquote><b>Пришли скриншот оплаты — "
        "он уйдёт на проверку и баланс будет пополнен.</b></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◁  Пропустить",
                        callback_data=f"pay:skip_screen:{pid}",
                        icon_custom_emoji_id=EmojiID.BACK,
                    ),
                ]
            ]
        ),
    )
    await db.set_setting(f"pay_screen_wait:{callback.from_user.id}", str(pid))


@router.callback_query(F.data.startswith("pay:skip_screen:"))
async def pay_skip_screen(callback: CallbackQuery, bot: Bot, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.data is None:
        return
    try:
        pid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer()
        return
    await db.set_setting(f"pay_screen_wait:{callback.from_user.id}", "")
    await callback.answer()
    await _notify_payment(bot, db, cfg, callback.from_user, pid, photo_file_id=None)
    if callback.message:
        with contextlib.suppress(Exception):
            await safe_edit(
                cast(Message, callback.message),
                "<blockquote><b>Заявка отправлена на проверку.</b></blockquote>",
            )


@router.message(F.photo)
async def pay_screenshot(message: Message, bot: Bot, db: DB, cfg: Config) -> None:
    """Intercept photo sent as payment proof."""
    if message.from_user is None:
        return
    wait_key = f"pay_screen_wait:{message.from_user.id}"
    pid_str = await db.get_setting(wait_key)
    if not pid_str:
        return
    try:
        pid = int(pid_str)
    except ValueError:
        return
    await db.set_setting(wait_key, "")
    photo_id = message.photo[-1].file_id if message.photo else None
    await _notify_payment(bot, db, cfg, message.from_user, pid, photo_file_id=photo_id)
    await message.reply(
        "<blockquote><b>Скриншот получен. Ожидай подтверждения.</b></blockquote>",
        parse_mode=ParseMode.HTML,
    )


async def _notify_payment(
    bot: Bot,
    db: DB,
    cfg: Config,
    user,
    pid: int,
    *,
    photo_file_id=None,
) -> None:
    """Send payment notification to admins and channel."""
    pay = await db.get_payment(pid)
    if not pay:
        return
    username = f"@{user.username}" if user.username else f"id={user.id}"
    provider_label = {"yoomoney": "ЮMoney", "sbp": "СБП"}.get(
        pay.get("provider", ""),
        pay.get("provider", "?"),
    )
    text = (
        f'<tg-emoji emoji-id="5318972874726339331">🪙</tg-emoji> '
        f"<b>Заявка {provider_label} #{pid}</b>\n\n"
        f"<blockquote>"
        f"<b>Пользователь:</b> {username} (<code>{user.id}</code>)\n"
        f"<b>Сумма:</b> <code>{pay['amount_kop'] / 100:.0f}₽</code>\n"
        f"<b>Метка:</b> <code>u{user.id}_{pid}</code>"
        f"</blockquote>"
    )
    kb = kb_admin_payment_review(pid)
    channel_id = await db.get_payment_channel()
    targets = list(cfg.admin_ids) + ([channel_id] if channel_id else [])

    for target in targets:
        try:
            if photo_file_id:
                await bot.send_photo(
                    chat_id=target,
                    photo=photo_file_id,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            else:
                await bot.send_message(
                    chat_id=target,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
        except Exception:
            log.exception("failed to notify target %s", target)


async def _credit_and_notify(
    bot: Bot,
    db: DB,
    billing: Billing,
    reports_db: ReportsDB,
    callback: CallbackQuery,
    pay: dict,
) -> None:
    pid = pay["id"]
    await db.update_payment_status(pid, "paid")
    new_bal = await billing.add_kop(pay["user_id"], pay["amount_kop"])
    await callback.answer("✅ Зачислено!")
    if callback.message:
        with contextlib.suppress(Exception):
            await callback.message.delete()
    # Re-fetch so the report shows the just-updated status/timestamp.
    fresh = await db.get_payment(pid) or pay
    try:
        await report_payment_paid(bot, db, reports_db, fresh)
    except Exception:
        log.exception("failed to report paid payment to channel")
    try:
        await send_event(
            bot,
            db,
            pay["user_id"],
            "credit",
            credit_text(pay["amount_kop"] / 100, new_bal / 100),
        )
    except Exception:
        log.exception("failed to notify paid user")


@router.callback_query(F.data.startswith(CB.PAY_RECHECK))
async def pay_recheck(
    callback: CallbackQuery,
    bot: Bot,
    db: DB,
    cryptobot: CryptoBot,
    platega: Platega,
    billing: Billing,
    reports_db: ReportsDB,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    try:
        pid = int(callback.data[len(CB.PAY_RECHECK) :])
    except ValueError:
        await callback.answer()
        return
    pay = await db.get_payment(pid)
    if not pay or pay["user_id"] != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    if pay["status"] == "paid":
        await callback.answer("Уже зачислено")
        return
    if pay["status"] != "pending":
        await callback.answer(f"Статус: {pay['status']}", show_alert=True)
        return

    provider = pay.get("provider", "")

    if provider == "platega_sbp":
        tx_id = (pay.get("external_id") or "").strip()
        if not tx_id:
            await callback.answer("Нет идентификатора транзакции", show_alert=True)
            return
        tx = await platega.get_transaction(tx_id)
        if not tx:
            await callback.answer("Не получилось проверить", show_alert=True)
            return
        status = (tx.get("status") or "").upper()
        if status == "CONFIRMED":
            await _credit_and_notify(bot, db, billing, reports_db, callback, pay)
        elif status in ("CANCELED", "CHARGEBACKED"):
            await db.update_payment_status(pid, "expired")
            await callback.answer(f"Платёж отменён ({status})", show_alert=True)
        else:
            await callback.answer(
                "Платёж ещё не поступил. Попробуй через минуту.",
                show_alert=True,
            )
        return

    inv = await cryptobot.get_invoice(int(pay["external_id"] or 0))
    if not inv:
        await callback.answer("Не получилось проверить", show_alert=True)
        return
    status = inv.get("status")
    if status == "paid":
        await _credit_and_notify(bot, db, billing, reports_db, callback, pay)
    elif status == "expired":
        await db.update_payment_status(pid, "expired")
        await callback.answer("Срок инвойса истёк", show_alert=True)
    else:
        await callback.answer(f"Статус: {status}. Попробуй через минуту.", show_alert=True)


@router.callback_query(F.data.startswith(CB.PAY_ADMIN_OK))
async def admin_confirm_payment(
    callback: CallbackQuery,
    bot: Bot,
    db: DB,
    cfg: Config,
    billing: Billing,
    reports_db: ReportsDB,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if callback.from_user.id not in cfg.admin_ids:
        await callback.answer()
        return
    try:
        pid = int(callback.data[len(CB.PAY_ADMIN_OK) :])
    except ValueError:
        await callback.answer()
        return
    pay = await db.get_payment(pid)
    if not pay or pay["status"] in ("paid", "rejected"):
        await callback.answer(f"Уже {pay['status'] if pay else 'нет'}", show_alert=True)
        return

    await db.update_payment_status(pid, "paid")
    new_bal = await billing.add_kop(pay["user_id"], pay["amount_kop"])
    await callback.answer("Подтверждено")

    if callback.message:
        try:
            existing = callback.message.text or callback.message.caption or ""
            await safe_edit(
                cast(Message, callback.message),
                existing + f"\n\n{E.CHECK} <b>Подтверждено</b>",
            )
        except Exception:
            pass

    fresh = await db.get_payment(pid) or pay
    try:
        await report_payment_paid(bot, db, reports_db, fresh)
    except Exception:
        log.exception("failed to report paid payment to channel")

    # Notify user — via send_event so admin-attached "payment_approved" media is used
    try:
        await send_event(
            bot,
            db,
            pay["user_id"],
            "payment_approved",
            payment_approved_text(pay["amount_kop"] / 100, new_bal / 100),
        )
    except Exception:
        log.exception("failed to notify paid user")


@router.callback_query(F.data.startswith(CB.PAY_ADMIN_NO))
async def admin_decline_payment(
    callback: CallbackQuery,
    bot: Bot,
    db: DB,
    cfg: Config,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if callback.from_user.id not in cfg.admin_ids:
        await callback.answer()
        return
    try:
        pid = int(callback.data[len(CB.PAY_ADMIN_NO) :])
    except ValueError:
        await callback.answer()
        return
    pay = await db.get_payment(pid)
    if not pay:
        await callback.answer()
        return
    await db.update_payment_status(pid, "rejected")
    await callback.answer("Отклонено")
    if callback.message:
        try:
            existing = callback.message.text or callback.message.caption or ""
            await safe_edit(
                cast(Message, callback.message),
                existing + f"\n\n{E.CROSS} <b>Отклонено</b>",
            )
        except Exception:
            pass
    with contextlib.suppress(Exception):
        await send_event(
            bot,
            db,
            pay["user_id"],
            "payment_rejected",
            payment_rejected_text(),
        )
