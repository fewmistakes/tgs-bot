"""Configs — save / load / find-by-id render presets.

Flow:
    /m:cfg            → list of saved presets + actions
    cfg:save          → ask for name → save current settings (bg media is
                                       copied into data/configs/{uid}/{cid})
    cfg:load:{id}     → restore preset (settings + bg media) into current job
    cfg:del:{id}      → delete preset and unlink its persisted bg
    cfg:findid        → prompt for an ID, load that preset (any owner)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

from aiogram import F, Router
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
from services.screens import S, edit_screen, safe_edit
from services.user_config import UserConfig
from states import ConfigsSG, GifSG, job_store
from utils.emoji import EmojiID

router = Router(name="configs")


_E_BOX = '<tg-emoji emoji-id="5452165780579843515">📦</tg-emoji>'
_E_BRUSH = '<tg-emoji emoji-id="5924961243721896074">🖌</tg-emoji>'
_E_COLOR = '<tg-emoji emoji-id="6035335349109002996">🎨</tg-emoji>'
_E_RES = '<tg-emoji emoji-id="5778479949572738874">↔️</tg-emoji>'
_E_FMT = '<tg-emoji emoji-id="5940433880585605708">🔨</tg-emoji>'
_E_CHECK = '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji>'
_E_CROSS = '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji>'
_E_LINK = '<tg-emoji emoji-id="5769289093221454192">🔗</tg-emoji>'
_E_WRITE = '<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji>'
_E_TRASH = '<tg-emoji emoji-id="5870875489362513438">🗑</tg-emoji>'
_E_MEDIA = '<tg-emoji emoji-id="6035128606563241721">🖼</tg-emoji>'


_DATA_DIR = Path("data")
_CFG_BG_DIR = _DATA_DIR / "configs"


def _persist_bg(user_id: int, cid: int, src: Path, kind: str) -> str:
    """Copy bg file to data/configs/{user_id}/{cid}.{ext}.

    Returns the path relative to data/ — what's stored inside the JSON blob."""
    ext = src.suffix or (".mp4" if kind == "video" else ".jpg")
    dst_dir = _CFG_BG_DIR / str(user_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{cid}{ext}"
    shutil.copy2(src, dst)
    return dst.relative_to(_DATA_DIR).as_posix()


def _restore_bg_to_tmp(user_id: int, cid: int, rel_path: str) -> Path | None:
    """Copy the persistent bg back into data/tmp so cleanup_bg can safely
    unlink the working copy without nuking the saved preset."""
    src = _DATA_DIR / rel_path
    if not src.exists():
        return None
    dst = _DATA_DIR / "tmp" / f"cfg_bg_{user_id}_{cid}{src.suffix}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def _delete_persisted_bg(rel_path: str) -> None:
    if not rel_path:
        return
    p = _DATA_DIR / rel_path
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _apply_bg_from_data(user_id: int, cid: int, data: dict) -> tuple[Path | None, str]:
    """Return (tmp_bg_path, kind) restored from a config dict, or (None, 'none')."""
    bg_kind = data.get("_bg_kind", "none")
    bg_rel = data.get("_bg_file")
    if bg_kind == "none" or not bg_rel:
        return None, "none"
    restored = _restore_bg_to_tmp(user_id, cid, bg_rel)
    if not restored:
        return None, "none"
    return restored, bg_kind


def _summary_html(d: dict) -> str:
    bg = d.get("bg_color", "#000000")
    emoji = d.get("emoji_color") or "—"
    w = d.get("width", 1920)
    h = d.get("height", 530)
    fmt = (d.get("fmt", "mp4") or "mp4").upper()
    media_kind = d.get("_bg_kind", "none")
    media_label = {
        "image": "фото",
        "video": "видео",
        "none": "—",
    }.get(media_kind, "—")
    return (
        f"<blockquote>"
        f"{_E_BRUSH} <b>Фон: <code>{bg}</code></b>\n"
        f"{_E_COLOR} <b>Цвет emoji: <code>{emoji}</code></b>\n"
        f"{_E_RES} <b>Разрешение: <code>{w}×{h}</code></b>\n"
        f"{_E_FMT} <b>Формат: <code>{fmt}</code></b>\n"
        f"{_E_MEDIA} <b>Медиа: <code>{media_label}</code></b>"
        f"</blockquote>"
    )


def _kb_main(presets: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in presets:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{p['id']}  {p['name']}",
                    callback_data=f"{CB.CFG_LOAD}{p['id']}",
                    icon_custom_emoji_id="5452165780579843515",
                ),
                InlineKeyboardButton(
                    text="·",
                    callback_data=f"{CB.CFG_DELETE}{p['id']}",
                    icon_custom_emoji_id="5870875489362513438",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Найти по ID",
                callback_data=CB.CFG_FIND_ID,
                icon_custom_emoji_id="5769289093221454192",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Сохранить текущий",
                callback_data=CB.CFG_SAVE_NEW,
                icon_custom_emoji_id="5870633910337015697",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="◁  Назад",
                callback_data=CB.GIF_HOME,
                icon_custom_emoji_id=EmojiID.BACK,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_back_to_configs() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  Назад",
                    callback_data=CB.CONFIGS,
                    icon_custom_emoji_id=EmojiID.BACK,
                ),
            ]
        ]
    )


