"""
Sticker / premium-emoji extractor.

Telegram sends "stickers" in three flavours we care about:

    1. Animated sticker (Lottie/TGS)   sticker.is_animated = True
    2. Animated premium emoji          message.entities contain custom_emoji,
                                       OR message.sticker with custom_emoji_id
                                       set on the file (Telegram Bot API
                                       returns these as Sticker objects with
                                       sticker.is_animated == True or
                                       sticker.is_video == True; the emoji is
                                       just a sticker from a special set).
    3. Video/WebP sticker              sticker.is_video = True   (we DON'T
                                       support these — they're not Lottie)

Premium / custom emoji can also appear inside a *text message* with
`entities` of type 'custom_emoji'. In that case Telegram doesn't ship the
underlying sticker — we have to call `bot.get_custom_emoji_stickers([id])`
to fetch the actual sticker file.

This module returns:
    (path_to_tgs, error_message)
where exactly one is None.
"""

from __future__ import annotations

import re
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message, Sticker

from utils.logger import get_logger

log = get_logger("sticker_fetch")


# Match t.me/addstickers/<name> or telegram.me/addstickers/<name>, with or
# without scheme/@. Captures the pack short_name only.
_PACK_LINK_RE = re.compile(
    r"(?:https?://)?(?:t|telegram)\.me/addstickers/([A-Za-z0-9_]+)",
    re.IGNORECASE,
)


async def fetch_tgs_from_message(
    message: Message,
    bot: Bot,
    dest_dir: Path,
) -> tuple[Path | None, str | None]:
    """Try every known way of getting a Lottie/TGS file from a user message."""
    user = message.from_user
    if user is None:
        return None, "no user"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Direct sticker (animated)
    if message.sticker is not None:
        st = message.sticker
        if st.is_animated:
            target = dest_dir / f"{user.id}_{st.file_unique_id}.tgs"
            await bot.download(st, destination=target)
            log.info("downloaded animated sticker for user=%d (%s)", user.id, st.set_name)
            return target, None
        if st.is_video:
            return None, (
                "Это видео-стикер (WebM), а нужен анимированный TGS. "
                "Пришли стикер из другого пака или анимированное эмодзи."
            )
        # static sticker: PNG/WebP
        return None, "Это статичный стикер. Нужен анимированный (TGS)."

    # Document with .tgs extension
    if message.document is not None:
        doc = message.document
        name = (doc.file_name or "").lower()
        mime = (doc.mime_type or "").lower()
        if name.endswith(".tgs") or mime in {"application/x-tgsticker"}:
            target = dest_dir / f"{user.id}_{doc.file_unique_id}.tgs"
            await bot.download(doc, destination=target)
            log.info("downloaded TGS document for user=%d", user.id)
            return target, None
        return None, "Этот документ не TGS."

    # Custom emoji inside a text message
    custom_ids: list[str] = []
    if message.entities:
        for ent in message.entities:
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                custom_ids.append(ent.custom_emoji_id)
    # Telegram caps the lookup at ~200 ids, but we only need 1
    if custom_ids:
        try:
            stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=custom_ids[:1])
        except Exception as exc:
            log.exception("get_custom_emoji_stickers failed")
            return None, f"Не получилось получить эмодзи: {exc}"
        for sticker in stickers:
            if sticker.is_animated:
                target = dest_dir / f"{user.id}_{sticker.file_unique_id}.tgs"
                await bot.download(sticker, destination=target)
                log.info("downloaded custom_emoji TGS for user=%d", user.id)
                return target, None
            if sticker.is_video:
                return None, (
                    "Это видео-эмодзи (WebM), а нужно анимированное (TGS). "
                    "Попробуй другое premium-эмодзи."
                )
        return None, "Это статичное эмодзи. Нужно анимированное."

    return None, "Пришли анимированный стикер, premium-эмодзи или TGS-файл."


