"""Render-based detection of swex-badge canvas position per master tile.

Запусти один раз (или после изменения master_pack):

    python tools/build_master_pack_positions.py

Скрипт:
  1. Для каждой плитки рендерит финальный кадр с swex-буквами и без них.
  2. Считает попиксельный diff → координаты, где буквы реально появлялись.
  3. Берёт центр найденного bbox, маппит обратно в координаты канвы (512×512)
     и сохраняет в master_pack/master_pack_positions.json.

Бот при сборке пака / превью читает этот JSON и кладёт лого ровно туда.
Если плитки нет в манифесте — фоллбек на алгоритмический xform-walker.

Зависимости: rlottie-python (или lottie + cairo), numpy, Pillow.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.swex.replacer import (
    _walk_and_drop_swex_letters,
)
from services.tgs_renderer import get_metadata, render_frames


def _find_master_pack_dir() -> Path:
    """Same location the bot uses (handlers/pack.py)."""
    for candidate in (
        ROOT / "master_pack",  # production layout
        ROOT / "SwexPlus" / "bot" / "master_pack",  # repo layout
    ):
        if candidate.exists() and any(candidate.glob("*.tgs")):
            return candidate
    return ROOT / "master_pack"


MASTER_DIR = _find_master_pack_dir()
OUT_FILE = MASTER_DIR / "master_pack_positions.json"
RENDER_PX = 256  # detector's internal resolution
DIFF_THRESHOLD = 25  # per-pixel |Δ| that counts as "changed"
MIN_PIXELS = 50  # anything below is anti-aliasing noise


def _render_last_frame(tgs_path: Path, size_px: int) -> np.ndarray | None:
    try:
        total, native_fps, _w, _h = get_metadata(tgs_path)
    except Exception:
        return None
    duration = max(0.05, total / max(native_fps, 1.0))
    frames = render_frames(
        tgs_path,
        out_size=size_px,
        fps=4,
        max_seconds=duration,
        workers=1,
        supersample=1.5,
    )
    if not frames:
        return None
    return frames[-1]


def detect_swex_canvas_bounds(tgs_path: Path, canvas_w: float, canvas_h: float):
    """{cx, cy, w, h} of the badge in canvas coordinates, or None.

    w and h let the replacer fit the logo to the actual badge instead of a fixed size.
    """
    original = _render_last_frame(tgs_path, RENDER_PX)
    if original is None:
        return None

    # Same tile with the swex letters stripped, written to a temporary TGS.
    j = json.loads(gzip.decompress(tgs_path.read_bytes()))
    removed = _walk_and_drop_swex_letters(j)
    if removed == 0:
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=".tgs", prefix="detect_")
    os.close(fd)
    tmp = Path(tmp_path)
    try:
        tmp.write_bytes(gzip.compress(json.dumps(j, separators=(",", ":")).encode()))
        no_swex = _render_last_frame(tmp, RENDER_PX)
    finally:
        tmp.unlink(missing_ok=True)
    if no_swex is None:
        return None

    # Diff in RGB only: alpha edges differ even without the swex letters
    # because of compositing.
    a = original.astype(np.int16)
    b = no_swex.astype(np.int16)
    diff_rgb = np.abs(a[..., :3] - b[..., :3]).sum(axis=-1)
    diff_a = np.abs(a[..., 3] - b[..., 3])
    mask = (diff_rgb > DIFF_THRESHOLD) | (diff_a > DIFF_THRESHOLD)
    if int(mask.sum()) < MIN_PIXELS:
        return None

    ys, xs = np.where(mask)
    xmin_r, xmax_r = float(xs.min()), float(xs.max())
    ymin_r, ymax_r = float(ys.min()), float(ys.max())
    cx_render = (xmin_r + xmax_r) / 2.0
    cy_render = (ymin_r + ymax_r) / 2.0
    w_render = max(1.0, xmax_r - xmin_r)
    h_render = max(1.0, ymax_r - ymin_r)

    sx = canvas_w / RENDER_PX
    sy = canvas_h / RENDER_PX
    return {
        "cx": round(cx_render * sx, 1),
        "cy": round(cy_render * sy, 1),
        "w": round(w_render * sx, 1),
        "h": round(h_render * sy, 1),
    }


def main() -> None:
    if not MASTER_DIR.exists():
        print(f"ERROR: {MASTER_DIR} not found")
        sys.exit(1)

    out: dict[str, dict] = {}
    if OUT_FILE.exists():
        try:
            out = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        except Exception:
            out = {}

    files = sorted(MASTER_DIR.glob("*.tgs"))
    print(f"Detecting swex centres in {len(files)} tiles…")

    success = 0
    fail = 0
    for f in files:
        tile_id = f.stem.split("_")[0]
        # Read canvas size for accurate mapping.
        try:
            j = json.loads(gzip.decompress(f.read_bytes()))
            cw = float(j.get("w", 512))
            ch = float(j.get("h", 512))
        except Exception:
            cw, ch = 512.0, 512.0

        try:
            bounds = detect_swex_canvas_bounds(f, cw, ch)
        except Exception as exc:
            print(f"  {f.name}: ERROR {exc}")
            fail += 1
            continue

        if bounds is None:
            print(f"  {f.name}: -")
            fail += 1
            continue

        out[tile_id] = bounds
        success += 1
        print(
            f"  {f.name}: ({bounds['cx']:.0f}, {bounds['cy']:.0f}) "
            f"size={bounds['w']:.0f}x{bounds['h']:.0f}"
        )

    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDone. {success}/{len(files)} detected, {fail} skipped.")
    print(f"Manifest written: {OUT_FILE}")


if __name__ == "__main__":
    main()