@router.callback_query(F.data == CB.CONFIGS)
async def open_configs(callback: CallbackQuery, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.message is None:
        return
    presets = await db.list_configs(callback.from_user.id)
    job = job_store.get(callback.from_user.id)
    count = len(presets)

    head = f"{_E_BOX} <b>Конфиги</b>"
    if count:
        intro = (
            f"<b>Сохранено: {count}</b>\n"
            f"<b>Тапни на пресет — загрузится. Кнопка справа — удалить.</b>"
        )
    else:
        intro = (
            "<b>Пусто. Сохрани текущие настройки, чтобы переключаться между ними одним тапом.</b>"
        )
    body = f"<blockquote>{intro}\n\n<b>Текущие настройки:</b></blockquote>"
    # Snapshot of current job — also show which media is set
    snap = job.cfg.to_dict()
    snap["_bg_kind"] = job.bg_kind
    text = f"{head}\n{body}\n{_summary_html(snap)}"

    await edit_screen(
        cast(Message, callback.message),
        db,
        S.CONFIGS,
        text,
        keyboard=_kb_main(presets),
    )
    await callback.answer()


@router.callback_query(F.data == CB.CFG_SAVE_NEW)
async def cfg_save_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(ConfigsSG.waiting_name)
    text = (
        f"{_E_WRITE} <b>Назови конфиг</b>\n"
        f"<blockquote>"
        f"<b>Например: Лого красное, Баннер 16:9, Тёмная вотермарка</b>"
        f"</blockquote>"
    )
    await safe_edit(
        cast(Message, callback.message),
        text,
        keyboard=_kb_back_to_configs(),
    )
    await callback.answer()


@router.message(ConfigsSG.waiting_name, F.text)
async def cfg_save_name(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None or message.text is None:
        return
    name = message.text.strip()[:48] or "Без названия"
    job = job_store.get(message.from_user.id)
    data = job.cfg.to_dict()

    cid = await db.save_config(message.from_user.id, name, data)

    # Persist current bg media alongside the preset so loading can restore it.
    if job.bg_kind != "none" and job.bg_path and job.bg_path.exists():
        rel = _persist_bg(message.from_user.id, cid, job.bg_path, job.bg_kind)
        data["_bg_kind"] = job.bg_kind
        data["_bg_file"] = rel
        await db.update_config_data(cid, message.from_user.id, data)

    await state.set_state(GifSG.idle)

    text = (
        f"{_E_CHECK} <b>Сохранено: <code>{name}</code></b>\n"
        f"{_summary_html(data)}\n"
        f"<blockquote>"
        f"{_E_LINK} <b>ID конфига: <code>#{cid}</code></b>\n"
        f"<b>Скинь ID другу — он впишет его в «Найти по ID» "
        f"и подтянет настройки + медиа.</b>"
        f"</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◁  В конфиги",
                    callback_data=CB.CONFIGS,
                    icon_custom_emoji_id=EmojiID.BACK,
                ),
            ]
        ]
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