async def _download_sticker(
    sticker: Sticker,
    user_id: int,
    dest_dir: Path,
    bot: Bot,
) -> tuple[Path | None, str | None]:
    """Download one Sticker as TGS, or return a per-item error string."""
    if sticker.is_animated:
        target = dest_dir / f"{user_id}_{sticker.file_unique_id}.tgs"
        try:
            await bot.download(sticker, destination=target)
            return target, None
        except Exception as exc:
            return None, f"не получилось скачать ({exc})"
    if sticker.is_video:
        return None, "видео-стикер пропущен"
    return None, "статичный стикер пропущен"


async def fetch_all_tgs_from_message(
    message: Message,
    bot: Bot,
    dest_dir: Path,
    *,
    max_items: int = 10,
) -> tuple[list[Path], list[str]]:
    """Pull every animated TGS reachable from one user message.

    Returns (paths, errors). Sources, in order of detection:
      1. t.me/addstickers/<name> link in text → entire animated pack
      2. Single uploaded sticker → just that one (NOT the whole pack —
         users send one sticker far more often than they want a batch)
      3. Single .tgs document → just that one
      4. custom_emoji entities in text → all animated emojis in the message,
         deduped by id, capped at max_items

    `errors` collects per-item issues (skipped video stickers, download
    failures) so the caller can report them without blocking the batch.
    """
    user = message.from_user
    if user is None:
        return [], ["no user"]
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Sticker pack link in plain text
    text = message.text or message.caption or ""
    if text:
        m = _PACK_LINK_RE.search(text)
        if m:
            pack_name = m.group(1)
            try:
                pack = await bot.get_sticker_set(pack_name)
            except Exception as exc:
                log.warning("get_sticker_set(%s) failed: %s", pack_name, exc)
                return [], [f"Не получилось открыть пак {pack_name}: {exc}"]
            paths: list[Path] = []
            errors: list[str] = []
            for st in pack.stickers[:max_items]:
                p, err = await _download_sticker(st, user.id, dest_dir, bot)
                if p is not None:
                    paths.append(p)
                elif err:
                    errors.append(err)
            if len(pack.stickers) > max_items:
                errors.append(
                    f"в паке {len(pack.stickers)} стикеров — обработаю первые {max_items}"
                )
            log.info(
                "pack %s: %d animated of %d (errors=%d) for user=%d",
                pack_name,
                len(paths),
                len(pack.stickers),
                len(errors),
                user.id,
            )
            return paths, errors

    # Direct sticker (single)
    if message.sticker is not None:
        p, err = await _download_sticker(message.sticker, user.id, dest_dir, bot)
        if p is not None:
            return [p], []
        return [], [err or "Не получилось получить стикер"]

    # Document with .tgs
    if message.document is not None:
        doc = message.document
        name = (doc.file_name or "").lower()
        mime = (doc.mime_type or "").lower()
        if name.endswith(".tgs") or mime in {"application/x-tgsticker"}:
            target = dest_dir / f"{user.id}_{doc.file_unique_id}.tgs"
            await bot.download(doc, destination=target)
            return [target], []
        return [], ["Этот документ не TGS."]

    # Custom emojis in text — dedupe by id, cap at max_items
    custom_ids: list[str] = []
    seen: set[str] = set()
    if message.entities:
        for ent in message.entities:
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                if ent.custom_emoji_id in seen:
                    continue
                seen.add(ent.custom_emoji_id)
                custom_ids.append(ent.custom_emoji_id)

    if not custom_ids:
        return [], [
            "Пришли анимированный стикер, premium-эмодзи, ссылку на стикерпак или TGS-файл."
        ]

    truncated = len(custom_ids) > max_items
    custom_ids = custom_ids[:max_items]

    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=custom_ids)
    except Exception as exc:
        log.exception("get_custom_emoji_stickers failed")
        return [], [f"Не получилось получить эмодзи: {exc}"]

    paths: list[Path] = []
    errors: list[str] = []
    for sticker in stickers:
        p, err = await _download_sticker(sticker, user.id, dest_dir, bot)
        if p is not None:
            paths.append(p)
        elif err:
            errors.append(err)
    if truncated:
        errors.append(f"эмодзи в сообщении больше {max_items} — обработаю первые")
    log.info(
        "multi-emoji: %d animated of %d (errors=%d) for user=%d",
        len(paths),
        len(custom_ids),
        len(errors),
        user.id,
    )
    return paths, errors
