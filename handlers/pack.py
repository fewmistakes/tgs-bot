"""Emoji pack creation flow: nickname, font, format, logo, preview, build.

Every text is overridable from the admin panel and every screen can carry a
media header (see services.screens).
"""

from __future__ import annotations

import contextlib
import html
from pathlib import Path
from typing import cast

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from config import BASE_DIR, TMP_DIR, Config
from db import DB
from keyboards.cb import CB
from services.billing import Billing
from services.content import btn, t
from services.pack_builder import build_pack
from services.pack_preview import build_preview_grid
from services.profile_db import ProfileDB
from services.screens import S, edit_screen, safe_edit
from services.svg_to_lottie import svg_to_lottie_shapes
from states import MainSG, PackSG, pack_store
from utils.emoji import E, EmojiID
from utils.logger import get_logger

log = get_logger("h.pack")

# Project-relative master pack + font.
MASTER_PACK_DIR = BASE_DIR / "master_pack"
FONT_PATH = BASE_DIR / "fonts" / "SeymourOne-Regular.ttf"

router = Router(name="pack")


_SVG_CONVERTER_URL = "https://convertio.co/ru/png-svg/"

# Premium emoji IDs for the four feature buttons
_E_NICK = "5812150667812280629"
_E_FONT = "5400214560542060148"
_E_FORMAT = "5884366771913233289"
_E_LOGO = "6021792097454002931"
_E_PREVIEW = "6037397706505195857"


# Curated font list — keys are stable, labels are human-readable.
PACK_FONTS: list[tuple[str, str]] = [
    ("seymour", "Seymour One"),
    ("rubik", "Rubik"),
    ("montserrat", "Montserrat"),
    ("press", "Press Start 2P"),
    ("russo", "Russo One"),
    ("pacifico", "Pacifico"),
]
_FONTS_BY_KEY = dict(PACK_FONTS)

# Map pack-font keys to Google Fonts family names. 'seymour' is bundled
# locally and resolves to FONT_PATH directly.
_GFONT_FAMILY = {
    "rubik": "Rubik",
    "montserrat": "Montserrat",
    "press": "Press Start 2P",
    "russo": "Russo One",
    "pacifico": "Pacifico",
}


def _resolve_font_path(font_key: str | None) -> Path:
    """Map a pack font key to a TTF/OTF file.

    'seymour', unset and unknown keys resolve to the bundled SeymourOne. Other keys
    are downloaded from Google Fonts into the shared data/fonts cache; if that
    fails the bundled font is used so the build still succeeds, and the reason is
    logged.
    """
    if not font_key or font_key == "seymour":
        return FONT_PATH
    family = _GFONT_FAMILY.get(font_key)
    if not family:
        log.warning("pack font: unknown key %r → fallback Seymour", font_key)
        return FONT_PATH
    from services.watermark import _gfont_path

    p = _gfont_path(family)
    if p:
        log.info("pack font %r → %s", font_key, p)
        return Path(p)
    log.warning(
        "pack font %r (family %r): _gfont_path returned None → fallback Seymour",
        font_key,
        family,
    )
    return FONT_PATH


_FORMAT_LABEL = {
    "sticker": ("pack.format.value.sticker", "pack.format.btn.sticker"),
    "emoji": ("pack.format.value.emoji", "pack.format.btn.emoji"),
}


def _pack_text(user_id: int) -> str:
    # The default template no longer has nick/font/format/logo placeholders,
    # but admin overrides stored in the DB still may; pass them through so
    # str.format() doesn't raise KeyError.
    s = pack_store.get(user_id)
    nick = html.escape(s.nick, quote=False) if s.nick else t("pack.value.unset")
    font = _FONTS_BY_KEY.get(s.font or "", t("pack.value.unset"))
    fmt = t(_FORMAT_LABEL[s.format][0]) if s.format in _FORMAT_LABEL else t("pack.value.unset")
    logo = t("pack.value.set") if s.logo_svg else t("pack.value.unset")
    label = t("pack.example.label")
    ex_nick = f'<a href="{t("pack.example.nick_url")}">[{label}]</a>'
    ex_logo = f'<a href="{t("pack.example.logo_url")}">[{label}]</a>'
    ex_sticker = f'<a href="{t("pack.example.sticker_url")}">[{label}]</a>'
    return t(
        "pack.home",
        nick=nick,
        font=font,
        format=fmt,
        logo=logo,
        ex_nick=ex_nick,
        ex_logo=ex_logo,
        ex_sticker=ex_sticker,
    )