@router.callback_query(F.data.startswith(CB.CFG_LOAD))
async def cfg_load(callback: CallbackQuery, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.data is None:
        return
    try:
        cid = int(callback.data[len(CB.CFG_LOAD) :])
    except ValueError:
        await callback.answer()
        return
    rec = await db.get_config(cid, callback.from_user.id)
    if not rec:
        await callback.answer("Не найден", show_alert=True)
        return
    job = job_store.get(callback.from_user.id)
    try:
        data = json.loads(rec["data_json"])
        job.cfg = UserConfig.from_dict(data)
    except Exception:
        await callback.answer("Битый конфиг", show_alert=True)
        return

    # Restore bg media (if the preset was saved with one).
    job.cleanup_bg()
    bg_path, bg_kind = _apply_bg_from_data(callback.from_user.id, cid, data)
    if bg_path:
        job.bg_path = bg_path
        job.bg_kind = bg_kind

    await callback.answer(f"✅ Загружено: {rec['name']}")
    await open_configs(callback, db, cfg)


@router.callback_query(F.data.startswith(CB.CFG_DELETE))
async def cfg_delete(callback: CallbackQuery, db: DB, cfg: Config) -> None:
    if callback.from_user is None or callback.data is None:
        return
    try:
        cid = int(callback.data[len(CB.CFG_DELETE) :])
    except ValueError:
        await callback.answer()
        return

    rec = await db.get_config(cid, callback.from_user.id)
    if rec:
        try:
            data = json.loads(rec["data_json"])
            _delete_persisted_bg(data.get("_bg_file") or "")
        except Exception:
            pass

    ok = await db.delete_config(cid, callback.from_user.id)
    await callback.answer("Удалено" if ok else "Не найден")
    await open_configs(callback, db, cfg)


@router.callback_query(F.data == CB.CFG_FIND_ID)
async def cfg_find_id_prompt(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    await state.set_state(ConfigsSG.waiting_load_id)
    text = (
        f"{_E_WRITE} <b>Введи ID конфига</b>\n"
        f"<blockquote>"
        f"<b>Просто число — например, <code>42</code>. "
        f"Подгрузим настройки + медиа автора.</b>"
        f"</blockquote>"
    )
    await safe_edit(
        cast(Message, callback.message),
        text,
        keyboard=_kb_back_to_configs(),
    )
    await callback.answer()


@router.message(ConfigsSG.waiting_load_id, F.text)
async def cfg_load_by_id(
    message: Message,
    state: FSMContext,
    db: DB,
    cfg: Config,
) -> None:
    if message.from_user is None or message.text is None:
        return
    txt = message.text.strip().lstrip("#")
    try:
        cid = int(txt)
    except ValueError:
        await message.reply(
            f"<blockquote>{_E_CROSS} <b>Не похоже на ID. Введи число.</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    rec = await db.get_config_by_id(cid)
    if not rec:
        await message.reply(
            f"<blockquote>{_E_CROSS} <b>Конфиг #{cid} не найден.</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        data = json.loads(rec["data_json"])
    except Exception:
        await message.reply(
            f"<blockquote>{_E_CROSS} <b>Не удалось распарсить конфиг.</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    job = job_store.get(message.from_user.id)
    try:
        job.cfg = UserConfig.from_dict(data)
    except Exception:
        await message.reply(
            f"<blockquote>{_E_CROSS} <b>Битый конфиг.</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    job.cleanup_bg()
    bg_path, bg_kind = _apply_bg_from_data(message.from_user.id, cid, data)
    if bg_path:
        job.bg_path = bg_path
        job.bg_kind = bg_kind

    await state.set_state(GifSG.idle)
    text = (
        f"{_E_CHECK} <b>Конфиг #{cid} применён: <code>{rec['name']}</code></b>\n"
        f"{_summary_html(data)}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="В меню",
                    callback_data=CB.MAIN,
                    icon_custom_emoji_id=EmojiID.HOME,
                ),
            ]
        ]
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def apply_shared_config(
    message: Message,
    db: DB,
    cfg: Config,
    ref_str: str,
) -> None:
    """Old share-link entry point. New shares are by ID; this stays so that
    pre-existing links still work."""
    try:
        cid = int(ref_str)
    except ValueError:
        await message.answer(
            f"<blockquote>{_E_CROSS} <b>Некорректная ссылка конфига</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    rec = await db.get_config_by_id(cid)
    if not rec or message.from_user is None:
        await message.answer(
            f"<blockquote>{_E_CROSS} <b>Конфиг не найден или удалён</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    job = job_store.get(message.from_user.id)
    try:
        data = json.loads(rec["data_json"])
        job.cfg = UserConfig.from_dict(data)
    except Exception:
        await message.answer(
            f"<blockquote>{_E_CROSS} <b>Не удалось применить конфиг</b></blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    job.cleanup_bg()
    bg_path, bg_kind = _apply_bg_from_data(message.from_user.id, cid, data)
    if bg_path:
        job.bg_path = bg_path
        job.bg_kind = bg_kind

    text = (
        f"{_E_CHECK} <b>Конфиг #{cid} применён: <code>{rec['name']}</code></b>\n"
        f"{_summary_html(data)}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="В меню",
                    callback_data=CB.MAIN,
                    icon_custom_emoji_id=EmojiID.HOME,
                ),
            ]
        ]
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
