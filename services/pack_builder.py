"""Builds a custom emoji / sticker set from the master pack.

Walks master_pack/, replaces the placeholder text with the user's nickname or
logo (services.swex), uploads the result to Telegram and keeps a progress
message up to date.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json as _json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputSticker, Message

from services.svg_to_lottie import svg_to_lottie_shapes
from services.swex.glyphs import FontRenderer
from services.swex.replacer import (
    _load_positions,
    _tile_id,
    replace_swex_bytes,
    replace_swex_with_logo_bytes,
)

log = logging.getLogger("pack_builder")

# Telegram limit per .tgs uploaded as animated custom emoji.
TG_TGS_LIMIT = 64 * 1024
# Telegram caps regular animated sticker sets at 120 stickers.
TG_STICKER_SET_LIMIT = 120
EMOJI_FILENAME_RE = re.compile(r"^\d+_([0-9a-f]+)")
SHORTNAME_RE = re.compile(r"[^A-Za-z0-9_]")


def _filename_emoji(path: Path, default: str = "🎭") -> str:
    m = EMOJI_FILENAME_RE.match(path.name)
    if not m:
        return default
    try:
        return bytes.fromhex(m.group(1)).decode("utf-8") or default
    except (ValueError, UnicodeDecodeError):
        return default


def _short_name(nick: str, user_id: int, bot_username: str) -> str:
    """Build a unique-ish short_name for the new sticker set.

    Telegram requires latin/digits/underscore, must start with a letter,
    and end with `_by_<bot_username>`. Pad with random suffix so the same
    user can build a pack multiple times without collisions.
    """
    base = SHORTNAME_RE.sub("", nick) or "swex"
    if not base[0].isalpha():
        base = "p" + base
    suffix = secrets.token_hex(3)
    candidate = f"{base[:20]}_{user_id % 10_000_000}_{suffix}"
    full = f"{candidate}_by_{bot_username}"
    # Telegram pack short_name limit: 64 chars. Trim base if needed.
    if len(full) > 64:
        keep = 64 - len(full) + len(base[:20])
        candidate = f"{base[: max(1, keep)]}_{suffix}"
        full = f"{candidate}_by_{bot_username}"
    return full


def _is_passport_path(path: Path) -> bool:
    try:
        data = gzip.decompress(path.read_bytes())
    except Exception:
        return False
    return b"swex_text_" in data or b"swex_qmark" in data


@dataclass
class _RenderCtx:
    nick: str
    content_color: str
    use_logo: bool
    logo_shapes: list = field(default_factory=list)
    logo_bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    renderer: FontRenderer | None = None
    positions: dict = field(default_factory=dict)
    passport_srcs: set = field(default_factory=set)
    override_logo_cache: dict = field(default_factory=dict)
    global_transform: dict | None = None


def _build_render_ctx(
    *,
    master_pack_dir: Path,
    new_pack_dir: Path | None,
    font_path: Path,
    nick: str,
    content_color: str,
    logo_svg: bytes | None,
    per_emoji: dict | None,
    new_per_emoji: dict | None,
    global_transform: dict | None,
) -> _RenderCtx:
    """Prep all per-build shared state: font, logo SVG, master positions,
    passport-detect set, and a cache of parsed override SVGs."""
    ctx = _RenderCtx(
        nick=nick or "",
        content_color=content_color or "#ffffff",
        use_logo=bool(logo_svg),
        global_transform=global_transform,
    )

    master_files = sorted(master_pack_dir.glob("*.tgs"))
    new_files: list[Path] = []
    if new_pack_dir and Path(new_pack_dir).exists():
        new_files = sorted(Path(new_pack_dir).glob("*.tgs"))
    ctx.positions = _load_positions(master_pack_dir)
    ctx.passport_srcs = {p for p in (master_files + new_files) if _is_passport_path(p)}

    if ctx.use_logo:
        ctx.logo_shapes, ctx.logo_bbox = svg_to_lottie_shapes(logo_svg)

    needs_renderer = (not ctx.use_logo) or bool(per_emoji) or bool(new_per_emoji)
    if needs_renderer:
        ctx.renderer = FontRenderer(font_path)

    for ovd in (per_emoji or {}, new_per_emoji or {}):
        for ov in ovd.values():
            if not isinstance(ov, dict):
                continue
            svg = ov.get("logo_svg")
            if svg and svg not in ctx.override_logo_cache:
                try:
                    ctx.override_logo_cache[svg] = svg_to_lottie_shapes(svg)
                except Exception as exc:
                    log.warning("override svg parse failed: %s", exc)
                    ctx.override_logo_cache[svg] = ([], (0.0, 0.0, 0.0, 0.0))
    return ctx


def _render_tile_bytes(
    ctx: _RenderCtx,
    *,
    kind: str,
    src: Path,
    ov: dict | None,
) -> bytes:
    """Render one tile (.tgs) with current ctx settings + per-tile override.

    Returns gzipped Lottie bytes. Raises on parser/glyph errors.
    """
    eff_text = (ov or {}).get("text") if ov else None
    if not eff_text:
        eff_text = ctx.nick
    eff_content_color = (ov or {}).get("content_color") if ov else None
    if not eff_content_color:
        eff_content_color = ctx.content_color
    ov_logo_svg = (ov or {}).get("logo_svg") if ov else None
    eff_kind = (ov or {}).get("kind") if ov else None
    if eff_kind not in ("text", "logo"):
        eff_kind = "logo" if (ov_logo_svg or ctx.use_logo) else "text"

    line_map_dict: dict[str, str] | None = None
    line_styles_dict: dict[str, dict] | None = None
    header_line: dict | None = None
    raw_lines = (ov or {}).get("lines") if ov else None
    if isinstance(raw_lines, list):
        line_map_dict = {}
        line_styles_dict = {}
        for ln in raw_lines:
            if not isinstance(ln, dict):
                continue
            nm = ln.get("name")
            tx = ln.get("text")
            if not isinstance(nm, str):
                continue
            if nm == "__header__":
                header_line = ln
                continue
            if isinstance(tx, str) and tx.strip():
                line_map_dict[nm] = tx
            ln_scale = float(ln.get("scale", 1.0) or 1.0)
            ln_color = str(ln.get("color") or "#000000")
            ln_ox = float(ln.get("offset_x", 0.0) or 0.0)
            ln_oy = float(ln.get("offset_y", 0.0) or 0.0)
            if (
                abs(ln_scale - 1.0) > 1e-3
                or ln_color.lower() != "#000000"
                or abs(ln_ox) > 1e-6
                or abs(ln_oy) > 1e-6
            ):
                line_styles_dict[nm] = {
                    "scale": max(0.3, min(3.0, ln_scale)),
                    "color": ln_color,
                    "offset_x": ln_ox,
                    "offset_y": ln_oy,
                }
        if not line_map_dict:
            line_map_dict = None
        if not line_styles_dict:
            line_styles_dict = None

    is_passport_tile = src in ctx.passport_srcs
    if is_passport_tile and header_line is None:
        header_line = {}
    if header_line is not None:
        eff_kind = "text"
        ov_logo_svg = None
        eff_text = str(header_line.get("text") or "")
        eff_content_color = str(header_line.get("color") or "#000000")

    photo_svg_bytes = (ov or {}).get("photo_svg") if ov else None
    if photo_svg_bytes and not isinstance(photo_svg_bytes, (bytes, bytearray)):
        photo_svg_bytes = None
    try:
        photo_scale_val = float((ov or {}).get("photo_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        photo_scale_val = 1.0
    photo_scale_val = max(0.3, min(3.0, photo_scale_val))

    transform = None
    gtf = ctx.global_transform or {}
    if header_line is not None:
        scale = max(0.3, min(3.0, float(header_line.get("scale", 1.0) or 1.0)))
        offset_x = float(header_line.get("offset_x", 0.0) or 0.0)
        offset_y = float(header_line.get("offset_y", 0.0) or 0.0)
        rotation_deg = 0.0
    else:
        scale = float(gtf.get("scale", 1.0))
        offset_x = float(gtf.get("offset_x", 0.0))
        offset_y = float(gtf.get("offset_y", 0.0))
        rotation_deg = float(gtf.get("rotation_deg", 0.0))
        if ov:
            scale *= max(0.5, min(5.0, float(ov.get("scale", 1.0))))
            offset_x += float(ov.get("offset_x", 0.0))
            offset_y += float(ov.get("offset_y", 0.0))
            rotation_deg += float(ov.get("rotation", 0.0))
    if (
        abs(scale - 1.0) > 1e-6
        or abs(offset_x) > 1e-6
        or abs(offset_y) > 1e-6
        or abs(rotation_deg) > 1e-6
    ):
        transform = {
            "scale": scale,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "rotation_deg": rotation_deg,
        }

    if eff_kind == "logo":
        if ov_logo_svg and ov_logo_svg in ctx.override_logo_cache:
            eff_shapes, eff_bbox = ctx.override_logo_cache[ov_logo_svg]
        else:
            eff_shapes, eff_bbox = ctx.logo_shapes, ctx.logo_bbox
        if not eff_shapes:
            new_bytes, _ = replace_swex_bytes(
                src.read_bytes(),
                eff_text or "swex",
                ctx.renderer,
                transform=transform,
                content_color=eff_content_color,
                line_map=line_map_dict,
                line_styles=line_styles_dict,
                photo_svg=photo_svg_bytes,
                photo_scale=photo_scale_val,
            )
        else:
            tid = _tile_id(src)
            pos_entry = ctx.positions.get(tid) if tid else None
            center = (pos_entry["cx"], pos_entry["cy"]) if pos_entry else None
            size = (
                (pos_entry["w"], pos_entry["h"])
                if pos_entry and "w" in pos_entry and "h" in pos_entry
                else None
            )
            letter_expand = 2.5 if kind == "new" else 1.0
            new_bytes, _ = replace_swex_with_logo_bytes(
                src.read_bytes(),
                eff_shapes,
                eff_bbox,
                override_center=center,
                override_size=size,
                override_min_px=pos_entry.get("min_px") if pos_entry else None,
                override_max_px=pos_entry.get("max_px") if pos_entry else None,
                override_pad=pos_entry.get("pad") if pos_entry else None,
                transform=transform,
                content_color=eff_content_color,
                line_map=line_map_dict,
                line_renderer=ctx.renderer,
                line_styles=line_styles_dict,
                photo_svg=photo_svg_bytes,
                photo_scale=photo_scale_val,
                letter_expand=letter_expand,
            )
    else:
        text_arg = eff_text if header_line is not None else (eff_text or "swex")
        new_bytes, _ = replace_swex_bytes(
            src.read_bytes(),
            text_arg,
            ctx.renderer,
            transform=transform,
            content_color=eff_content_color,
            line_map=line_map_dict,
            line_styles=line_styles_dict,
            photo_svg=photo_svg_bytes,
            photo_scale=photo_scale_val,
        )
    # Apply advanced colour remap LAST so it repaints freshly inserted
    # text/logo fills too (matches the client-side preview order).
    color_remap_raw = (ov or {}).get("color_remap") if ov else None
    if isinstance(color_remap_raw, dict) and color_remap_raw:
        new_bytes = _apply_color_remap_bytes(new_bytes, color_remap_raw)
    return new_bytes


_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _norm_hex(s: str) -> str | None:
    s = (s or "").strip()
    if not _HEX_RE.match(s):
        return None
    s = s.lower()
    if len(s) == 4:  # #rgb → #rrggbb
        s = "#" + "".join(ch * 2 for ch in s[1:])
    return s


def _hex_to_rgba(hx: str, alpha: float) -> list:
    return [
        int(hx[1:3], 16) / 255.0,
        int(hx[3:5], 16) / 255.0,
        int(hx[5:7], 16) / 255.0,
        float(alpha),
    ]


def _rgba_to_hex(rgba: list) -> str | None:
    if not isinstance(rgba, list) or len(rgba) < 3:
        return None
    r, g, b = rgba[0], rgba[1], rgba[2]
    return f"#{max(0, min(255, round(r * 255))):02x}{max(0, min(255, round(g * 255))):02x}{max(0, min(255, round(b * 255))):02x}"


def _apply_color_remap_bytes(tgs_bytes: bytes, remap_raw: dict) -> bytes:
    """Walk every static `fl`/`st` colour in the gzipped Lottie tree and
    replace any RGBA matching a `remap_raw` key (hex) with the mapped hex.

    Animated colour properties (`c.a == 1`) and gradient fills are skipped —
    the webapp editor doesn't expose them, so leaving them alone keeps the
    server output identical to the client-side preview.
    """
    # Normalise the table once.
    table: dict[str, str] = {}
    for k, v in remap_raw.items():
        a = _norm_hex(str(k))
        b = _norm_hex(str(v))
        if a and b and a != b:
            table[a] = b
    if not table:
        return tgs_bytes

    tree = _json.loads(gzip.decompress(tgs_bytes))

    def visit(n):
        if isinstance(n, list):
            for c in n:
                visit(c)
            return
        if not isinstance(n, dict):
            return
        if n.get("ty") in ("fl", "st"):
            c = n.get("c")
            if isinstance(c, dict) and c.get("a") == 0:
                k = c.get("k")
                if isinstance(k, list) and len(k) >= 3:
                    hx = _rgba_to_hex(k)
                    if hx and hx in table:
                        alpha = k[3] if len(k) >= 4 else 1.0
                        c["k"] = _hex_to_rgba(table[hx], alpha)
        for key in ("layers", "assets", "shapes", "it"):
            v = n.get(key)
            if isinstance(v, list):
                visit(v)

    visit(tree)
    return gzip.compress(_json.dumps(tree, separators=(",", ":")).encode())


async def _edit_status(status: Message, html: str) -> None:
    """Edit progress message in place; tolerate media messages."""
    try:
        if (
            getattr(status, "animation", None)
            or getattr(status, "video", None)
            or getattr(status, "photo", None)
        ):
            await status.edit_caption(caption=html, parse_mode=ParseMode.HTML)
        else:
            await status.edit_text(html, parse_mode=ParseMode.HTML)
    except Exception as exc:
        if "message is not modified" in str(exc):
            return
        log.debug("status edit failed: %s", exc)


async def build_pack(
    *,
    bot: Bot,
    user_id: int,
    nick: str,
    master_pack_dir: Path,
    font_path: Path,
    bot_username: str,
    pack_format: str = "emoji",
    title_suffix: str = "",
    pack_title: str | None = None,  # overrides nick for title/short_name
    logo_svg: bytes | None = None,
    content_color: str = "#ffffff",
    status: Message | None = None,
    progress_template_html: str = "<blockquote><b>Создаю пак…  {i}/{total}</b></blockquote>",
    per_emoji: dict | None = None,
    new_per_emoji: dict | None = None,
    new_pack_dir: Path | None = None,
    selected: set | None = None,
    global_transform: dict | None = None,
) -> tuple[bool, str, dict]:
    """Build a sticker / custom-emoji pack for the user.

    pack_format: "emoji" → custom_emoji set (https://t.me/addemoji/...)
                 "sticker" → regular animated sticker set (.../addstickers/...)
    Returns (ok, message_html, info).
    info contains: short_name, link, total, skipped, error (if any), format.

    new_pack_dir / new_per_emoji: optional second source ("New" tab in the
        webapp) whose tiles are appended after the master pack. Transforms for
        these tiles are keyed by 1-based index in `new_per_emoji`.
    selected: when given (sticker mode), a set of normalized ids used to filter
        which tiles go into the pack. Master tiles are int 1..144, new tiles
        are strings "new:1".."new:N".
    """
    master_files = sorted(master_pack_dir.glob("*.tgs"))
    new_files: list[Path] = []
    if new_pack_dir and Path(new_pack_dir).exists():
        new_files = sorted(Path(new_pack_dir).glob("*.tgs"))

    # Each entry: (kind, idx_1based, path) where kind is "master" or "new".
    entries: list[tuple[str, int, Path]] = []
    for i, p in enumerate(master_files, 1):
        entries.append(("master", i, p))
    for i, p in enumerate(new_files, 1):
        entries.append(("new", i, p))

    # Apply selection (sticker mode) and per-format caps.
    if selected is not None:

        def _norm(e):
            k, i, _ = e
            return i if k == "master" else f"new:{i}"

        entries = [e for e in entries if _norm(e) in selected]
    if pack_format == "sticker" and len(entries) > TG_STICKER_SET_LIMIT:
        # TG caps regular sticker sets at 120. Never drop a "new" tile in
        # favour of a "master" duplicate-emoji — the new ones are the whole
        # point of the New tab. Keep every "new" entry (truncating only if
        # they alone exceed the cap), then fill the rest with master tiles
        # in their original order.
        new_only = [e for e in entries if e[0] == "new"]
        master_only = [e for e in entries if e[0] == "master"]
        keep_new = new_only[:TG_STICKER_SET_LIMIT]
        slack = TG_STICKER_SET_LIMIT - len(keep_new)
        keep_master = master_only[: max(0, slack)]
        # Preserve the visual master-first layout so users don't see a
        # reshuffled pack.
        entries = keep_master + keep_new
    files = [e[2] for e in entries]
    total = len(files)
    info: dict = {
        "short_name": "",
        "link": "",
        "total": total,
        "skipped": [],
        "truncated": 0,
        "error": "",
        "format": pack_format,
    }
    if total == 0:
        info["error"] = "master_pack_empty"
        return (
            False,
            f"<blockquote><b>Master pack пуст: <code>{master_pack_dir}</code></b></blockquote>",
            info,
        )

    sticker_type = "regular" if pack_format == "sticker" else "custom_emoji"
    link_prefix = "addstickers" if pack_format == "sticker" else "addemoji"

    # Logo overrides text — when both are set, use logo only.
    use_logo = bool(logo_svg)
    # `pack_title`, when provided by the mini-app modal, is what the user
    # actually wants on the pack — independent from the per-emoji `nick`.
    if pack_title and pack_title.strip():
        title_base = pack_title.strip()
    elif use_logo and not nick:
        title_base = "created"
    else:
        title_base = nick or "swex"
    short_name = _short_name(title_base, user_id, bot_username)
    title = (title_base + title_suffix)[:64]
    info["short_name"] = short_name
    info["link"] = f"https://t.me/{link_prefix}/{short_name}"
    info["mode"] = "logo" if use_logo else "text"

    # Font, logo, tile positions, passport detection and the SVG override cache.
    # Validation errors propagate to the caller.
    try:
        ctx = _build_render_ctx(
            master_pack_dir=master_pack_dir,
            new_pack_dir=new_pack_dir,
            font_path=font_path,
            nick=nick,
            content_color=content_color,
            logo_svg=logo_svg,
            per_emoji=per_emoji,
            new_per_emoji=new_per_emoji,
            global_transform=global_transform,
        )
    except Exception as exc:
        log.exception("render context build failed")
        info["error"] = f"ctx: {exc}"
        return (
            False,
            (
                f"<blockquote><b>Не получилось подготовить рендер: "
                f"<code>{exc}</code></b></blockquote>"
            ),
            info,
        )
    if use_logo and not ctx.logo_shapes:
        info["error"] = "svg_no_paths"
        return (
            False,
            (
                "<blockquote><b>В SVG нет путей. Сконвертируй файл через "
                "«Конвертировать в SVG» и пришли снова.</b></blockquote>"
            ),
            info,
        )

    created = False
    skipped: list[str] = []
    included_ids: list = []
    last_progress_t = 0.0

    for i, ent in enumerate(entries, 1):
        kind, sub_idx, src = ent
        norm_id = sub_idx if kind == "master" else f"new:{sub_idx}"
        if kind == "master":
            ov = (per_emoji or {}).get(sub_idx) if per_emoji else None
        else:
            ov = (new_per_emoji or {}).get(sub_idx) if new_per_emoji else None
        try:
            new_bytes = _render_tile_bytes(ctx, kind=kind, src=src, ov=ov)
        except Exception as exc:
            log.exception("replace failed for %s", src)
            info["error"] = f"replace {src.name}: {exc}"
            return (
                False,
                (
                    f"<blockquote><b>Ошибка обработки <code>{src.name}</code>: "
                    f"<code>{exc}</code></b></blockquote>"
                ),
                info,
            )

        if len(new_bytes) > TG_TGS_LIMIT:
            log.warning("skip %s: %d bytes > 64KB", src.name, len(new_bytes))
            skipped.append(src.name)
            continue

        sticker = InputSticker(
            sticker=BufferedInputFile(new_bytes, filename=src.name),
            format="animated",
            emoji_list=[_filename_emoji(src)],
        )

        # STICKERPACK_STICKERS_TOO_MUCH is sometimes returned transiently
        # below the real per-pack limit (packs of 144 emojis succeed normally).
        # Retry hard before giving up — only accept partial as last resort.
        limit_retries = 0
        limit_delays = [10, 20, 40]
        flood_retries = 0

        while True:
            try:
                if not created:
                    await bot.create_new_sticker_set(
                        user_id=user_id,
                        name=short_name,
                        title=title,
                        stickers=[sticker],
                        sticker_type=sticker_type,
                    )
                    created = True
                else:
                    await bot.add_sticker_to_set(
                        user_id=user_id,
                        name=short_name,
                        sticker=sticker,
                    )
                included_ids.append(norm_id)
                break
            except TelegramRetryAfter as e:
                if flood_retries >= 5:
                    raise
                flood_retries += 1
                await asyncio.sleep(e.retry_after + 1)
            except Exception as exc:
                low = str(exc).lower().replace(" ", "")
                if created and "stickers_too_much" in low and limit_retries < len(limit_delays):
                    delay = limit_delays[limit_retries]
                    limit_retries += 1
                    log.warning(
                        "stickers_too_much at #%d, retry %d/%d in %ds: %s",
                        i,
                        limit_retries,
                        len(limit_delays),
                        delay,
                        exc,
                    )
                    if status is not None:
                        await _edit_status(
                            status,
                            (
                                f"<blockquote><b>Telegram капризничает на #{i}, "
                                f"повтор {limit_retries}/{len(limit_delays)} "
                                f"через {delay}с…</b></blockquote>"
                            ),
                        )
                    await asyncio.sleep(delay)
                    continue
                log.exception("API call failed (i=%d, %s)", i, src.name)
                # Retries exhausted on stickers_too_much — keep partial pack.
                if created and "stickers_too_much" in low:
                    added = i - 1 - len(skipped)
                    info["truncated"] = max(0, total - added - len(skipped))
                    info["skipped"] = skipped
                    info["included_ids"] = list(included_ids)
                    log.warning(
                        "pack truncated at #%d after %d retries: %d added",
                        i,
                        limit_retries,
                        added,
                    )
                    return True, "", info
                hint = ""
                if "occupied" in low or "invalid" in low:
                    hint = " (короткое имя занято — попробуй ещё раз)"
                elif created:
                    hint = f" (пак уже частично создан: {i - 1 - len(skipped)} эмодзи)"
                info["error"] = f"tg #{i}: {exc}"
                return (
                    False,
                    (
                        f"<blockquote><b>Ошибка Telegram на эмодзи #{i}: "
                        f"<code>{exc}</code>{hint}</b></blockquote>"
                    ),
                    info,
                )

        now = time.monotonic()
        if status is not None and (now - last_progress_t > 4 or i == total):
            with contextlib.suppress(Exception):
                await _edit_status(
                    status,
                    progress_template_html.format(i=i, total=total),
                )
            last_progress_t = now

    info["skipped"] = skipped
    info["included_ids"] = list(included_ids)
    return True, "", info


async def add_tiles_to_pack(
    *,
    bot: Bot,
    user_id: int,
    short_name: str,
    master_pack_dir: Path,
    new_pack_dir: Path | None,
    font_path: Path,
    tile_ids: list,  # mixed ints (master) + "new:N" strings
    nick: str,
    content_color: str,
    logo_svg: bytes | None,
    per_emoji: dict | None = None,
    new_per_emoji: dict | None = None,
    global_transform: dict | None = None,
) -> tuple[list, list[dict]]:
    """Add new tiles to an existing sticker/emoji set.

    Returns (added_ids, errors). `added_ids` mirrors the normalization in
    profile_db._normalize_ids — ints for master, "new:N" for new.
    `errors` is a list of {id, src, message} for tiles that failed.
    """
    master_files = sorted(master_pack_dir.glob("*.tgs"))
    new_files: list[Path] = []
    if new_pack_dir and Path(new_pack_dir).exists():
        new_files = sorted(Path(new_pack_dir).glob("*.tgs"))

    by_master: dict[int, Path] = dict(enumerate(master_files, 1))
    by_new: dict[int, Path] = dict(enumerate(new_files, 1))

    ctx = _build_render_ctx(
        master_pack_dir=master_pack_dir,
        new_pack_dir=new_pack_dir,
        font_path=font_path,
        nick=nick,
        content_color=content_color,
        logo_svg=logo_svg,
        per_emoji=per_emoji,
        new_per_emoji=new_per_emoji,
        global_transform=global_transform,
    )

    added: list = []
    errors: list[dict] = []

    for raw_id in tile_ids:
        if isinstance(raw_id, int) or (isinstance(raw_id, str) and not raw_id.startswith("new:")):
            try:
                idx = int(raw_id)
            except (TypeError, ValueError):
                errors.append({"id": raw_id, "src": "", "message": "bad id"})
                continue
            src = by_master.get(idx)
            kind = "master"
            ov = (per_emoji or {}).get(idx) if per_emoji else None
            norm_id: object = idx
        else:
            try:
                idx = int(str(raw_id)[4:])
            except (TypeError, ValueError):
                errors.append({"id": raw_id, "src": "", "message": "bad id"})
                continue
            src = by_new.get(idx)
            kind = "new"
            ov = (new_per_emoji or {}).get(idx) if new_per_emoji else None
            norm_id = f"new:{idx}"

        if src is None:
            errors.append({"id": raw_id, "src": "", "message": "not found"})
            continue

        try:
            new_bytes = _render_tile_bytes(ctx, kind=kind, src=src, ov=ov)
        except Exception as exc:
            log.exception("add: render failed for %s", src)
            errors.append({"id": raw_id, "src": src.name, "message": f"render: {exc}"})
            continue

        if len(new_bytes) > TG_TGS_LIMIT:
            errors.append(
                {
                    "id": raw_id,
                    "src": src.name,
                    "message": f"size {len(new_bytes)} > 64KB",
                }
            )
            continue

        sticker = InputSticker(
            sticker=BufferedInputFile(new_bytes, filename=src.name),
            format="animated",
            emoji_list=[_filename_emoji(src)],
        )

        flood_retries = 0
        while True:
            try:
                await bot.add_sticker_to_set(
                    user_id=user_id,
                    name=short_name,
                    sticker=sticker,
                )
                added.append(norm_id)
                break
            except TelegramRetryAfter as e:
                if flood_retries >= 5:
                    errors.append({"id": raw_id, "src": src.name, "message": f"flood: {e}"})
                    break
                flood_retries += 1
                await asyncio.sleep(e.retry_after + 1)
            except Exception as exc:
                log.exception("add_sticker_to_set failed for %s", src)
                errors.append({"id": raw_id, "src": src.name, "message": str(exc)})
                break

    return added, errors