def _kb_pack_home(*, price_rub: int, webapp_url: str = "") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if webapp_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn("pack.btn.editor"),
                    web_app=WebAppInfo(url=webapp_url),
                    icon_custom_emoji_id="5346136511354004471",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◁  Назад", callback_data=CB.MAIN, icon_custom_emoji_id=EmojiID.BACK
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_logo_screen(has_logo: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=btn("pack.logo.btn.convert"),
                web_app=WebAppInfo(url=_SVG_CONVERTER_URL),
                icon_custom_emoji_id=_E_LOGO,
            ),
        ]
    ]
    if has_logo:
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn("pack.logo.btn.delete"),
                    callback_data=CB.PACK_LOGO_DEL,
                    icon_custom_emoji_id=EmojiID.TRASH,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◁  Назад",
                callback_data=CB.PACK_HOME,
                icon_custom_emoji_id=EmojiID.BACK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_back_to_pack() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад", callback_data=CB.PACK_HOME, icon_custom_emoji_id=EmojiID.BACK
                ),
            ]
        ]
    )


async def _replace_prompt(
    bot: Bot,
    chat_id: int,
    msg_id: int,
    text: str,
    kb: InlineKeyboardMarkup,
) -> bool:
    """Replace a previously shown prompt screen with new content.

    The prompt may be a text message or a captioned media message (when the admin
    attached a header), so both edits are attempted.
    """
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return True
    except Exception as exc:
        if "message is not modified" in str(exc):
            return True
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=msg_id,
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return True
    except Exception as exc:
        log.debug("prompt edit failed: %s", exc)
        return False


def _btn_with_select(label: str, cb: str, emoji_id: str, selected: bool) -> InlineKeyboardButton:
    kw: dict = {
        "text": label,
        "callback_data": cb,
        "icon_custom_emoji_id": emoji_id,
    }
    if selected:
        kw["style"] = "success"
    return InlineKeyboardButton(**kw)


