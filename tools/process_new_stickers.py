"""Re-process new_stickers/ into emoji_stickers_swex/ using the curated
text-bbox manifest (tools/text_bbox_final.json).

For each sticker:
  • placement entry -> delete the placeholder letter-shapes inside the bbox,
    then insert ONE clean swex container. Prefer in-place insertion into the
    parent list where the deletions happened (inherits the original layer's
    transform / animation); fall back to a top-level overlay if no parent was
    found. Colour comes from the manifest.
  • skip entry -> no designer placeholder; add a small bottom-centre fallback
    swex overlay so the runtime can still personalise the tile.

This path NEVER renames existing shapes (the cause of the previous
'Group has no to_matrix' corruption) — every swex we add is a freshly-built,
well-formed group.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.swex.glyphs import FontRenderer
from tools.swex_new_stickers import (
    _add_overlay_with_plate,
    _delete_shapes_in_canvas_bbox,
    find_all_text_runs,
)


def _structural_delete_text_runs(tree: dict) -> int:
    """Delete every letter of every detected text run (the baked
    'TEXT'/'ангельский' placeholder and any duplicate baked in another layer).

    This is structural — it operates on the JSON shape tree, not on computed
    canvas positions — so it works even on heavily-animated layers where the
    canvas-bbox estimate is unreliable. Returns the count of shapes removed.
    """
    runs = find_all_text_runs(tree)
    by_parent: dict[int, tuple] = {}
    for _score, parent, idxs, _bb in runs:
        cur = by_parent.setdefault(id(parent), (parent, set()))
        cur[1].update(idxs)
    removed = 0
    for parent, idxset in by_parent.values():
        for i in sorted(idxset, reverse=True):
            if 0 <= i < len(parent):
                del parent[i]
                removed += 1
    return removed


SRC_DIR = ROOT / "new_stickers"
OUT_DIR = ROOT / "emoji_stickers_swex"
FONT_PATH = ROOT / "fonts" / "SeymourOne-Regular.ttf"
MANIFEST = ROOT / "tools" / "text_bbox_final.json"


def _hex_rgb(h: str) -> tuple[float, float, float]:
    s = h.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


def _load(p: Path) -> dict:
    return json.loads(gzip.decompress(p.read_bytes()))


def _save(p: Path, tree: dict) -> None:
    p.write_bytes(gzip.compress(json.dumps(tree, separators=(",", ":")).encode()))


def process_one(sid: str, entry: dict, renderer: FontRenderer) -> dict:
    tree = _load(SRC_DIR / f"{sid}.tgs")
    info = {"id": sid}

    if entry.get("skip"):
        # No placeholder — drop a small bottom-centre swex so runtime can
        # still substitute the user's nick.
        cw = float(tree.get("w", 512))
        ch = float(tree.get("h", 512))
        cx, cy = cw / 2.0, ch * 0.86
        w, h = cw * 0.34, ch * 0.11
        _add_overlay_with_plate(
            tree,
            renderer,
            cx,
            cy,
            w,
            h,
            plate=False,
            text_color=(1.0, 1.0, 1.0),
        )
        info["mode"] = "skip_fallback"
        _save(OUT_DIR / f"{sid}.tgs", tree)
        return info

    cx, cy = float(entry["cx"]), float(entry["cy"])
    w, h = float(entry["w"]), float(entry["h"])
    color = _hex_rgb(entry.get("color", "#ffffff"))

    # Pad the bbox generously so we catch the WHOLE placeholder word. The
    # detector's bbox hugs only the glyphs it found; trailing letters
    # ("X", "T" of TEXT) often sit just outside it and survived the earlier
    # narrow delete. Wider pad + lower overlap threshold sweeps them out.
    # The size guards inside _delete_shapes_in_canvas_bbox still protect the
    # big background plates/banners from deletion.
    pad_w = w * 0.42 + 10
    pad_h = h * 0.45 + 8
    target = (cx - w / 2 - pad_w, cy - h / 2 - pad_h, cx + w / 2 + pad_w, cy + h / 2 + pad_h)

    # Structural removal of the baked placeholder letters — reliable even
    # on animated layers (works on the shape tree, not canvas estimates).
    info["struct_del"] = _structural_delete_text_runs(tree)
    # Canvas-bbox sweep as a backstop for any stray glyph the structural
    # pass missed (e.g. a non-letter decoration overlapping the word).
    info["bbox_del"] = _delete_shapes_in_canvas_bbox(
        tree,
        target,
        overlap_threshold=0.14,
    )

    # Always place a clean top-level overlay at the canvas position. This is
    # predictable and always visible — in-place insertion proved unstable on
    # layers with extreme / animated parent transforms (the swex drifted
    # off-canvas or rendered sub-pixel). The text is static rather than
    # riding the original animation, which the user accepted as the
    # reliability trade-off.
    _add_overlay_with_plate(
        tree,
        renderer,
        cx,
        cy,
        max(w, 90),
        max(h, 34),
        plate=False,
        text_color=color,
    )
    info["mode"] = "overlay"

    _save(OUT_DIR / f"{sid}.tgs", tree)
    return info


def main():
    OUT_DIR.mkdir(exist_ok=True)
    renderer = FontRenderer(FONT_PATH)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ids = sorted(p.stem for p in SRC_DIR.glob("*.tgs"))
    counts = {}
    for sid in ids:
        entry = manifest.get(sid, {"skip": True})
        try:
            info = process_one(sid, entry, renderer)
            counts[info.get("mode", "?")] = counts.get(info.get("mode", "?"), 0) + 1
        except Exception as e:
            import traceback

            print(f"FAIL {sid}: {e}")
            traceback.print_exc()
    print("Done.", counts)


if __name__ == "__main__":
    main()
