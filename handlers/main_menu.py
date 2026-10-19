"""/start, the main menu, referrals and the /admin entry point."""

from __future__ import annotations

import contextlib
import random
from typing import cast

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from config import Config
from db import DB
from keyboards.cb import CB
from services.billing import Billing
from services.content import btn, t
from services.notify_texts import referral_text
from services.profile_db import ProfileDB
from services.promo_db import PromoDB
from services.screens import S, edit_screen, safe_edit, send_screen
from states import AdminSG, GifSG, MainSG, PromoSG, RefCaptchaSG, job_store
from utils.emoji import E, EmojiID

router = Router(name="main")


def _kb_main_dynamic(
    *, balance_rub: float, free: int, webapp_url: str = ""
) -> InlineKeyboardMarkup:
    """Main menu: wallet, GIF + emoji, referrals + info.

    When `webapp_url` is set the emoji button opens the editor directly;
    otherwise it goes through the pack home screen.
    """
    # Launch the mini-app directly; the callback path remains as a fallback.
    if webapp_url:
        emoji_btn = InlineKeyboardButton(
            text=btn("main.btn.create_pack"),
            web_app=WebAppInfo(url=webapp_url),
            icon_custom_emoji_id="5346136511354004471",
        )
    else:
        emoji_btn = InlineKeyboardButton(
            text=btn("main.btn.create_pack"),
            callback_data=CB.PACK_HOME,
            icon_custom_emoji_id="5346136511354004471",
        )
    rows = [
        [
            InlineKeyboardButton(
                text=btn("main.btn.wallet", balance=f"{balance_rub:.0f}", free_badge=""),
                callback_data=CB.WALLET,
                icon_custom_emoji_id="5354996371755645180",
            )
        ],
        [
            InlineKeyboardButton(
                text=btn("main.btn.gif_home"),
                callback_data=CB.GIF_HOME,
                icon_custom_emoji_id="5355313692529424034",
            ),
            emoji_btn,
        ],
        [
            InlineKeyboardButton(
                text=btn("main.btn.other"),
                callback_data=CB.OTHER_HOME,
                icon_custom_emoji_id="5345869179704606041",
            ),
            InlineKeyboardButton(
                text=btn("main.btn.info"),
                callback_data=CB.INFO,
                icon_custom_emoji_id="5983568653751160844",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


_E_BOT = '<tg-emoji emoji-id="5402419678356205423">⚡</tg-emoji>'
_E_UPLOAD = '<tg-emoji emoji-id="5258332798409783582">⬆️</tg-emoji>'
_E_SETTINGS = '<tg-emoji emoji-id="5346115646402869647">⚙️</tg-emoji>'
_E_PROFILE = '<tg-emoji emoji-id="5210740046759482936">👤</tg-emoji>'
_E_MONEY = '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji>'


async def _user_rank(db: DB, profile_db: ProfileDB, user_id: int) -> tuple[int, int]:
    """1-based ordinal position of the user by total (renders + packs).

    Previously this used "competition ranking": every user with the same
    score got the same rank. Result: every newcomer (score=0) saw the
    same rank like `43/226`. Now ordinal — ties are broken by user_id
    (smaller user_id = earlier registration = higher rank within tie),
    so every user gets a unique position.
    """
    cur1 = await db.conn.execute("SELECT user_id, total_renders FROM users")
    renders = {r["user_id"]: int(r["total_renders"] or 0) for r in await cur1.fetchall()}
    cur2 = await profile_db.conn.execute("SELECT user_id, total_packs FROM profile")
    packs = {r["user_id"]: int(r["total_packs"] or 0) for r in await cur2.fetchall()}
    all_uids = set(renders) | set(packs) | {user_id}
    # Sort key: score DESC (negative so default ASC sort), then uid ASC
    # (earlier-registered → smaller uid → higher rank within ties).
    scored = sorted(
        all_uids,
        key=lambda uid: (-(renders.get(uid, 0) + packs.get(uid, 0)), uid),
    )
    try:
        rank = scored.index(user_id) + 1
    except ValueError:
        rank = len(scored)
    return rank, len(all_uids)


def _main_text(user: dict, profile: dict, rank: int, total_users: int) -> str:
    return t(
        "main.text",
        bot_emoji=_E_BOT,
        upload_emoji=_E_UPLOAD,
        settings_emoji=_E_SETTINGS,
        profile_emoji=_E_PROFILE,
        renders=int(user.get("total_renders") or 0),
        packs=int(profile.get("total_packs") or 0),
        rank=rank,
        total_users=total_users,
        refs=int(user.get("referrals_count") or 0),
        media_line="",
    )


_E_LOCK = '<tg-emoji emoji-id="6037249452824072506">🔒</tg-emoji>'


def _gen_ref_captcha() -> tuple[list[int], str]:
    """Pick 4 distinct random digits. Return (shuffled_digits, correct_answer)
    where correct answer is the digits sorted ascending, concatenated."""
    digits = random.sample(range(10), 4)
    return digits, "".join(str(d) for d in sorted(digits))


def _ref_captcha_text(digits: list[int], *, wrong: bool = False) -> str:
    shown = "  ".join(f"<b>{d}</b>" for d in digits)
    head = (
        f"{E.CROSS} <b>Неверно — попробуй ещё раз</b>"
        if wrong
        else f"{_E_LOCK} <b>Антибот проверка</b>"
    )
    return (
        f"{head}\n"
        f"<blockquote>"
        f"<b>Цифры:</b> {shown}\n"
        f"<b>Отправь их в порядке возрастания, слитно (например: 1357).</b>"
        f"</blockquote>"
    )


async def _start_ref_captcha(message: Message, state: FSMContext, ref_id: int) -> None:
    digits, correct = _gen_ref_captcha()
    await state.set_state(RefCaptchaSG.waiting_answer)
    await state.update_data(
        ref_captcha_answer=correct,
        ref_captcha_ref_id=ref_id,
    )
    await message.answer(
        _ref_captcha_text(digits),
        parse_mode=ParseMode.HTML,
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
    billing: Billing,
    profile_db: ProfileDB,
) -> None:
    if message.from_user is None:
        return
    free_default = await billing.get_free_default()
    created, user = await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        default_free=free_default,
        starter_balance_kop=cfg.starter_balance_kop,
    )
    # /start <payload>: referral link or shared config
    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1].strip()

    if payload.startswith("ref_") and created:
        ref_str = payload.removeprefix("ref_")
        if ref_str.isdigit():
            ref_id = int(ref_str)
            if ref_id != message.from_user.id:
                # Captcha first — referrer is credited only after the new
                # user enters 4 digits in ascending order. Blocks farming
                # via bulk-created accounts.
                job_store.clear_uploads(message.from_user.id)
                await _start_ref_captcha(message, state, ref_id)
                return
    elif payload.startswith("cfg_"):
        cfg_id_str = payload.removeprefix("cfg_")
        from handlers.configs import apply_shared_config

        await apply_shared_config(message, db, cfg, cfg_id_str)
        await state.clear()
        await state.set_state(MainSG.idle)
        return

    # Soft reset: keep settings, only clear in-flight TGS upload
    job_store.clear_uploads(message.from_user.id)
    await state.clear()
    await state.set_state(MainSG.idle)

    balance_rub = user["balance_kop"] / 100
    profile = await profile_db.get(message.from_user.id)
    rank, total_users = await _user_rank(db, profile_db, message.from_user.id)

    text = _main_text(user, profile, rank, total_users)
    kb = _kb_main_dynamic(
        balance_rub=balance_rub, free=user["free_renders"], webapp_url=cfg.webapp_url
    )

    await send_screen(message.bot, db, message.chat.id, S.MAIN_MENU, text, keyboard=kb)


@router.message(RefCaptchaSG.waiting_answer, F.text)
async def receive_ref_captcha(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
    billing: Billing,
    profile_db: ProfileDB,
) -> None:
    if message.from_user is None or message.text is None:
        return

    data = await state.get_data()
    expected = str(data.get("ref_captcha_answer") or "")
    ref_id = int(data.get("ref_captcha_ref_id") or 0)
    answer = message.text.strip()

    if not expected or answer != expected:
        # Wrong — issue a fresh challenge so brute-force is useless.
        digits, correct = _gen_ref_captcha()
        await state.update_data(ref_captcha_answer=correct)
        await message.answer(
            _ref_captcha_text(digits, wrong=True),
            parse_mode=ParseMode.HTML,
        )
        return

    # Passed — credit the referrer, then drop the user into the main menu.
    if ref_id and await db.set_referrer(message.from_user.id, ref_id):
        await db.add_balance_kop(ref_id, 500)
        with contextlib.suppress(Exception):
            await message.bot.send_message(
                ref_id,
                referral_text(5),
                parse_mode=ParseMode.HTML,
            )

    await state.update_data(
        ref_captcha_answer=None,
        ref_captcha_ref_id=None,
    )
    await state.set_state(MainSG.idle)

    user = await db.get_user(message.from_user.id)
    if user is None:
        return
    balance_rub = user["balance_kop"] / 100
    profile = await profile_db.get(message.from_user.id)
    rank, total_users = await _user_rank(db, profile_db, message.from_user.id)
    text = _main_text(user, profile, rank, total_users)
    kb = _kb_main_dynamic(
        balance_rub=balance_rub, free=user["free_renders"], webapp_url=cfg.webapp_url
    )
    await send_screen(message.bot, db, message.chat.id, S.MAIN_MENU, text, keyboard=kb)


@router.callback_query(F.data == CB.MAIN)
async def cb_back_to_main(
    callback: CallbackQuery,
    state: FSMContext,
    db: DB,
    cfg: Config,
    billing: Billing,
    profile_db: ProfileDB,
) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    user = await db.get_user(callback.from_user.id)
    if user is None:
        free = await billing.get_free_default()
        _, user = await db.upsert_user(callback.from_user.id, callback.from_user.username, free)

    await state.set_state(MainSG.idle)
    balance_rub = user["balance_kop"] / 100
    profile = await profile_db.get(callback.from_user.id)
    rank, total_users = await _user_rank(db, profile_db, callback.from_user.id)

    text = _main_text(user, profile, rank, total_users)
    kb = _kb_main_dynamic(
        balance_rub=balance_rub, free=user["free_renders"], webapp_url=cfg.webapp_url
    )

    await edit_screen(cast(Message, callback.message), db, S.MAIN_MENU, text, keyboard=kb)
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
    billing: Billing,
    profile_db: ProfileDB,
) -> None:
    if message.from_user is None:
        return
    job_store.reset(message.from_user.id)
    await state.clear()
    await state.set_state(MainSG.idle)
    await message.answer(t("errors.cancelled"), parse_mode=ParseMode.HTML)
    await cmd_start(message, state, db, cfg, billing, profile_db)