def _kb_font(current: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for key, label in PACK_FONTS:
        pair.append(
            _btn_with_select(
                label,
                f"{CB.PACK_FONT_SET}{key}",
                _E_FONT,
                current == key,
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [
            InlineKeyboardButton(
                text="◁  Назад",
                callback_data=CB.PACK_HOME,
                icon_custom_emoji_id=EmojiID.BACK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_format(current: str | None) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn_with_select(
                btn("pack.format.btn.sticker"),
                f"{CB.PACK_FORMAT_SET}sticker",
                _E_FORMAT,
                current == "sticker",
            ),
            _btn_with_select(
                btn("pack.format.btn.emoji"),
                f"{CB.PACK_FORMAT_SET}emoji",
                _E_FORMAT,
                current == "emoji",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◁  Назад",
                callback_data=CB.PACK_HOME,
                icon_custom_emoji_id=EmojiID.BACK,
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _price_for(user_id: int, billing: Billing) -> int:
    if pack_store.get(user_id).logo_svg:
        return await billing.get_pack_logo_price_rub()
    return await billing.get_pack_price_rub()


@router.callback_query(F.data == CB.PACK_HOME)
async def pack_home(
    callback: CallbackQuery, state: FSMContext, db: DB, billing: Billing, cfg: Config
) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(MainSG.idle)
    price = await _price_for(callback.from_user.id, billing)
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.PACK,
        _pack_text(callback.from_user.id),
        keyboard=_kb_pack_home(price_rub=price, webapp_url=cfg.webapp_url),
    )
    with contextlib.suppress(Exception):
        await callback.answer()


@router.callback_query(F.data == CB.PACK_EDITOR)
async def pack_editor_open(callback: CallbackQuery, cfg: Config) -> None:
    """Send a reply keyboard with a web_app button.

    Only mini-apps opened this way can return data to the bot via sendData
    (`web_app_data`).
    """
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    if not cfg.webapp_url:
        await callback.answer("Редактор не настроен", show_alert=True)
        return
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🎨 Открыть редактор",
                    web_app=WebAppInfo(url=cfg.webapp_url),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    await callback.message.answer(
        "<blockquote><b>Жми «Открыть редактор» снизу — настроишь "
        "позиции для каждого эмодзи и тапнешь «Создать пак».</b></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    await callback.answer()


@router.message(F.web_app_data)
async def pack_editor_data(
    message: Message, bot: Bot, db: DB, billing: Billing, cfg: Config, profile_db
) -> None:
    """Settings submitted from the mini-app: start the pack build."""
    if message.from_user is None or message.web_app_data is None:
        return
    import base64
    import json as _json

    raw = message.web_app_data.data or "{}"
    try:
        payload = _json.loads(raw)
    except Exception as exc:
        await message.answer(
            f"<blockquote>{E.CROSS} <b>Не понял данные из редактора: "
            f"<code>{exc}</code></b></blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if payload.get("type") != "build_pack_editor":
        await message.answer(
            f"<blockquote>{E.CROSS} <b>Неизвестный тип данных от редактора</b></blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    raw_overrides = payload.get("per_emoji") or {}
    per_emoji: dict = {}
    for k, v in raw_overrides.items():
        try:
            idx = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, dict):
            continue
        ov = {
            "kind": str(v.get("kind") or "text"),
            "text": str(v.get("text") or ""),
            "scale": float(v.get("scale", 1.0)),
            "offset_x": float(v.get("offset_x", 0.0)),
            "offset_y": float(v.get("offset_y", 0.0)),
            "rotation": float(v.get("rotation", 0.0)),
        }
        b64 = v.get("logo_svg_b64")
        if b64:
            with contextlib.suppress(Exception):
                ov["logo_svg"] = base64.b64decode(b64)
        per_emoji[idx] = ov

    if not per_emoji:
        await message.answer(
            f"<blockquote>{E.CROSS} <b>Нет ни одного изменённого эмодзи — "
            f"отредактируй хоть один тайл, прежде чем создавать.</b></blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    s = pack_store.get(message.from_user.id)
    # Mini-app value first, then pack_store, then the emoji default.
    raw_fmt = str(payload.get("format") or "").lower().strip()
    pack_format = raw_fmt if raw_fmt in {"emoji", "sticker"} else (s.format or "emoji")
    with_logo = bool(s.logo_svg) or any(
        ov.get("kind") == "logo" and ov.get("logo_svg") for ov in per_emoji.values()
    )

    user = await db.get_user(message.from_user.id)
    if user is None:
        await message.answer(
            f"<blockquote>{E.CROSS} <b>Юзер не найден в БД</b></blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    price_rub = (
        (await billing.get_pack_logo_price_rub())
        if with_logo
        else (await billing.get_pack_price_rub())
    )
    price_kop = price_rub * 100
    if (user.get("balance_kop") or 0) < price_kop:
        await message.answer(
            t("pack.no_funds", balance=f"{user['balance_kop'] / 100:.0f}", price=price_rub),
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    ok, balance_kop, price_kop = await billing.charge_pack(
        message.from_user.id,
        with_logo=with_logo,
    )
    if not ok:
        await message.answer(
            t("pack.no_funds", balance=f"{balance_kop / 100:.0f}", price=price_kop // 100),
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    progress = await message.answer(
        t("pack.progress", i=0, total="…"),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )

    bot_username = cfg.bot_username or (await bot.get_me()).username or "bot"
    title_suffix = f" by @{bot_username}"
    progress_html = (
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        "<b>Создаю пак…</b>\n"
        "<blockquote><b>Готово: {i}/{total}</b></blockquote>"
    )
    try:
        ok_build, err_html, info = await build_pack(
            bot=bot,
            user_id=message.from_user.id,
            nick=s.nick or "",
            master_pack_dir=MASTER_PACK_DIR,
            font_path=_resolve_font_path(s.font),
            bot_username=bot_username,
            pack_format=pack_format,
            title_suffix=title_suffix,
            logo_svg=s.logo_svg,
            status=progress,
            progress_template_html=progress_html,
            per_emoji=per_emoji,
        )
    except Exception as exc:
        log.exception("editor pack build crashed")
        await billing.refund_pack(message.from_user.id, price_kop)
        await safe_edit(
            progress,
            t("pack.error", detail=f"<code>{exc}</code>"),
            keyboard=_kb_back_to_pack(),
        )
        return

    if not ok_build:
        await billing.refund_pack(message.from_user.id, price_kop)
        await safe_edit(
            progress,
            t("pack.error", detail=err_html),
            keyboard=_kb_back_to_pack(),
        )
        return

    total = info.get("total", 0)
    skipped = len(info.get("skipped") or [])
    truncated = info.get("truncated", 0)
    count = max(0, total - skipped - truncated)
    link = info.get("link", "")
    label = html.escape(s.nick or ("Лого" if with_logo else "swex"), quote=False)
    with contextlib.suppress(Exception):
        await profile_db.add_pack_stats(message.from_user.id, count)
    await safe_edit(
        progress,
        t("pack.success", nick=label, count=count, price=price_kop // 100, link=link),
        keyboard=_kb_back_to_pack(),
    )


@router.callback_query(F.data == CB.PACK_NICK)
async def pack_nick_prompt(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(PackSG.waiting_nick)
    msg = cast(Message, callback.message)
    await edit_screen(
        msg,
        db,
        S.PACK_NICK,
        t("pack.nick.prompt"),
        keyboard=_kb_back_to_pack(),
    )
    # Remember the prompt so it can be removed as soon as the nick arrives;
    # otherwise a stale "enter nick" screen with a dead Back button is left behind.
    await state.update_data(
        nick_prompt_chat_id=msg.chat.id,
        nick_prompt_msg_id=msg.message_id,
    )
    await callback.answer()


@router.message(PackSG.waiting_nick, F.text)
async def pack_nick_set(message: Message, state: FSMContext, bot: Bot, db: DB) -> None:
    if message.from_user is None or message.text is None:
        return
    nick = message.text.strip()[:30]
    if not nick:
        await message.reply(t("pack.nick.prompt"))
        return
    pack_store.get(message.from_user.id).nick = nick
    await state.set_state(MainSG.idle)

    data = await state.get_data()
    prompt_chat_id = data.get("nick_prompt_chat_id")
    prompt_msg_id = data.get("nick_prompt_msg_id")
    await state.update_data(nick_prompt_chat_id=None, nick_prompt_msg_id=None)

    # Keep the chat clean: only the bot's screen stays.
    with contextlib.suppress(Exception):
        await message.delete()

    text = t("pack.nick.set", nick=html.escape(nick, quote=False))
    kb = _kb_back_to_pack()

    edited = False
    if prompt_chat_id and prompt_msg_id:
        edited = await _replace_prompt(bot, prompt_chat_id, prompt_msg_id, text, kb)
    if not edited:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data == CB.PACK_FONT)
async def pack_font_open(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(MainSG.idle)
    s = pack_store.get(callback.from_user.id)
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.PACK_FONT,
        t("pack.font.prompt"),
        keyboard=_kb_font(s.font),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.PACK_FONT_SET))
async def pack_font_set(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        await callback.answer()
        return
    key = callback.data[len(CB.PACK_FONT_SET) :]
    if key not in _FONTS_BY_KEY:
        await callback.answer()
        return
    pack_store.get(callback.from_user.id).font = key
    await callback.answer(_FONTS_BY_KEY[key])
    # Re-render font screen with the new selection highlighted.
    await safe_edit(
        cast(Message, callback.message),
        t("pack.font.prompt"),
        keyboard=_kb_font(key),
    )


@router.callback_query(F.data == CB.PACK_FORMAT)
async def pack_format_open(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(MainSG.idle)
    s = pack_store.get(callback.from_user.id)
    await edit_screen(
        cast(Message, callback.message),
        db,
        S.PACK_FORMAT,
        t("pack.format.prompt"),
        keyboard=_kb_format(s.format),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(CB.PACK_FORMAT_SET))
async def pack_format_set(callback: CallbackQuery, db: DB) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        await callback.answer()
        return
    val = callback.data[len(CB.PACK_FORMAT_SET) :]
    if val not in {"sticker", "emoji"}:
        await callback.answer()
        return
    pack_store.get(callback.from_user.id).format = val
    await callback.answer(t(_FORMAT_LABEL[val][0]))
    await safe_edit(
        cast(Message, callback.message),
        t("pack.format.prompt"),
        keyboard=_kb_format(val),
    )


@router.callback_query(F.data == CB.PACK_LOGO)
async def pack_logo_open(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    await state.set_state(PackSG.waiting_logo)
    has_logo = bool(pack_store.get(callback.from_user.id).logo_svg)
    msg = cast(Message, callback.message)
    await edit_screen(
        msg,
        db,
        S.PACK_PAINT,
        t("pack.logo.prompt"),
        keyboard=_kb_logo_screen(has_logo),
    )
    # Kept so it can be replaced with the "logo applied" screen later.
    await state.update_data(
        logo_prompt_chat_id=msg.chat.id,
        logo_prompt_msg_id=msg.message_id,
    )
    await callback.answer()


@router.callback_query(F.data == CB.PACK_LOGO_DEL)
async def pack_logo_delete(callback: CallbackQuery, state: FSMContext, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    pack_store.get(callback.from_user.id).logo_svg = None
    await callback.answer("Удалено")
    await safe_edit(
        cast(Message, callback.message),
        t("pack.logo.prompt"),
        keyboard=_kb_logo_screen(has_logo=False),
    )


@router.message(PackSG.waiting_logo, F.document)
async def pack_logo_receive(message: Message, state: FSMContext, bot: Bot, db: DB) -> None:
    if message.from_user is None or message.document is None:
        return
    doc = message.document
    name = (doc.file_name or "").lower()
    mime = (doc.mime_type or "").lower()
    if not (name.endswith(".svg") or "svg" in mime):
        await message.reply(
            t(
                "pack.logo.bad_svg",
                detail="<b>Это не SVG.</b> Пришли файл с расширением <code>.svg</code>.",
            )
        )
        return
    # Cap to 256KB — SVGs above that almost always have raster embeds we can't use
    if doc.file_size and doc.file_size > 256 * 1024:
        await message.reply(
            t(
                "pack.logo.bad_svg",
                detail=(f"<b>SVG слишком большой ({doc.file_size / 1024:.0f}KB).</b> Лимит 256KB."),
            )
        )
        return

    # The "downloading logo" status is removed on every exit path.
    status: Message | None = None
    try:
        status = await message.reply(
            t("pack.logo.downloading"),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        status = None

    async def _drop_status() -> None:
        if status is None:
            return
        with contextlib.suppress(Exception):
            await status.delete()

    f = await bot.get_file(doc.file_id)
    buf = await bot.download_file(f.file_path)
    svg_bytes = buf.read() if buf else b""
    if not svg_bytes:
        await _drop_status()
        await message.reply(t("pack.logo.bad_svg", detail="<b>Пустой файл.</b>"))
        return
    try:
        shapes, _bbox = svg_to_lottie_shapes(svg_bytes)
    except Exception as exc:
        log.exception("svg parse failed")
        await _drop_status()
        await message.reply(t("pack.logo.bad_svg", detail=f"<code>{exc}</code>"))
        return
    if not shapes:
        await _drop_status()
        await message.reply(
            t(
                "pack.logo.bad_svg",
                detail=(
                    "<b>В SVG нет путей.</b> Сконвертируй файл через "
                    "«Конвертировать в SVG» и пришли получившийся."
                ),
            )
        )
        return

    await _drop_status()
    pack_store.get(message.from_user.id).logo_svg = svg_bytes
    await state.set_state(MainSG.idle)

    data = await state.get_data()
    prompt_chat_id = data.get("logo_prompt_chat_id")
    prompt_msg_id = data.get("logo_prompt_msg_id")
    await state.update_data(logo_prompt_chat_id=None, logo_prompt_msg_id=None)

    # Keep the chat clean: only the bot's screen stays.
    with contextlib.suppress(Exception):
        await message.delete()

    text = t(
        "pack.logo.set",
        name=doc.file_name or "logo.svg",
        paths=len(shapes),
    )
    kb = _kb_back_to_pack()

    edited = False
    if prompt_chat_id and prompt_msg_id:
        edited = await _replace_prompt(bot, prompt_chat_id, prompt_msg_id, text, kb)
    if not edited:
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data == CB.PACK_PREVIEW)
async def pack_preview(callback: CallbackQuery, bot: Bot, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    s = pack_store.get(callback.from_user.id)
    if not s.nick and not s.logo_svg:
        await callback.answer()
        await safe_edit(
            cast(Message, callback.message),
            t("pack.preview.empty"),
            keyboard=_kb_back_to_pack(),
        )
        return

    await callback.answer()

    # Progress goes to a separate message so the home screen and its keyboard
    # stay intact after the preview.
    msg = cast(Message, callback.message)
    progress = await msg.answer(
        t("pack.preview.progress"),
        parse_mode=ParseMode.HTML,
    )

    bot_username = cfg.bot_username or (await bot.get_me()).username or "bot"

    try:
        out_path = await build_preview_grid(
            master_pack_dir=MASTER_PACK_DIR,
            font_path=_resolve_font_path(s.font),
            nick=s.nick or "",
            logo_svg_bytes=s.logo_svg,
            bot_username=bot_username,
            tmp_dir=TMP_DIR,
            user_id=callback.from_user.id,
        )
    except Exception as exc:
        log.exception("preview build failed")
        with contextlib.suppress(Exception):
            await progress.delete()
        await msg.answer(
            t("pack.preview.error", detail=f"<code>{type(exc).__name__}: {exc}</code>"),
            parse_mode=ParseMode.HTML,
        )
        return

    kind = t("pack.preview.kind.logo" if s.logo_svg else "pack.preview.kind.nick")
    caption = t("pack.preview.caption", kind=kind, bot_username=bot_username)

    try:
        await msg.answer_photo(
            FSInputFile(out_path, filename="preview.png"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    finally:
        with contextlib.suppress(Exception):
            await progress.delete()
        with contextlib.suppress(OSError):
            out_path.unlink()


def _kb_pack_confirm(price_rub: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=btn("pack.btn.confirm", price=price_rub),
                    callback_data=CB.PACK_CONFIRM,
                    icon_custom_emoji_id=EmojiID.CHECK,
                )
            ],
            [
                InlineKeyboardButton(
                    text=btn("pack.btn.cancel"),
                    callback_data=CB.PACK_HOME,
                    icon_custom_emoji_id=EmojiID.CROSS,
                )
            ],
        ]
    )


@router.callback_query(F.data == CB.PACK_APPLY)
async def pack_apply(callback: CallbackQuery, billing: Billing, db: DB) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    s = pack_store.get(callback.from_user.id)
    if not s.nick and not s.logo_svg:
        await callback.answer()
        await safe_edit(
            cast(Message, callback.message),
            t("pack.no_nick"),
            keyboard=_kb_back_to_pack(),
        )
        return

    user = await db.get_user(callback.from_user.id)
    price = await _price_for(callback.from_user.id, billing)
    balance_kop = user["balance_kop"] if user else 0
    if balance_kop < price * 100:
        await callback.answer()
        await safe_edit(
            cast(Message, callback.message),
            t("pack.no_funds", balance=f"{balance_kop / 100:.0f}", price=price),
            keyboard=_kb_back_to_pack(),
        )
        return

    label = html.escape(s.nick or ("Лого" if s.logo_svg else ""), quote=False)
    await callback.answer()
    await safe_edit(
        cast(Message, callback.message),
        t("pack.confirm", nick=label, price=price),
        keyboard=_kb_pack_confirm(price),
    )


@router.callback_query(F.data == CB.PACK_CONFIRM)
async def pack_confirm(
    callback: CallbackQuery, bot: Bot, db: DB, billing: Billing, cfg: Config, profile_db: ProfileDB
) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return
    s = pack_store.get(callback.from_user.id)
    if not s.nick and not s.logo_svg:
        await callback.answer()
        await safe_edit(
            cast(Message, callback.message),
            t("pack.no_nick"),
            keyboard=_kb_back_to_pack(),
        )
        return

    with_logo = bool(s.logo_svg)
    ok, balance_kop, price_kop = await billing.charge_pack(
        callback.from_user.id,
        with_logo=with_logo,
    )
    if not ok:
        price_rub = price_kop // 100
        await callback.answer()
        await safe_edit(
            cast(Message, callback.message),
            t("pack.no_funds", balance=f"{balance_kop / 100:.0f}", price=price_rub),
            keyboard=_kb_back_to_pack(),
        )
        return

    await callback.answer("Стартую…")
    await safe_edit(
        cast(Message, callback.message),
        t("pack.progress", i=0, total="…"),
        keyboard=None,
    )

    bot_username = cfg.bot_username or (await bot.get_me()).username or "bot"
    title_suffix = f" by @{bot_username}"
    progress_html = (
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        "<b>Создаю пак…</b>\n"
        "<blockquote><b>Готово: {i}/{total}</b></blockquote>"
    )

    try:
        ok_build, err_html, info = await build_pack(
            bot=bot,
            user_id=callback.from_user.id,
            nick=s.nick or "",
            master_pack_dir=MASTER_PACK_DIR,
            font_path=_resolve_font_path(s.font),
            bot_username=bot_username,
            pack_format=s.format or "emoji",
            title_suffix=title_suffix,
            logo_svg=s.logo_svg,
            status=cast(Message, callback.message),
            progress_template_html=progress_html,
        )
    except Exception as exc:
        log.exception("pack build crashed")
        await billing.refund_pack(callback.from_user.id, price_kop)
        await safe_edit(
            cast(Message, callback.message),
            t("pack.error", detail=f"<code>{exc}</code>"),
            keyboard=_kb_back_to_pack(),
        )
        return

    if not ok_build:
        await billing.refund_pack(callback.from_user.id, price_kop)
        await safe_edit(
            cast(Message, callback.message),
            t("pack.error", detail=err_html),
            keyboard=_kb_back_to_pack(),
        )
        return

    total = info.get("total", 0)
    skipped = len(info.get("skipped") or [])
    truncated = info.get("truncated", 0)
    count = max(0, total - skipped - truncated)
    link = info.get("link", "")
    price_rub = price_kop // 100
    label = html.escape(s.nick or ("Лого" if with_logo else ""), quote=False)
    await profile_db.add_pack_stats(callback.from_user.id, count)
    await safe_edit(
        cast(Message, callback.message),
        t("pack.success", nick=label, count=count, price=price_rub, link=link),
        keyboard=_kb_back_to_pack(),
    )
