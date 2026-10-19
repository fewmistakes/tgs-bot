"""3×3 pack preview.

Renders nine master tiles with the user's nickname or logo applied, lays them
out on a dark canvas and covers it with a diagonal @bot_username watermark.
Rendering is CPU-heavy, so it runs in a thread executor.
"""

from __future__ import annotations

import asyncio
import math
import os
import secrets
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from services.svg_to_lottie import svg_to_lottie_shapes
from services.swex.glyphs import FontRenderer
from services.swex.replacer import (
    _load_positions,
    _tile_id,
    replace_swex_bytes,
    replace_swex_with_logo_bytes,
)
from services.tgs_renderer import get_metadata, render_frames
from services.watermark import _load_font  # already handles Google Fonts downloads
from utils.logger import get_logger

log = get_logger("pack_preview")


PREVIEW_TILE_PX = 220  # side of a single tile
PREVIEW_GAP_PX = 6  # gap between tiles
PREVIEW_BG_RGB = (28, 28, 30)


def _pick_indices(total: int, n: int = 9) -> list[int]:
    """n evenly spaced indices in [0, total)."""
    if total <= n:
        return list(range(total))
    return [round(i * (total - 1) / (n - 1)) for i in range(n)]


def _render_one_tile(
    src_tgs_bytes: bytes,
    *,
    use_logo: bool,
    nick: str,
    renderer: FontRenderer | None,
    logo_shapes: list[dict],
    logo_bbox: tuple[float, float, float, float],
    tile_px: int,
    override_center: tuple[float, float] | None = None,
    override_size: tuple[float, float] | None = None,
    override_min_px: float | None = None,
    override_max_px: float | None = None,
    override_pad: float | None = None,
) -> np.ndarray:
    """Apply the nick/logo and render the tile's final frame as a tile_px² RGBA array."""
    if use_logo:
        new_bytes, _ = replace_swex_with_logo_bytes(
            src_tgs_bytes,
            logo_shapes,
            logo_bbox,
            override_center=override_center,
            override_size=override_size,
            override_min_px=override_min_px,
            override_max_px=override_max_px,
            override_pad=override_pad,
        )
    else:
        new_bytes, _ = replace_swex_bytes(src_tgs_bytes, nick, renderer)

    fd, tmp_str = tempfile.mkstemp(suffix=".tgs", prefix="prev_")
    os.close(fd)
    tmp = Path(tmp_str)
    try:
        tmp.write_bytes(new_bytes)
        # Sample near the end, not at frame 0: many master tiles open with an
        # "exit" effect on the original letters (precomp refs scaling up from
        # [4, 46.2] etc.), so the first frame shows the effect's start state
        # rather than the text. The final frame is a stable pose.
        try:
            total_frames, native_fps, _w, _h = get_metadata(tmp)
        except Exception:
            total_frames, native_fps = 30, 60.0
        duration = max(0.05, total_frames / max(native_fps, 1.0))
        frames = render_frames(
            tmp,
            out_size=tile_px,
            fps=4,
            max_seconds=duration,
            workers=1,
            supersample=1.5,
        )
    finally:
        tmp.unlink(missing_ok=True)

    if not frames:
        # empty slot stays transparent
        return np.zeros((tile_px, tile_px, 4), dtype=np.uint8)
    return frames[-1]


def _alpha_paste_over_rgb(
    base: Image.Image,
    overlay: np.ndarray,
    x: int,
    y: int,
) -> None:
    """Alpha-blend an RGBA array onto an RGB image at (x, y), in place."""
    img = Image.fromarray(overlay, "RGBA")
    base.paste(img, (x, y), img)