def _kb_gif_home() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=btn("main.btn.bg"),
                callback_data=CB.BG_COLOR,
                icon_custom_emoji_id="5924961243721896074",
            ),
            InlineKeyboardButton(
                text=btn("main.btn.res"),
                callback_data=CB.RESOLUTION,
                icon_custom_emoji_id=EmojiID.RESOLUTION,
            ),
        ],
        [
            InlineKeyboardButton(
                text="Цвет Emoji",
                callback_data=CB.EMOJI_COLOR,
                icon_custom_emoji_id="6035335349109002996",
            ),
            InlineKeyboardButton(
                text=btn("main.btn.media"),
                callback_data=CB.MEDIA,
                icon_custom_emoji_id=EmojiID.MEDIA_PHOTO,
            ),
        ],
        [
            InlineKeyboardButton(
                text=btn("main.btn.configs"),
                callback_data=CB.CONFIGS,
                icon_custom_emoji_id="5452165780579843515",
            ),
            InlineKeyboardButton(
                text=btn("main.btn.watermark"),
                callback_data=CB.WATERMARK,
                icon_custom_emoji_id="5346198045350459046",
            ),
        ],
        [
            InlineKeyboardButton(
                text=btn("main.btn.preview"),
                callback_data=CB.PREVIEW,
                icon_custom_emoji_id="5346297083001339175",
            )
        ],
        [
            InlineKeyboardButton(
                text="◁  Назад", callback_data=CB.MAIN, icon_custom_emoji_id=EmojiID.BACK
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gif_home_text(job) -> str:
    return t(
        "gif.home.text",
        upload_emoji=_E_UPLOAD,
        settings_emoji=_E_SETTINGS,
        summary=job.cfg.summary_html(),
    )


@router.callback_query(F.data == CB.NOOP)
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == CB.GIF_HOME)
async def cb_gif_home(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(GifSG.idle)
    job = job_store.get(callback.from_user.id)
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.GIF_HOME,
        _gif_home_text(job),
        keyboard=_kb_gif_home(),
    )
    await callback.answer()


def _kb_other() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=btn("other.btn.referral"),
                    callback_data=CB.REFERRAL,
                    icon_custom_emoji_id="5258513401784573443",
                ),
                InlineKeyboardButton(
                    text=btn("other.btn.promo"),
                    callback_data=CB.PROMO_INPUT,
                    icon_custom_emoji_id="5346044461614924803",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="◁  Назад", callback_data=CB.MAIN, icon_custom_emoji_id=EmojiID.BACK
                )
            ],
        ]
    )


