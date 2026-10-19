"""aiohttp server for the pack editor mini-app.

Routes:
    GET  /                      redirect to /editor
    GET  /editor                webapp/index.html
    GET  /static/{name}         files from webapp/
    GET  /api/master/list       the 144 master tiles (id, emoji)
    GET  /api/master/manifest   master_pack_positions.json
    GET  /api/master/{id}.tgs   original gzipped Lottie
    POST /api/build_pack        start a pack build (auth via initData HMAC)

Telegram only loads mini-apps over HTTPS, so in production this listens on
webapp_host:webapp_port behind a TLS-terminating proxy (nginx, cloudflared).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import hashlib
import hmac
import json as _json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs

from aiohttp import web

log = logging.getLogger("webapp_server")

EMOJI_FILENAME_RE = re.compile(r"^(\d+)_([0-9a-f]+)")
NEW_FILENAME_RE = re.compile(r"^(\d{19})\.tgs$")
_HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")

# Price of adding one missing tile to an existing pack.
PACK_ADDITION_PRICE_RUB = 2

# Strong references to fire-and-forget build tasks, so they aren't
# garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()

# Telegram per-set caps. Custom-emoji sets allow up to 200 stickers; regular
# sticker sets are capped at 120.
TG_EMOJI_SET_LIMIT = 200
TG_STICKER_SET_LIMIT = 120


def _pack_link(short_name: str, fmt: str) -> str:
    prefix = "addstickers" if fmt == "sticker" else "addemoji"
    return f"https://t.me/{prefix}/{short_name}"


def _slim_per_emoji(d: dict) -> dict:
    """Drop heavy/internal fields from a saved per_emoji map so it's safe to
    return over the wire. `logo_svg_b64` stays out (can be 100s of KB) —
    callers get a `has_logo` flag instead."""
    out: dict = {}
    for k, v in (d or {}).items():
        if not isinstance(v, dict):
            continue
        pruned = {
            kk: vv
            for kk, vv in v.items()
            if kk in ("scale", "offset_x", "offset_y", "rotation", "content_color", "text", "kind")
        }
        if v.get("logo_svg_b64") or v.get("logo_svg"):
            pruned["has_logo"] = True
        if pruned:
            out[str(k)] = pruned
    return out


def _clean_tile_override(ov: dict) -> dict:
    """Validate + clamp a per-tile override dict sent from the mini-app's
    pack-tile editor. Returns ALL keys present in the input (even at default
    values) so a merge with the saved baseline reflects the user's intent —
    if they slid the color back to white, white wins."""
    out: dict = {}
    if "scale" in ov:
        with contextlib.suppress(TypeError, ValueError):
            out["scale"] = max(0.5, min(5.0, float(ov["scale"])))
    for k in ("offset_x", "offset_y"):
        if k in ov:
            with contextlib.suppress(TypeError, ValueError):
                out[k] = max(-400.0, min(400.0, float(ov[k])))
    if "rotation" in ov:
        with contextlib.suppress(TypeError, ValueError):
            out["rotation"] = float(ov["rotation"])
    if "content_color" in ov and isinstance(ov["content_color"], str):
        c = ov["content_color"].strip().lower()
        if re.match(r"^#[0-9a-f]{6}$", c):
            out["content_color"] = c
    if "text" in ov and isinstance(ov["text"], str):
        # Empty string is allowed: it means "no custom text — fall back to
        # the pack's global nick".
        out["text"] = ov["text"].strip()[:30]
    if "kind" in ov and ov["kind"] in ("text", "logo"):
        out["kind"] = ov["kind"]
    if "logo_svg_b64" in ov and isinstance(ov["logo_svg_b64"], str):
        b64 = ov["logo_svg_b64"].strip()
        # Cap size at ~340KB of base64 (=~256KB of SVG) to match main editor.
        if 0 < len(b64) <= 340_000:
            out["logo_svg_b64"] = b64
        elif b64 == "":
            # Empty string = explicit "remove per-tile logo" — keep so the
            # merge can wipe a previously-saved per-tile logo.
            out["logo_svg_b64"] = ""
    return out


def _hydrate_per_emoji(
    raw_per: dict,
    *,
    with_logo: bool,
    nick: str,
    content_color: str,
    logo_svg: bytes | None,
) -> dict:
    """Convert payload-style per_emoji dict (with photo_svg_b64 strings) into
    the internal pack_builder format used by _render_tile_bytes.

    Honors per-tile `text`, `kind`, and `logo_svg_b64` overrides — falling
    back to pack global values only when the override is absent or blank.
    """
    out: dict = {}
    for idx, tr in raw_per.items():
        if not isinstance(tr, dict):
            continue

        # Per-tile logo: explicit b64 → bytes. Empty string means "drop
        # the saved per-tile logo and inherit pack global".
        tile_logo_bytes: bytes | None = None
        logo_b64 = tr.get("logo_svg_b64")
        if isinstance(logo_b64, str) and logo_b64:
            try:
                tile_logo_bytes = base64.b64decode(logo_b64)
            except ValueError:
                tile_logo_bytes = None

        # Kind resolution: explicit override > implied by logo presence >
        # pack global mode.
        kind_explicit = tr.get("kind") if tr.get("kind") in ("text", "logo") else None
        if kind_explicit:
            kind = kind_explicit
        elif tile_logo_bytes:
            kind = "logo"
        else:
            kind = "logo" if with_logo else "text"

        # Text resolution: non-empty override > pack nick.
        tr_text = tr.get("text")
        text_val = tr_text if (isinstance(tr_text, str) and tr_text.strip()) else (nick or "")

        entry: dict = {
            "kind": kind,
            "text": text_val,
            "scale": tr.get("scale", 1.0),
            "offset_x": tr.get("offset_x", 0.0),
            "offset_y": tr.get("offset_y", 0.0),
            "rotation": tr.get("rotation", 0.0),
            "content_color": tr.get("content_color", content_color),
        }
        if tr.get("lines"):
            entry["lines"] = tr["lines"]
        if tr.get("photo_svg_b64"):
            with contextlib.suppress(Exception):
                entry["photo_svg"] = base64.b64decode(tr["photo_svg_b64"])
        if tr.get("photo_scale") is not None:
            with contextlib.suppress(TypeError, ValueError):
                entry["photo_scale"] = float(tr["photo_scale"])
        # Advanced colour editor: { "#oldhex": "#newhex", … }
        cm = tr.get("color_remap")
        if isinstance(cm, dict) and cm:
            cleaned: dict[str, str] = {}
            for ok, ov in cm.items():
                ok_s = str(ok).strip().lower()
                ov_s = str(ov).strip().lower()
                if ok_s != ov_s and _HEX_COLOR.match(ok_s) and _HEX_COLOR.match(ov_s):
                    cleaned[ok_s] = ov_s
            if cleaned:
                entry["color_remap"] = cleaned

        # Logo source: per-tile bytes > pack global (only when in logo mode).
        if tile_logo_bytes:
            entry["logo_svg"] = tile_logo_bytes
        elif kind == "logo" and logo_svg:
            entry["logo_svg"] = logo_svg

        out[idx] = entry
    return out


# Files whose Lottie tree contains `gr` nodes named `swex_text_<text>` have
# multiple editable text lines (the passport tile baked by
# tools/swex_emoji_stickers.py). We extract the original texts ordered top→
# bottom so the webapp can show one input per line.
_SWEX_TEXT_NM_RE = re.compile(r"^swex_text_(.+)$")


def _find_swex_text_lines(tree: dict) -> list[dict]:
    """Return [{name, text, y}] for each swex_text_<text> gr, sorted by Y."""
    out: list[dict] = []
    seen_names: set[str] = set()

    def visit(node):
        if isinstance(node, list):
            for c in node:
                visit(c)
            return
        if not isinstance(node, dict):
            return
        nm = node.get("nm")
        if isinstance(nm, str) and node.get("ty") == "gr":
            m = _SWEX_TEXT_NM_RE.match(nm)
            if m and nm not in seen_names:
                seen_names.add(nm)
                # Approximate Y for ordering from the first sh vertex inside.
                y = _first_vertex_y(node)
                out.append({"name": nm, "text": m.group(1), "y": y})
        for v in node.values() if isinstance(node, dict) else []:
            if isinstance(v, (list, dict)):
                visit(v)

    visit(tree)
    out.sort(key=lambda e: e["y"])
    return out


def _first_vertex_y(node) -> float:
    """Find the Y of the first sh vertex anywhere under `node` (depth-first)."""
    if isinstance(node, list):
        for c in node:
            r = _first_vertex_y(c)
            if r is not None:
                return r
        return 1e9
    if isinstance(node, dict):
        if node.get("ty") == "sh":
            k = node.get("ks", {}).get("k", {})
            if isinstance(k, dict):
                v = k.get("v")
                if isinstance(v, list) and v and isinstance(v[0], list) and len(v[0]) >= 2:
                    return float(v[0][1])
        for val in node.values():
            if isinstance(val, (list, dict)):
                r = _first_vertex_y(val)
                if r is not None and r < 1e9:
                    return r
    return 1e9


def _passport_tile_ids(new_pack_dir: Path | None) -> set[str]:
    """IDs of new-tab tiles that are 'passport' style (have swex_text_*).

    Returns a set of "new:N" strings (1-based, matching `_list_new_files`).
    The passport surcharges the pack price by `pack_passport_price_rub`.
    """
    if not new_pack_dir or not new_pack_dir.exists():
        return set()
    out: set[str] = set()
    for idx, p in enumerate(sorted(new_pack_dir.glob("*.tgs")), start=1):
        if _probe_passport_lines(p) is not None:
            out.add(f"new:{idx}")
    return out


def _probe_passport_lines(path: Path) -> list[dict] | None:
    """If the .tgs file has swex_text_* groups, return their ordered metadata.

    Prepends a synthetic `__header__` row so the webapp can offer a 1st-row
    input for the s/w/e/x letter container (the passport "name" line). The
    global nick input is suppressed on passport tiles; users edit the header
    here instead.
    """
    try:
        tree = _json.loads(gzip.decompress(path.read_bytes()))
    except Exception:
        return None
    lines = _find_swex_text_lines(tree)
    if not lines:
        return None
    out = [{"name": "__header__", "text": "swex"}]
    out.extend({"name": ln["name"], "text": ln["text"]} for ln in lines)
    return out


def _list_master_files(master_pack_dir: Path) -> list[dict]:
    """[{id, file, emoji, v}] sorted by id.

    `v` is the file's mtime, appended by the client as `?v=` for cache busting:
    a rebuilt .tgs gets a new URL and the WebView fetches it immediately.
    """
    out: list[dict] = []
    for p in sorted(master_pack_dir.glob("*.tgs")):
        m = EMOJI_FILENAME_RE.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        try:
            char = bytes.fromhex(m.group(2)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            char = "🎭"
        try:
            v = int(p.stat().st_mtime)
        except OSError:
            v = 0
        out.append({"id": idx, "file": p.name, "emoji": char, "v": v})
    return out


def _list_new_files(new_pack_dir: Path) -> list[dict]:
    """[{id, file, emoji, v, [passport_lines]}] for emoji_stickers_swex/.

    Files are named `<telegram_emoji_id>.tgs`. Client ids are "new:<idx>" (from 1)
    so they never collide with master tiles 1–144.
    """
    out: list[dict] = []
    if not new_pack_dir.exists():
        return out
    files = sorted(new_pack_dir.glob("*.tgs"))
    for idx, p in enumerate(files, start=1):
        try:
            v = int(p.stat().st_mtime)
        except OSError:
            v = 0
        item: dict = {"id": f"new:{idx}", "file": p.name, "emoji": "✨", "v": v}
        lines = _probe_passport_lines(p)
        if lines:
            item["passport_lines"] = lines
        out.append(item)
    return out


def _validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram WebApp initData HMAC. Returns user dict or None."""
    if not init_data or not bot_token:
        return None
    params = parse_qs(init_data, keep_blank_values=True)
    flat = {k: v[0] for k, v in params.items()}
    check_hash = flat.pop("hash", None)
    if not check_hash:
        return None
    data_check_string = "\n".join(f"{k}={flat[k]}" for k in sorted(flat))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        hashlib.sha256,
    ).digest()
    computed = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, check_hash):
        return None
    user_str = flat.get("user")
    if not user_str:
        return None
    try:
        return _json.loads(user_str)
    except Exception:
        return None