def _draw_diagonal_watermark(
    canvas: Image.Image,
    text: str,
    *,
    tile_px: int,
    gap_px: int,
    grid_n: int = 3,
) -> None:
    """Cover the canvas with a repeating diagonal @username.

    A single large translucent layer is drawn, rotated, cropped and blended on
    top, so the stripes run uninterrupted across tile borders.
    """
    cw, ch = canvas.size
    # ~10–11% of the tile height.
    font_size = max(14, int(tile_px * 0.105))
    font = _load_font("bold", font_size)

    # One repeated line spans the diagonal of three tiles plus some slack.
    diag_len = int(math.hypot(cw, ch)) + 200

    # Repeat the text in parallel stripes at -25° on a transparent layer.
    # A vertical step of ~3× font size keeps lines from merging and puts
    # one or two marks on every tile.
    layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    # Draw on a wider canvas, then rotate.
    big = Image.new(
        "RGBA",
        (diag_len, ch + diag_len),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(big)
    bbox = draw.textbbox((0, 0), text, font=font)
    bbox[3] - bbox[1]
    line_step = max(font_size + 6, int(tile_px * 0.55))

    spacing = int(font_size * 1.2)  # gap between repeats
    one_w = bbox[2] - bbox[0]
    repeats = diag_len // (one_w + spacing) + 2

    y = -line_step
    while y < big.size[1]:
        # offset every other row by half a period
        x_off = (y // line_step) % 2 * ((one_w + spacing) // 2)
        for i in range(repeats):
            draw.text(
                (i * (one_w + spacing) - x_off, y),
                text,
                font=font,
                fill=(255, 255, 255, 110),  # white at ~43% opacity
            )
        y += line_step

    rotated = big.rotate(-25, resample=Image.BICUBIC, expand=True)
    rw, rh = rotated.size
    crop_x = (rw - cw) // 2
    crop_y = (rh - ch) // 2
    rotated = rotated.crop((crop_x, crop_y, crop_x + cw, crop_y + ch))
    layer.paste(rotated, (0, 0), rotated)

    canvas.alpha_composite(layer)


def _compose_grid_sync(
    *,
    files: list[Path],
    nick: str,
    use_logo: bool,
    logo_svg_bytes: bytes | None,
    font_path: Path,
    bot_username: str,
    out_path: Path,
) -> None:
    """Blocking part of the preview build; runs in a thread pool."""
    if not files:
        raise RuntimeError("master_pack пуст")

    indices = _pick_indices(len(files), n=9)
    chosen = [files[i] for i in indices]

    logo_shapes: list[dict] = []
    logo_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    renderer: FontRenderer | None = None

    if use_logo and logo_svg_bytes:
        logo_shapes, logo_bbox = svg_to_lottie_shapes(logo_svg_bytes)
        if not logo_shapes:
            raise RuntimeError("в SVG нет путей")
    else:
        renderer = FontRenderer(font_path)

    # Per-tile centres, generated by tools/build_master_pack_positions.py.
    master_dir = files[0].parent if files else Path(".")
    positions = _load_positions(master_dir)

    tiles: list[np.ndarray] = []
    for src in chosen:
        tid = _tile_id(src)
        entry = positions.get(tid) if tid else None
        center = (entry["cx"], entry["cy"]) if entry else None
        size = (entry["w"], entry["h"]) if entry and "w" in entry and "h" in entry else None
        try:
            tile = _render_one_tile(
                src.read_bytes(),
                use_logo=use_logo,
                nick=nick,
                renderer=renderer,
                logo_shapes=logo_shapes,
                logo_bbox=logo_bbox,
                tile_px=PREVIEW_TILE_PX,
                override_center=center,
                override_size=size,
                override_min_px=entry.get("min_px") if entry else None,
                override_max_px=entry.get("max_px") if entry else None,
                override_pad=entry.get("pad") if entry else None,
            )
        except Exception as exc:
            log.warning(
                "preview tile %s failed: %r",
                src.name,
                exc,
                exc_info=True,
            )
            tile = np.zeros((PREVIEW_TILE_PX, PREVIEW_TILE_PX, 4), dtype=np.uint8)
        tiles.append(tile)

    # 3×3 grid with gaps.
    side = PREVIEW_TILE_PX * 3 + PREVIEW_GAP_PX * 2
    canvas = Image.new("RGBA", (side, side), (*PREVIEW_BG_RGB, 255))

    for i, tile in enumerate(tiles):
        row, col = divmod(i, 3)
        x = col * (PREVIEW_TILE_PX + PREVIEW_GAP_PX)
        y = row * (PREVIEW_TILE_PX + PREVIEW_GAP_PX)
        _alpha_paste_over_rgb(canvas, tile, x, y)

    _draw_diagonal_watermark(
        canvas,
        f"@{bot_username}",
        tile_px=PREVIEW_TILE_PX,
        gap_px=PREVIEW_GAP_PX,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, format="PNG", optimize=True)


async def build_preview_grid(
    *,
    master_pack_dir: Path,
    font_path: Path,
    nick: str,
    logo_svg_bytes: bytes | None,
    bot_username: str,
    tmp_dir: Path,
    user_id: int,
) -> Path:
    """Build the 3×3 PNG preview and return its path."""
    files = sorted(master_pack_dir.glob("*.tgs"))
    if not files:
        raise RuntimeError(f"master_pack пуст: {master_pack_dir}")

    use_logo = bool(logo_svg_bytes)
    out_path = tmp_dir / f"preview_{user_id}_{secrets.token_hex(4)}.png"

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: _compose_grid_sync(
            files=files,
            nick=nick or "swex",
            use_logo=use_logo,
            logo_svg_bytes=logo_svg_bytes,
            font_path=font_path,
            bot_username=bot_username,
            out_path=out_path,
        ),
    )
    return out_path