@router.callback_query(F.data == CB.OTHER_HOME)
async def cb_other_home(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(MainSG.idle)
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.OTHER,
        t("other.text"),
        keyboard=_kb_other(),
    )
    await callback.answer()


@router.callback_query(F.data == CB.PROMO_INPUT)
async def cb_promo_prompt(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(PromoSG.waiting_code)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад", callback_data=CB.OTHER_HOME, icon_custom_emoji_id=EmojiID.BACK
                ),
            ]
        ]
    )
    msg = cast(Message, callback.message)
    await edit_screen(msg, db, S.PROMO, t("promo.prompt"), keyboard=kb)
    await state.update_data(
        promo_anchor_chat=msg.chat.id,
        promo_anchor_msg=msg.message_id,
    )
    await callback.answer()


@router.message(PromoSG.waiting_code, F.text)
async def receive_promo(message: Message, state: FSMContext, db: DB, promo_db: PromoDB) -> None:
    if message.from_user is None or message.text is None:
        return
    code = message.text.strip()
    if not code or len(code) > 64:
        await message.reply(t("promo.not_found"), parse_mode=ParseMode.HTML)
        return

    status, promo = await promo_db.try_consume(code, message.from_user.id)
    if status == "ok" and promo:
        try:
            await db.add_balance_kop(message.from_user.id, int(promo["balance_kop"]))
        except Exception:
            await promo_db.revert_consume(int(promo["id"]), message.from_user.id)
            status = "not_found"
            promo = None
    await state.set_state(MainSG.idle)

    data = await state.get_data()
    anchor_chat = data.get("promo_anchor_chat")
    anchor_msg = data.get("promo_anchor_msg")
    await state.update_data(promo_anchor_chat=None, promo_anchor_msg=None)

    with contextlib.suppress(Exception):
        await message.delete()

    if status == "ok" and promo:
        amount = promo["balance_kop"] // 100
        user = await db.get_user(message.from_user.id)
        balance = (user["balance_kop"] // 100) if user else amount
        text = t("promo.ok", amount=amount, balance=balance)
    elif status == "exhausted":
        text = t("promo.exhausted")
    elif status == "used":
        text = t("promo.used")
    else:
        text = t("promo.not_found")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад", callback_data=CB.OTHER_HOME, icon_custom_emoji_id=EmojiID.BACK
                ),
            ]
        ]
    )

    bot = message.bot
    if anchor_chat and anchor_msg and bot is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=anchor_chat,
                message_id=anchor_msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            return
        except Exception:
            try:
                await bot.edit_message_caption(
                    chat_id=anchor_chat,
                    message_id=anchor_msg,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                return
            except Exception:
                pass
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data == CB.REFERRAL)
async def cb_referral(callback: CallbackQuery, state: FSMContext, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    count = await db.get_referrals_count(callback.from_user.id)

    text = (
        f'<tg-emoji emoji-id="5258513401784573443">🤝</tg-emoji> '
        f"<b>Рефералка</b>\n"
        f"<blockquote><b>За каждого приглашённого человека "
        f"+5₽ на баланс</b></blockquote>\n"
        f'<tg-emoji emoji-id="5258330865674494479">📊</tg-emoji> '
        f"<b>Статистика: {count} юзеров</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Реф ссылка",
                    callback_data=CB.REF_COPY,
                    icon_custom_emoji_id="5316612764427367709",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.OTHER_HOME,
                    icon_custom_emoji_id=EmojiID.BACK,
                )
            ],
        ]
    )

    msg = cast(Message, callback.message)
    await safe_edit(msg, text, keyboard=kb)
    await callback.answer()