async def _do_build_pack(
    app: web.Application,
    user_id: int,
    per_emoji_transforms: dict,
    new_per_emoji_transforms: dict,
    selected: set | None,
    pack_count: int,
    has_passport: bool,
    pack_title: str,
) -> None:
    """Background task: build the pack and notify user via bot."""
    bot = app["bot"]
    try:
        await _do_build_pack_inner(
            app,
            user_id,
            per_emoji_transforms,
            new_per_emoji_transforms,
            selected,
            pack_count,
            has_passport,
            pack_title,
        )
    except Exception:
        log.exception("_do_build_pack crashed for user %d", user_id)
        with contextlib.suppress(Exception):
            await bot.send_message(
                user_id,
                "❌ <b>Ошибка сборки пака из редактора. Попробуй ещё раз.</b>",
                parse_mode="HTML",
            )


async def _do_build_pack_inner(
    app: web.Application,
    user_id: int,
    per_emoji_transforms: dict,
    new_per_emoji_transforms: dict,
    selected: set | None,
    pack_count: int,
    has_passport: bool,
    pack_title: str,
) -> None:
    from aiogram.enums import ParseMode

    from handlers.pack import MASTER_PACK_DIR, _resolve_font_path
    from services.content import t
    from services.pack_builder import build_pack
    from services.screens import safe_edit
    from states import pack_store

    bot = app["bot"]
    cfg = app["cfg"]
    db = app["db"]
    billing = app["billing"]
    profile_db = app["profile_db"]

    s = pack_store.get(user_id)
    with_logo = bool(s.logo_svg)
    global_transform: dict | None = None
    content_rotation = float(getattr(s, "content_rotation", getattr(s, "logo_rotation", 0.0)))
    content_color = str(getattr(s, "content_color", "#ffffff") or "#ffffff")
    if abs(content_rotation) > 1e-6:
        global_transform = {
            "scale": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
            "rotation_deg": content_rotation,
        }

    user = await db.get_user(user_id)
    if not user:
        await bot.send_message(user_id, "❌ Юзер не найден в БД")
        return

    price_rub = await billing.quote_pack_price_rub(
        count=pack_count,
        has_passport=has_passport,
    )
    price_kop = price_rub * 100
    if (user.get("balance_kop") or 0) < price_kop:
        await bot.send_message(
            user_id,
            t("pack.no_funds", balance=f"{user['balance_kop'] / 100:.0f}", price=price_rub),
            parse_mode=ParseMode.HTML,
        )
        return

    ok, balance_kop, charged_kop = await billing.charge_pack(
        user_id,
        count=pack_count,
        has_passport=has_passport,
    )
    if not ok:
        await bot.send_message(
            user_id,
            t("pack.no_funds", balance=f"{balance_kop / 100:.0f}", price=charged_kop // 100),
            parse_mode=ParseMode.HTML,
        )
        return

    progress = await bot.send_message(
        user_id,
        t("pack.progress", i=0, total="…"),
        parse_mode=ParseMode.HTML,
    )

    bot_username = cfg.bot_username or (await bot.get_me()).username or "bot"
    title_suffix = f" by @{bot_username}"
    progress_html = (
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        "<b>Создаю пак…</b>\n"
        "<blockquote><b>Готово: {i}/{total}</b></blockquote>"
    )

    per_emoji: dict | None = None
    if per_emoji_transforms:
        per_emoji = _hydrate_per_emoji(
            per_emoji_transforms,
            with_logo=with_logo,
            nick=s.nick or "",
            content_color=content_color,
            logo_svg=s.logo_svg,
        )
    new_per_emoji: dict | None = None
    if new_per_emoji_transforms:
        new_per_emoji = _hydrate_per_emoji(
            new_per_emoji_transforms,
            with_logo=with_logo,
            nick=s.nick or "",
            content_color=content_color,
            logo_svg=s.logo_svg,
        )

    new_pack_dir = app.get("new_pack_dir")

    try:
        ok_build, err_html, info = await build_pack(
            bot=bot,
            user_id=user_id,
            nick=s.nick or "logo",
            pack_title=pack_title or None,
            master_pack_dir=MASTER_PACK_DIR,
            font_path=_resolve_font_path(s.font),
            bot_username=bot_username,
            pack_format=s.format or "emoji",
            title_suffix=title_suffix,
            logo_svg=s.logo_svg,
            content_color=content_color,
            status=progress,
            progress_template_html=progress_html,
            per_emoji=per_emoji,
            new_per_emoji=new_per_emoji,
            new_pack_dir=new_pack_dir,
            selected=selected,
            global_transform=global_transform,
        )
    except Exception as exc:
        log.exception("webapp build_pack crashed")
        await billing.refund_pack(user_id, charged_kop)
        await safe_edit(
            progress,
            t("pack.error", detail=f"<code>{exc}</code>"),
        )
        return

    if not ok_build:
        await billing.refund_pack(user_id, charged_kop)
        await safe_edit(progress, t("pack.error", detail=err_html))
        return

    total = info.get("total", 0)
    skipped = len(info.get("skipped") or [])
    truncated = info.get("truncated", 0)
    count = max(0, total - skipped - truncated)
    link = info.get("link", "")
    label = "Лого" if with_logo else (s.nick or "swex")
    with contextlib.suppress(Exception):
        await profile_db.add_pack_stats(user_id, count)

    # Persist the pack so the "My Packs" view can reload + add missing tiles
    # later. We store the payload-shaped settings (b64 for any bytes) so the
    # JSON blob is fully reloadable.
    try:
        included_ids = info.get("included_ids") or []
        logo_b64_str = base64.b64encode(s.logo_svg).decode("ascii") if s.logo_svg else ""
        settings_blob = {
            "global": {
                "kind": "logo" if with_logo else "text",
                "text": s.nick or "",
                "logo_svg_b64": logo_b64_str,
                "content_color": content_color,
                "content_rotation": content_rotation,
                "font": getattr(s, "font", None) or "",
            },
            "per_emoji": per_emoji_transforms or {},
            "new_per_emoji": new_per_emoji_transforms or {},
        }
        await profile_db.save_pack(
            user_id=user_id,
            short_name=info.get("short_name", ""),
            title=(pack_title or label)[:40],
            fmt=info.get("format") or "emoji",
            included=included_ids,
            settings=settings_blob,
        )
    except Exception:
        log.exception("save_pack failed (non-fatal)")

    await safe_edit(
        progress,
        t("pack.success", nick=label, count=count, price=charged_kop // 100, link=link),
    )


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Telegram-Init-Data",
                "Access-Control-Max-Age": "86400",
            }
        )
    response: web.StreamResponse = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def build_app(
    *,
    master_pack_dir: Path,
    webapp_dir: Path,
    new_pack_dir: Path | None = None,
    bot=None,
    cfg=None,
    db=None,
    billing=None,
    profile_db=None,
) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot
    app["cfg"] = cfg
    app["db"] = db
    app["billing"] = billing
    app["profile_db"] = profile_db
    app["new_pack_dir"] = new_pack_dir
    app["master_pack_dir"] = master_pack_dir

    async def root(_req: web.Request) -> web.Response:
        raise web.HTTPFound("/editor")

    async def editor_page(_req: web.Request) -> web.FileResponse:
        index = webapp_dir / "index.html"
        if not index.exists():
            raise web.HTTPNotFound(text="webapp/index.html not found")
        return web.FileResponse(index, headers={"Cache-Control": "no-store"})

    async def static_file(req: web.Request) -> web.FileResponse:
        name = req.match_info["name"]
        # Subfolders like fonts/ are allowed; anything escaping webapp_dir is not.
        if ".." in name or name.startswith(("/", "\\")):
            raise web.HTTPForbidden()
        target = (webapp_dir / name).resolve()
        try:
            target.relative_to(webapp_dir.resolve())
        except ValueError:
            raise web.HTTPForbidden() from None
        if not target.exists() or not target.is_file():
            raise web.HTTPNotFound(text=name)
        return web.FileResponse(target)

    async def master_list(_req: web.Request) -> web.Response:
        return web.json_response(_list_master_files(master_pack_dir))

    async def master_manifest(_req: web.Request) -> web.Response:
        f = master_pack_dir / "master_pack_positions.json"
        if not f.exists():
            return web.json_response({})
        return web.FileResponse(f)

    async def master_file(req: web.Request) -> web.FileResponse:
        name = req.match_info["name"]
        if "/" in name or "\\" in name or ".." in name:
            raise web.HTTPForbidden()
        target = master_pack_dir / name
        if not target.exists() or not target.is_file():
            raise web.HTTPNotFound(text=name)
        # The client appends `?v=<mtime>`, so versioned URLs can be cached
        # forever; bare URLs get a short revalidated window instead.
        is_versioned = bool(req.query.get("v"))
        cache = (
            "public, max-age=31536000, immutable"
            if is_versioned
            else "public, max-age=60, must-revalidate"
        )
        return web.FileResponse(
            target,
            headers={
                "Content-Type": "application/octet-stream",
                "Cache-Control": cache,
            },
        )

    async def build_pack_api(req: web.Request) -> web.Response:
        """POST /api/build_pack — validates initData, starts pack build."""
        log.info("build_pack_api: request received")
        _bot = req.app.get("bot")
        _cfg = req.app.get("cfg")
        if not _bot or not _cfg:
            log.error("build_pack_api: bot or cfg not configured")
            return web.json_response({"error": "Сервер не настроен"}, status=500)

        init_data = req.headers.get("X-Telegram-Init-Data", "")
        user_data = _validate_init_data(init_data, _cfg.bot_token)
        if not user_data:
            log.warning("build_pack_api: invalid initData")
            return web.json_response({"error": "Невалидный initData"}, status=403)

        user_id = user_data.get("id")
        if not user_id:
            return web.json_response({"error": "Нет user ID"}, status=403)
        user_id = int(user_id)

        try:
            payload = await req.json()
        except Exception:
            return web.json_response({"error": "Невалидный JSON"}, status=400)

        # Parse global settings
        global_data = payload.get("global") or {}
        kind = str(global_data.get("kind") or "text")
        text = str(global_data.get("text") or "").strip()[:30]
        logo_b64 = str(global_data.get("logo_svg_b64") or "")
        content_color = str(global_data.get("content_color") or "#ffffff").strip() or "#ffffff"
        content_rotation = float(
            global_data.get("content_rotation", global_data.get("logo_rotation", 0.0)) or 0.0
        )
        fmt = str(payload.get("format") or "emoji").lower().strip()
        if fmt not in ("emoji", "sticker"):
            fmt = "emoji"

        # Update pack_store with global settings from mini-app
        from states import pack_store

        s = pack_store.get(user_id)
        s.content_rotation = content_rotation
        s.logo_rotation = content_rotation
        s.content_color = content_color
        if kind == "logo" and logo_b64:
            try:
                s.logo_svg = base64.b64decode(logo_b64)
            except Exception:
                return web.json_response({"error": "Невалидный base64 лого"}, status=400)
        elif kind == "text" and text:
            s.nick = text
            s.logo_svg = None
        s.format = fmt

        # Validation of "either nick or logo" is moved below — see after
        # `selected` is parsed. Solo-passport builds don't need globals.

        # Parse per-emoji transforms (only emojis with custom size/position).
        # Master tiles are keyed by int 1..144, new tiles by string "new:1"…
        raw_per = payload.get("per_emoji") or {}
        per_emoji_transforms: dict = {}
        new_per_emoji_transforms: dict = {}
        for k, v in raw_per.items():
            if not isinstance(v, dict):
                continue
            ks = str(k)
            new_idx: int | None = None
            master_idx: int | None = None
            if ks.startswith("new:"):
                try:
                    new_idx = int(ks[4:])
                except ValueError:
                    continue
            else:
                try:
                    master_idx = int(ks)
                except ValueError:
                    continue
            sc = max(0.5, min(5.0, float(v.get("scale", 1.0))))
            ox = float(v.get("offset_x", 0.0))
            oy = float(v.get("offset_y", 0.0))
            rot = float(v.get("rotation", 0.0))
            per_color = str(v.get("content_color") or "#ffffff").strip() or "#ffffff"
            raw_lines = v.get("lines")
            lines: list[dict] = []
            if isinstance(raw_lines, list):
                for ln in raw_lines:
                    if not isinstance(ln, dict):
                        continue
                    nm = str(ln.get("name") or "")
                    if not nm:
                        continue
                    tx = str(ln.get("text") or "")[:30]
                    ln_scale = float(ln.get("scale", 1.0) or 1.0)
                    ln_scale = max(0.3, min(3.0, ln_scale))
                    ln_color = str(ln.get("color") or "#000000").strip() or "#000000"
                    ln_ox = float(ln.get("offset_x", 0.0) or 0.0)
                    ln_oy = float(ln.get("offset_y", 0.0) or 0.0)
                    # Clamp offsets to sane range so a bad client can't
                    # send the text off-canvas.
                    ln_ox = max(-300.0, min(300.0, ln_ox))
                    ln_oy = max(-300.0, min(300.0, ln_oy))
                    lines.append(
                        {
                            "name": nm,
                            "text": tx,
                            "scale": ln_scale,
                            "color": ln_color,
                            "offset_x": ln_ox,
                            "offset_y": ln_oy,
                        }
                    )
            has_lines = any(
                ln["text"].strip()
                or abs(ln["scale"] - 1.0) > 1e-3
                or ln["color"].lower() != "#000000"
                or abs(ln["offset_x"]) > 1e-6
                or abs(ln["offset_y"]) > 1e-6
                for ln in lines
            )
            photo_b64 = str(v.get("photo_svg_b64") or "")
            photo_scale = float(v.get("photo_scale", 1.0) or 1.0)
            photo_scale = max(0.3, min(3.0, photo_scale))
            has_photo_scale = photo_b64 and abs(photo_scale - 1.0) > 1e-3
            if photo_b64:
                log.warning(
                    "[webapp_server] payload tile=%s photo_scale_raw=%r "
                    "photo_scale_clamped=%.3f has_photo_scale=%s payload_keys=%s",
                    ks,
                    v.get("photo_scale"),
                    photo_scale,
                    has_photo_scale,
                    sorted(v.keys()),
                )
            if (
                abs(sc - 1.0) > 1e-6
                or abs(ox) > 1e-6
                or abs(oy) > 1e-6
                or abs(rot) > 1e-6
                or per_color.lower() != "#ffffff"
                or has_lines
                or photo_b64
            ):
                entry = {
                    "scale": sc,
                    "offset_x": ox,
                    "offset_y": oy,
                    "rotation": rot,
                    "content_color": per_color,
                }
                if has_lines:
                    entry["lines"] = lines
                if photo_b64:
                    entry["photo_svg_b64"] = photo_b64
                if has_photo_scale:
                    entry["photo_scale"] = photo_scale
                if new_idx is not None:
                    new_per_emoji_transforms[new_idx] = entry
                elif master_idx is not None:
                    per_emoji_transforms[master_idx] = entry

        # Parse `selected` (sticker mode subset). null/missing → all.
        raw_selected = payload.get("selected")
        selected: set | None = None
        if isinstance(raw_selected, list):
            selected = set()
            for x in raw_selected:
                xs = str(x)
                if xs.startswith("new:"):
                    selected.add(xs)
                else:
                    try:
                        selected.add(int(xs))
                    except ValueError:
                        continue

        pack_title = str(payload.get("pack_title") or "").strip()[:40]

        # Compute pricing inputs from the selection. Passport tiles are
        # priced separately; everything else counts toward the tier.
        passport_ids = _passport_tile_ids(req.app.get("new_pack_dir"))
        if selected is None:
            # Default: include every tile except passports.
            new_dir = req.app.get("new_pack_dir")
            new_total = len(list(new_dir.glob("*.tgs"))) if (new_dir and new_dir.exists()) else 0
            master_dir = req.app.get("master_pack_dir")
            try:
                master_total = len(list(master_dir.glob("*.tgs"))) if master_dir else 144
            except Exception:
                master_total = 144
            has_passport = False
            pack_count = master_total + new_total - len(passport_ids)
            sel_only_passports = False
        else:
            sel_str = {str(x) for x in selected}
            has_passport = bool(sel_str & passport_ids)
            pack_count = sum(1 for x in sel_str if x not in passport_ids)
            sel_only_passports = bool(sel_str) and sel_str.issubset(passport_ids)

        # The global nick/logo only applies to non-passport tiles, so a
        # passport-only pack skips that validation.
        if not sel_only_passports and not s.nick and not s.logo_svg:
            return web.json_response({"error": "Введи текст или загрузи лого"}, status=400)
        log.info(
            "build_pack_api: user=%d kind=%s nick=%r master_tf=%d new_tf=%d sel=%s count=%d passport=%s → starting build",
            user_id,
            kind,
            s.nick,
            len(per_emoji_transforms),
            len(new_per_emoji_transforms),
            "all" if selected is None else len(selected),
            pack_count,
            has_passport,
        )
        task = asyncio.create_task(
            _do_build_pack(
                req.app,
                user_id,
                per_emoji_transforms,
                new_per_emoji_transforms,
                selected,
                pack_count,
                has_passport,
                pack_title,
            ),
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return web.json_response({"ok": True})

    async def pricing_api(_req: web.Request) -> web.Response:
        """Return pricing constants + passport tile IDs so the client can
        compute the live total based on selection. Falls back to default
        constants when `billing` is not wired (dev preview)."""
        b = _req.app.get("billing")
        if b is not None:
            small = await b.get_pack_small_price_rub()
            full = await b.get_pack_full_price_rub()
            passport = await b.get_pack_passport_price_rub()
        else:
            from services.billing import (
                DEFAULT_PACK_FULL_PRICE_RUB,
                DEFAULT_PACK_PASSPORT_PRICE_RUB,
                DEFAULT_PACK_SMALL_PRICE_RUB,
            )

            small = DEFAULT_PACK_SMALL_PRICE_RUB
            full = DEFAULT_PACK_FULL_PRICE_RUB
            passport = DEFAULT_PACK_PASSPORT_PRICE_RUB
        return web.json_response(
            {
                "small_price_rub": small,
                "full_price_rub": full,
                "passport_price_rub": passport,
                "addition_price_rub": PACK_ADDITION_PRICE_RUB,
                "small_threshold": 20,
                "passport_ids": sorted(_passport_tile_ids(new_pack_dir)),
            }
        )

    def _gate(req: web.Request) -> int | None:
        """initData → user_id, or None if invalid."""
        if not _cfg_token():
            return None
        init_data = req.headers.get("X-Telegram-Init-Data", "")
        ud = _validate_init_data(init_data, _cfg_token())
        if not ud or not ud.get("id"):
            return None
        return int(ud["id"])

    def _cfg_token() -> str:
        c = app.get("cfg")
        return c.bot_token if c else ""

    async def packs_list_api(req: web.Request) -> web.Response:
        user_id = _gate(req)
        if user_id is None:
            return web.json_response({"error": "auth"}, status=403)
        pdb = req.app.get("profile_db")
        if pdb is None:
            return web.json_response([])
        items = await pdb.list_user_packs(user_id)
        master_total = len(list(master_pack_dir.glob("*.tgs")))
        new_total = (
            len(list(new_pack_dir.glob("*.tgs"))) if (new_pack_dir and new_pack_dir.exists()) else 0
        )
        out: list[dict] = []
        for it in items:
            settings = it.pop("settings", None) or {}
            cap = TG_EMOJI_SET_LIMIT if it["format"] == "emoji" else TG_STICKER_SET_LIMIT
            it["total"] = master_total + new_total
            it["cap"] = cap
            it["link"] = _pack_link(it["short_name"], it["format"])
            # Include the pack's appearance so mini-thumbs in the card can
            # render the actual customised look (nick/logo/color), not bare
            # master tiles. logo_svg_b64 is needed for accurate logo packs;
            # we keep it as-is (typical SVG ≲ 50KB).
            it["global"] = dict(settings.get("global") or {})
            it["per_emoji"] = _slim_per_emoji(settings.get("per_emoji"))
            it["new_per_emoji"] = _slim_per_emoji(settings.get("new_per_emoji"))
            out.append(it)
        return web.json_response(out)

    async def pack_get_api(req: web.Request) -> web.Response:
        user_id = _gate(req)
        if user_id is None:
            return web.json_response({"error": "auth"}, status=403)
        try:
            pid = int(req.match_info["pid"])
        except ValueError:
            return web.json_response({"error": "bad id"}, status=400)
        pdb = req.app.get("profile_db")
        if pdb is None:
            return web.json_response({"error": "no_db"}, status=500)
        rec = await pdb.get_pack(pid, user_id)
        if not rec:
            return web.json_response({"error": "not_found"}, status=404)
        cap = TG_EMOJI_SET_LIMIT if rec["format"] == "emoji" else TG_STICKER_SET_LIMIT
        master_total = len(list(master_pack_dir.glob("*.tgs")))
        new_total = (
            len(list(new_pack_dir.glob("*.tgs"))) if (new_pack_dir and new_pack_dir.exists()) else 0
        )
        global_view = dict(rec["settings"].get("global") or {})
        return web.json_response(
            {
                "id": rec["id"],
                "short_name": rec["short_name"],
                "title": rec["title"],
                "format": rec["format"],
                "included": rec["included"],
                "global": global_view,
                "per_emoji": _slim_per_emoji(rec["settings"].get("per_emoji")),
                "new_per_emoji": _slim_per_emoji(rec["settings"].get("new_per_emoji")),
                "total": master_total + new_total,
                "cap": cap,
                "link": _pack_link(rec["short_name"], rec["format"]),
                "created_at": rec["created_at"],
                "updated_at": rec["updated_at"],
            }
        )

    async def pack_rename_api(req: web.Request) -> web.Response:
        user_id = _gate(req)
        if user_id is None:
            return web.json_response({"error": "auth"}, status=403)
        try:
            pid = int(req.match_info["pid"])
        except ValueError:
            return web.json_response({"error": "bad id"}, status=400)
        try:
            payload = await req.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        new_title = str(payload.get("title") or "").strip()[:40]
        if not new_title:
            return web.json_response({"error": "Введите название"}, status=400)
        pdb = req.app.get("profile_db")
        rec = await pdb.get_pack(pid, user_id)
        if not rec:
            return web.json_response({"error": "not_found"}, status=404)
        bot = req.app.get("bot")
        cfg = req.app.get("cfg")
        bot_username = (cfg.bot_username if cfg else "") or "bot"
        full_title = (new_title + f" by @{bot_username}")[:64]
        try:
            await bot.set_sticker_set_title(
                name=rec["short_name"],
                title=full_title,
            )
        except Exception as exc:
            log.warning("set_sticker_set_title failed: %s", exc)
            return web.json_response({"error": f"Telegram: {exc}"}, status=400)
        await pdb.rename_pack(pid, user_id, new_title)
        return web.json_response(
            {
                "ok": True,
                "title": new_title,
                "full_title": full_title,
            }
        )

    async def pack_add_tiles_api(req: web.Request) -> web.Response:
        user_id = _gate(req)
        if user_id is None:
            return web.json_response({"error": "auth"}, status=403)
        try:
            pid = int(req.match_info["pid"])
        except ValueError:
            return web.json_response({"error": "bad id"}, status=400)
        try:
            payload = await req.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        raw_ids = payload.get("tile_ids") or []
        if not isinstance(raw_ids, list) or not raw_ids:
            return web.json_response({"error": "Список пуст"}, status=400)

        pdb = req.app.get("profile_db")
        rec = await pdb.get_pack(pid, user_id)
        if not rec:
            return web.json_response({"error": "not_found"}, status=404)

        # Filter to "actually missing" tiles (dedup vs included).
        included_set = {str(x) for x in rec["included"]}
        normalized: list = []
        for x in raw_ids:
            xs = str(x)
            if xs in included_set:
                continue
            if xs.startswith("new:"):
                normalized.append(xs)
            else:
                try:
                    normalized.append(int(xs))
                except ValueError:
                    continue
        if not normalized:
            return web.json_response({"error": "Эти эмодзи уже в паке"}, status=400)

        # Enforce TG cap so we don't pay for tiles Telegram will reject.
        cap = TG_EMOJI_SET_LIMIT if rec["format"] == "emoji" else TG_STICKER_SET_LIMIT
        slack = cap - len(rec["included"])
        if slack <= 0:
            return web.json_response({"error": "Пак заполнен под завязку"}, status=400)
        if len(normalized) > slack:
            normalized = normalized[:slack]

        # Charge upfront, refund failures at the end.
        price_kop = len(normalized) * PACK_ADDITION_PRICE_RUB * 100
        db = req.app.get("db")
        u = await db.get_user(user_id)
        if not u or (u.get("balance_kop") or 0) < price_kop:
            return web.json_response(
                {
                    "error": f"Недостаточно ₽. Нужно {price_kop // 100}₽",
                    "need_kop": price_kop,
                    "balance_kop": (u.get("balance_kop") if u else 0),
                },
                status=402,
            )
        await db.add_balance_kop(user_id, -price_kop)

        # Hydrate settings → pack_builder format.
        gset = rec["settings"].get("global") or {}
        per_raw = rec["settings"].get("per_emoji") or {}
        new_raw = rec["settings"].get("new_per_emoji") or {}
        # JSON keys are strings — restore ints.
        per_raw_i: dict = {}
        for k, v in per_raw.items():
            with contextlib.suppress(TypeError, ValueError):
                per_raw_i[int(k)] = dict(v) if isinstance(v, dict) else v
        new_raw_i: dict = {}
        for k, v in new_raw.items():
            with contextlib.suppress(TypeError, ValueError):
                new_raw_i[int(k)] = dict(v) if isinstance(v, dict) else v

        # Apply per-tile overrides sent by the client (mini-editor inside
        # the My Packs view). Master ids go into per_raw_i, "new:N" ids into
        # new_raw_i. We merge field-by-field so a partial override doesn't
        # wipe baseline transform inherited from the pack's saved per_emoji.
        raw_overrides = payload.get("overrides") or {}
        if isinstance(raw_overrides, dict):
            for k, ov in raw_overrides.items():
                if not isinstance(ov, dict):
                    continue
                ks = str(k)
                cleaned = _clean_tile_override(ov)
                if not cleaned:
                    continue
                target_idx, target_dict = None, None
                if ks.startswith("new:"):
                    try:
                        target_idx = int(ks[4:])
                        target_dict = new_raw_i
                    except (TypeError, ValueError):
                        continue
                else:
                    try:
                        target_idx = int(ks)
                        target_dict = per_raw_i
                    except (TypeError, ValueError):
                        continue
                base = target_dict.get(target_idx) or {}
                merged = dict(base)
                merged.update(cleaned)
                target_dict[target_idx] = merged

        nick = str(gset.get("text") or "")
        content_color = str(gset.get("content_color") or "#ffffff")
        content_rotation = float(gset.get("content_rotation") or 0.0)
        kind = str(gset.get("kind") or "text")
        logo_b64 = str(gset.get("logo_svg_b64") or "")
        logo_bytes: bytes | None = None
        if kind == "logo" and logo_b64:
            try:
                logo_bytes = base64.b64decode(logo_b64)
            except Exception:
                logo_bytes = None
        with_logo = bool(logo_bytes)
        per_emoji = (
            _hydrate_per_emoji(
                per_raw_i,
                with_logo=with_logo,
                nick=nick,
                content_color=content_color,
                logo_svg=logo_bytes,
            )
            if per_raw_i
            else None
        )
        new_per_emoji = (
            _hydrate_per_emoji(
                new_raw_i,
                with_logo=with_logo,
                nick=nick,
                content_color=content_color,
                logo_svg=logo_bytes,
            )
            if new_raw_i
            else None
        )
        global_transform = None
        if abs(content_rotation) > 1e-6:
            global_transform = {
                "scale": 1.0,
                "offset_x": 0.0,
                "offset_y": 0.0,
                "rotation_deg": content_rotation,
            }

        from handlers.pack import _resolve_font_path
        from services.pack_builder import add_tiles_to_pack

        font_key = gset.get("font") or ""
        font_path = _resolve_font_path(font_key)

        bot = req.app.get("bot")
        try:
            added, errors = await add_tiles_to_pack(
                bot=bot,
                user_id=user_id,
                short_name=rec["short_name"],
                master_pack_dir=master_pack_dir,
                new_pack_dir=new_pack_dir,
                font_path=font_path,
                tile_ids=normalized,
                nick=nick or ("logo" if with_logo else ""),
                content_color=content_color,
                logo_svg=logo_bytes,
                per_emoji=per_emoji,
                new_per_emoji=new_per_emoji,
                global_transform=global_transform,
            )
        except Exception as exc:
            log.exception("add_tiles_to_pack crashed")
            await db.add_balance_kop(user_id, price_kop)
            return web.json_response({"error": f"Ошибка сборки: {exc}"}, status=500)

        if added:
            await pdb.add_included_tiles(pid, user_id, added)

        # Refund the part user didn't get.
        refunded = (len(normalized) - len(added)) * PACK_ADDITION_PRICE_RUB * 100
        if refunded > 0:
            await db.add_balance_kop(user_id, refunded)

        # Notify the user via DM — the webapp closes/minimises and the user
        # may want to tap through to the updated pack in a single tap.
        if added:
            try:
                link = _pack_link(rec["short_name"], rec["format"])
                title_safe = (rec["title"] or "пак").replace("<", "&lt;").replace(">", "&gt;")
                # The inner character is the fallback for clients without Premium.
                emoji_top = '<tg-emoji emoji-id="5348220115953403873">✨</tg-emoji>'
                emoji_link = '<tg-emoji emoji-id="5346284060660494696">🔗</tg-emoji>'
                lines_html = [
                    f"{emoji_top} <b>Пак обновлён</b>",
                    f"<blockquote>"
                    f"<b>{title_safe}</b> — добавил "
                    f"<b>{len(added)}</b> "
                    f"{('эмодзи' if rec['format'] == 'emoji' else 'стикер(ов)')}"
                    + (f", не получилось <b>{len(errors)}</b>" if errors else "")
                    + f".\n"
                    f"Списал <b>{(price_kop - refunded) // 100} ₽</b>."
                    f"</blockquote>",
                    f'{emoji_link} <a href="{link}">Открыть пак</a>',
                ]
                await bot.send_message(
                    user_id,
                    "\n".join(lines_html),
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
            except Exception:
                log.exception("add_tiles: DM notification failed (non-fatal)")

        new_balance = (await db.get_user(user_id) or {}).get("balance_kop", 0)
        return web.json_response(
            {
                "ok": True,
                "added": [str(x) for x in added],
                "errors": errors,
                "charged_kop": price_kop - refunded,
                "refunded_kop": refunded,
                "balance_kop": new_balance,
                "link": _pack_link(rec["short_name"], rec["format"]),
            }
        )

    async def new_list(_req: web.Request) -> web.Response:
        if not new_pack_dir:
            return web.json_response([])
        return web.json_response(_list_new_files(new_pack_dir))

    async def new_file(req: web.Request) -> web.FileResponse:
        if not new_pack_dir:
            raise web.HTTPNotFound()
        name = req.match_info["name"]
        if "/" in name or "\\" in name or ".." in name:
            raise web.HTTPForbidden()
        target = new_pack_dir / name
        if not target.exists() or not target.is_file():
            raise web.HTTPNotFound(text=name)
        # See master_file for caching strategy: versioned URLs (`?v=mtime`)
        # are immutable for a year; unversioned URLs (legacy clients,
        # paranoia) revalidate every 60s.
        is_versioned = bool(req.query.get("v"))
        cache = (
            "public, max-age=31536000, immutable"
            if is_versioned
            else "public, max-age=60, must-revalidate"
        )
        return web.FileResponse(
            target,
            headers={
                "Content-Type": "application/octet-stream",
                "Cache-Control": cache,
            },
        )

    app.router.add_get("/", root)
    app.router.add_get("/editor", editor_page)
    app.router.add_get("/static/{name:.+}", static_file)
    app.router.add_get("/api/master/list", master_list)
    app.router.add_get("/api/master/manifest", master_manifest)
    app.router.add_get("/api/master/{name}", master_file)
    app.router.add_get("/api/new/list", new_list)
    app.router.add_get("/api/new/{name}", new_file)
    app.router.add_get("/api/pricing", pricing_api)
    app.router.add_post("/api/build_pack", build_pack_api)
    app.router.add_get("/api/packs/list", packs_list_api)
    app.router.add_get("/api/packs/{pid}", pack_get_api)
    app.router.add_post("/api/packs/{pid}/rename", pack_rename_api)
    app.router.add_post("/api/packs/{pid}/add_tiles", pack_add_tiles_api)
    return app


async def run_webapp(
    *,
    master_pack_dir: Path,
    webapp_dir: Path,
    host: str,
    port: int,
    new_pack_dir: Path | None = None,
    bot=None,
    cfg=None,
    db=None,
    billing=None,
    profile_db=None,
) -> tuple[web.AppRunner, web.TCPSite]:
    """Start the aiohttp server and return (runner, site) for shutdown."""
    app = build_app(
        master_pack_dir=master_pack_dir,
        webapp_dir=webapp_dir,
        new_pack_dir=new_pack_dir,
        bot=bot,
        cfg=cfg,
        db=db,
        billing=billing,
        profile_db=profile_db,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("webapp server listening on %s:%d", host, port)
    return runner, site