@router.callback_query(F.data == CB.REF_COPY)
async def cb_ref_copy(callback: CallbackQuery, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    bot_username = cfg.bot_username or "bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{callback.from_user.id}"
    text = (
        f'<tg-emoji emoji-id="5316612764427367709">🔗</tg-emoji> '
        f"<b>Твоя реф ссылка</b>\n"
        f"<blockquote><b>Тапни на ссылку — она скопируется:</b></blockquote>\n"
        f"<code>{ref_link}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Поделиться",
                    url=f"https://t.me/share/url?url={ref_link}",
                    icon_custom_emoji_id="5316612764427367709",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.REFERRAL,
                    icon_custom_emoji_id=EmojiID.BACK,
                )
            ],
        ]
    )
    msg = cast(Message, callback.message)
    await safe_edit(msg, text, keyboard=kb)
    await callback.answer()


def _info_text(cfg: Config) -> str:
    links = []
    if cfg.privacy_url:
        links.append(f'<a href="{cfg.privacy_url}">Политика конфиденциальности</a>')
    if cfg.terms_url:
        links.append(f'<a href="{cfg.terms_url}">Пользовательское соглашение</a>')
    if cfg.support_username:
        links.append(
            f'По вопросам оплаты — <a href="https://t.me/{cfg.support_username}">'
            f"@{cfg.support_username}</a>"
        )
    body = "<blockquote expandable>" + "\n".join(links) + "</blockquote>" if links else ""
    return f'<tg-emoji emoji-id="5983568653751160844">ℹ️</tg-emoji> <b>Информация</b>\n\n{body}'


@router.callback_query(F.data == CB.INFO)
async def cb_info(callback: CallbackQuery, state: FSMContext, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.MAIN,
                    icon_custom_emoji_id=EmojiID.BACK,
                ),
            ]
        ]
    )
    await edit_screen(cast(Message, callback.message), db, S.INFO, _info_text(cfg), keyboard=kb)
    await callback.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, cfg: Config) -> None:
    if message.from_user is None:
        return
    if message.from_user.id not in cfg.admin_ids:
        return
    from keyboards.inline import kb_admin_home

    await state.set_state(AdminSG.idle)
    await message.answer(
        t("admin.home"),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin_home(),
    )


@router.callback_query(F.data == CB.ADM_HOME)
async def cb_admin_home(callback: CallbackQuery, state: FSMContext, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    if callback.from_user.id not in cfg.admin_ids:
        await callback.answer()
        return
    from keyboards.inline import kb_admin_home

    await state.set_state(AdminSG.idle)
    msg = cast(Message, callback.message)
    try:
        await msg.edit_text(
            t("admin.home"), parse_mode=ParseMode.HTML, reply_markup=kb_admin_home()
        )
    except Exception:
        await msg.answer(t("admin.home"), parse_mode=ParseMode.HTML, reply_markup=kb_admin_home())
    await callback.answer()
