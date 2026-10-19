"""Replaces the 'swex' placeholder in a TGS file with text or an SVG logo.

The four letters are separate shape items named 's', 'w', 'e', 'x' ('e' is a
group of two paths). The replacer finds their common parent and its bbox, then:

- text mode deletes the letters and inserts new glyph shapes in their place, so
  the parent's transform and fill still apply;
- logo mode swaps the letters for the fitted SVG, either inside the same group
  or as a separate shape layer at the placeholder's canvas position for tiles
  whose cascaded precomp animations would distort an in-place logo.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from pathlib import Path

from .glyphs import FontRenderer


# Lazy import to avoid a circular dep at module load time.
def svg_to_lottie_shapes(svg_bytes):
    from services.svg_to_lottie import svg_to_lottie_shapes as _impl

    return _impl(svg_bytes)


LETTER_NAMES = {"s", "w", "e", "x"}


def _shapes_bbox(shapes: list[dict]) -> tuple[float, float, float, float] | None:
    pts: list[tuple[float, float]] = []
    for sh in shapes or []:
        ks = sh.get("ks", {}).get("k") if isinstance(sh, dict) else None
        if not isinstance(ks, dict):
            continue
        for v in ks.get("v", []) or []:
            pts.append((float(v[0]), float(v[1])))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _wrap_in_transform(
    shapes: list[dict],
    *,
    scale: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    rotation_deg: float = 0.0,
) -> dict:
    """Wrap `sh` items in a `gr` with its own `tr`.

    Used for per-emoji overrides from the mini-app (scale, offset and rotation
    relative to the default badge position).
    """
    bbox = _shapes_bbox(shapes)
    anchor = [0.0, 0.0]
    if bbox is not None:
        anchor = [float((bbox[0] + bbox[2]) / 2.0), float((bbox[1] + bbox[3]) / 2.0)]
    return {
        "ty": "gr",
        "nm": "swex_override",
        "it": [
            *shapes,
            {
                "ty": "tr",
                "p": {"a": 0, "k": [float(offset_x), float(offset_y)]},
                "a": {"a": 0, "k": anchor},
                "s": {"a": 0, "k": [100.0 * float(scale), 100.0 * float(scale)]},
                "r": {"a": 0, "k": float(rotation_deg)},
                "o": {"a": 0, "k": 100},
                "sk": {"a": 0, "k": 0},
                "sa": {"a": 0, "k": 0},
            },
        ],
    }


def _has_transform(transform: dict | None) -> bool:
    if not transform:
        return False
    return (
        abs(float(transform.get("scale", 1.0)) - 1.0) > 1e-6
        or abs(float(transform.get("offset_x", 0.0))) > 1e-6
        or abs(float(transform.get("offset_y", 0.0))) > 1e-6
        or abs(float(transform.get("rotation_deg", 0.0))) > 1e-6
    )


def _normalize_hex_color(color: str | None) -> str:
    s = str(color or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", s):
        return "#" + "".join(ch * 2 for ch in s[1:]).lower()
    return "#ffffff"


def _hex_to_lottie_color(color: str | None) -> dict:
    raw = _normalize_hex_color(color)[1:]
    return {
        "a": 0,
        "k": [
            int(raw[0:2], 16) / 255.0,
            int(raw[2:4], 16) / 255.0,
            int(raw[4:6], 16) / 255.0,
            1.0,
        ],
    }


def _clone_fill_stroke(parent_it: list, content_color: str | None) -> list[dict]:
    out: list[dict] = []
    color_prop = _hex_to_lottie_color(content_color)
    has_fill = False
    has_stroke = False
    for item in parent_it:
        if not isinstance(item, dict):
            continue
        ty = item.get("ty")
        if ty == "fl":
            has_fill = True
            cloned = dict(item)
            cloned["c"] = color_prop
            out.append(cloned)
        elif ty == "st":
            has_stroke = True
            cloned = dict(item)
            cloned["c"] = color_prop
            cur_w = cloned.get("w")
            if isinstance(cur_w, dict):
                cur_w = cur_w.get("k")
            try:
                width = float(cur_w)
            except (TypeError, ValueError):
                width = 0.0
            if width < 0.5:
                cloned["w"] = {"a": 0, "k": 2.5}
            if "lc" not in cloned:
                cloned["lc"] = 2
            if "lj" not in cloned:
                cloned["lj"] = 2
            out.append(cloned)
    if not has_fill:
        out.append(
            {
                "ty": "fl",
                "c": color_prop,
                "o": {"a": 0, "k": 100},
                "r": 1,
                "bm": 0,
                "nm": "Fill",
            }
        )
    if not has_stroke:
        out.append(
            {
                "ty": "st",
                "c": color_prop,
                "o": {"a": 0, "k": 100},
                "w": {"a": 0, "k": 2.5},
                "lc": 2,
                "lj": 2,
                "bm": 0,
                "nm": "Stroke",
            }
        )
    return out


def _transform_shapes_direct(
    shapes: list[dict],
    bbox: tuple[float, float, float, float],
    transform: dict | None,
) -> list[dict]:
    if not shapes or not bbox or not _has_transform(transform):
        return shapes
    cx = float((bbox[0] + bbox[2]) / 2.0)
    cy = float((bbox[1] + bbox[3]) / 2.0)
    scale = float((transform or {}).get("scale", 1.0) or 1.0)
    rot = math.radians(float((transform or {}).get("rotation_deg", 0.0) or 0.0))
    offset_x = float((transform or {}).get("offset_x", 0.0) or 0.0)
    offset_y = float((transform or {}).get("offset_y", 0.0) or 0.0)
    cos_r = math.cos(rot)
    sin_r = math.sin(rot)
    out: list[dict] = []
    for sh in shapes:
        if not isinstance(sh, dict):
            out.append(sh)
            continue
        ks = sh.get("ks", {}).get("k")
        if not isinstance(ks, dict):
            out.append(dict(sh))
            continue
        verts = ks.get("v") or []
        ins = ks.get("i") or []
        outs = ks.get("o") or []
        new_v = []
        for v in verts:
            dx = (float(v[0]) - cx) * scale
            dy = (float(v[1]) - cy) * scale
            xr = dx * cos_r - dy * sin_r
            yr = dx * sin_r + dy * cos_r
            new_v.append([round(xr + cx + offset_x, 2), round(yr + cy + offset_y, 2)])

        def _tx_rel(arr):
            res = []
            for pt in arr:
                x = float(pt[0]) * scale
                y = float(pt[1]) * scale
                xr = x * cos_r - y * sin_r
                yr = x * sin_r + y * cos_r
                res.append([round(xr, 2), round(yr, 2)])
            return res

        cloned = dict(sh)
        cloned["ks"] = {
            "a": 0,
            "k": {
                "v": new_v,
                "i": _tx_rel(ins),
                "o": _tx_rel(outs),
                "c": bool(ks.get("c", False)),
            },
        }
        out.append(cloned)
    return out


def _fit_transform_to_canvas(
    transform: dict | None,
    shape_bbox: tuple[float, float, float, float] | None,
    *,
    canvas_w: float,
    canvas_h: float,
    margin: float = 96.0,
) -> dict | None:
    if not transform or not shape_bbox:
        return transform
    scale = float(transform.get("scale", 1.0) or 1.0)
    if scale <= 1.0:
        return transform
    rot = math.radians(float(transform.get("rotation_deg", 0.0) or 0.0))
    w = max(1e-3, float(shape_bbox[2] - shape_bbox[0]))
    h = max(1e-3, float(shape_bbox[3] - shape_bbox[1]))
    cos_r = abs(math.cos(rot))
    sin_r = abs(math.sin(rot))
    rotated_w = w * cos_r + h * sin_r
    rotated_h = w * sin_r + h * cos_r
    max_w = max(1.0, float(canvas_w) + margin * 2.0)
    max_h = max(1.0, float(canvas_h) + margin * 2.0)
    safe_scale = min(scale, max_w / rotated_w, max_h / rotated_h)
    safe_scale = max(0.5, safe_scale)
    if safe_scale >= scale:
        return transform
    fitted = dict(transform)
    fitted["scale"] = safe_scale
    return fitted


# Optional manifest with per-tile logo centres, populated by
# tools/build_master_pack_positions.py — sits next to master_pack/.
_POSITIONS_MANIFEST = "master_pack_positions.json"
_TILE_ID_RE = re.compile(r"^(\d+)")
# Per-dir cache: tile_id -> {"cx", "cy", optional "w", "h"}
_positions_cache: dict[Path, dict[str, dict]] = {}


def _tile_id(tgs_path: Path) -> str | None:
    """File stem prefix ('001', '002', …), as used for manifest keys."""
    m = _TILE_ID_RE.match(tgs_path.stem)
    return m.group(1) if m else None


def _load_positions(master_pack_dir: Path) -> dict[str, dict]:
    """Load the per-tile manifest (cached per directory).

    Fields: cx, cy — badge centre in canvas coordinates (required); w, h — badge
    size for auto-fitting; min_px / max_px — per-tile clamp of the logo's visual
    size (global default 165); pad — breathing room around the logo (default 1.45).
    """
    if master_pack_dir in _positions_cache:
        return _positions_cache[master_pack_dir]
    out: dict[str, dict] = {}
    f = master_pack_dir / _POSITIONS_MANIFEST
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for k, v in data.items():
                if not isinstance(v, dict) or "cx" not in v or "cy" not in v:
                    continue
                entry = {"cx": float(v["cx"]), "cy": float(v["cy"])}
                for opt in ("w", "h", "min_px", "max_px", "pad"):
                    if opt in v:
                        entry[opt] = float(v[opt])
                out[str(k)] = entry
        except Exception:
            pass
    _positions_cache[master_pack_dir] = out
    return out


def _shape_bbox(node: dict) -> tuple[float, float, float, float] | None:
    """Bbox of a 'sh' or 'gr' node by recursively scanning shape vertices.

    Handles BOTH static (ks.a == 0, ks.k is `{v, i, o, c}`) and animated
    (ks.a == 1, ks.k is a list of keyframes) paths — for animated, we read
    the first keyframe's vertex set. Without this, animated stickers like
    the lock / cigar-frog had `_shape_bbox` return None on every letter,
    `find_swex_containers` skipped them, and the runtime never substituted
    the user's nickname.
    """
    out: list[tuple[float, float]] = []

    def collect_verts(ks_k):
        if isinstance(ks_k, dict):
            for v in ks_k.get("v", []) or []:
                if isinstance(v, list) and len(v) >= 2:
                    out.append((v[0], v[1]))
        elif isinstance(ks_k, list) and ks_k:
            # Animated path: take first keyframe's start-state vertices.
            kf = ks_k[0]
            if isinstance(kf, dict):
                s = kf.get("s")
                if isinstance(s, list) and s and isinstance(s[0], dict):
                    for v in s[0].get("v", []) or []:
                        if isinstance(v, list) and len(v) >= 2:
                            out.append((v[0], v[1]))
                elif isinstance(s, dict):
                    for v in s.get("v", []) or []:
                        if isinstance(v, list) and len(v) >= 2:
                            out.append((v[0], v[1]))

    def visit(n):
        if isinstance(n, dict):
            if n.get("ty") == "sh":
                ks = n.get("ks", {})
                if isinstance(ks, dict):
                    collect_verts(ks.get("k"))
            for k, v in n.items():
                if k in ("it", "shapes") and isinstance(v, list):
                    for c in v:
                        visit(c)

    visit(node)
    if not out:
        return None
    xs = [p[0] for p in out]
    ys = [p[1] for p in out]
    return (min(xs), min(ys), max(xs), max(ys))


def find_swex_containers(
    tree: dict,
) -> list[tuple[list, list[int], tuple[float, float, float, float]]]:
    """Locate every parent .it list that holds s,w,e,x sibling items.

    Returns: list of (parent_it_list, indices_of_letter_items, bbox).
    Most emojis have exactly one such container, but we return all.
    """
    found: list[tuple[list, list[int], tuple[float, float, float, float]]] = []

    def visit(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("layers", "assets", "shapes", "it") and isinstance(v, list):
                    indices_by_name: dict[str, int] = {}
                    for i, child in enumerate(v):
                        if isinstance(child, dict):
                            nm = child.get("nm")
                            if nm in LETTER_NAMES and nm not in indices_by_name:
                                indices_by_name[nm] = i
                    if LETTER_NAMES.issubset(indices_by_name.keys()):
                        idxs = sorted(indices_by_name.values())
                        bboxes = [_shape_bbox(v[i]) for i in idxs]
                        bboxes = [b for b in bboxes if b is not None]
                        if bboxes:
                            xmin = min(b[0] for b in bboxes)
                            ymin = min(b[1] for b in bboxes)
                            xmax = max(b[2] for b in bboxes)
                            ymax = max(b[3] for b in bboxes)
                            found.append((v, idxs, (xmin, ymin, xmax, ymax)))
                    for child in v:
                        visit(child)

    visit(tree)
    return found


_SWEX_TEXT_NM_RE = re.compile(r"^swex_text_(.+)$")


def _walk_for_swex_text_groups(tree) -> list[tuple[list, int, dict]]:
    """Return [(parent_list, index, gr_node)] for every gr named swex_text_*."""
    found: list[tuple[list, int, dict]] = []

    def visit(node):
        if isinstance(node, list):
            for c in node:
                visit(c)
            return
        if not isinstance(node, dict):
            return
        for k in ("layers", "assets", "shapes", "it"):
            v = node.get(k)
            if isinstance(v, list):
                for i, child in enumerate(v):
                    if (
                        isinstance(child, dict)
                        and child.get("ty") == "gr"
                        and isinstance(child.get("nm"), str)
                        and _SWEX_TEXT_NM_RE.match(child["nm"])
                    ):
                        found.append((v, i, child))
                for child in v:
                    if isinstance(child, (list, dict)):
                        visit(child)

    visit(tree)
    return found


def _gr_text_bbox(gr: dict) -> tuple[float, float, float, float] | None:
    """Local bbox over all sh vertices inside a gr node."""
    pts: list[tuple[float, float]] = []

    def visit(node):
        if isinstance(node, list):
            for c in node:
                visit(c)
            return
        if not isinstance(node, dict):
            return
        if node.get("ty") == "sh":
            k = node.get("ks", {}).get("k", {})
            if isinstance(k, dict):
                for v in k.get("v", []) or []:
                    if isinstance(v, list) and len(v) >= 2:
                        pts.append((float(v[0]), float(v[1])))
        for kk in ("it", "shapes"):
            vv = node.get(kk)
            if isinstance(vv, list):
                for c in vv:
                    visit(c)

    visit(gr)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _fill_black_item() -> dict:
    return {
        "ty": "fl",
        "c": {"a": 0, "k": [0, 0, 0, 1]},
        "o": {"a": 0, "k": 100},
        "r": 1,
        "bm": 0,
        "nm": "Fill",
    }


def _identity_tr_item() -> dict:
    return {
        "ty": "tr",
        "p": {"a": 0, "k": [0, 0]},
        "a": {"a": 0, "k": [0, 0]},
        "s": {"a": 0, "k": [100, 100]},
        "r": {"a": 0, "k": 0},
        "o": {"a": 0, "k": 100},
        "sk": {"a": 0, "k": 0},
        "sa": {"a": 0, "k": 0},
    }


def _shift_shapes_x(shapes: list[dict], dx: float) -> None:
    """Translate every vertex in every sh by dx along X (in-place)."""
    if abs(dx) < 1e-6:
        return
    for sh in shapes:
        k = sh.get("ks", {}).get("k", {})
        if not isinstance(k, dict):
            continue
        for v in k.get("v", []) or []:
            if isinstance(v, list) and len(v) >= 1:
                v[0] += dx


def _shapes_leftmost_x(shapes: list[dict]) -> float | None:
    out = None
    for sh in shapes:
        k = sh.get("ks", {}).get("k", {})
        if not isinstance(k, dict):
            continue
        for v in k.get("v", []) or []:
            if isinstance(v, list) and len(v) >= 1:
                out = v[0] if out is None else min(out, float(v[0]))
    return out


def _hex_to_lottie_rgba(color: str) -> list[float]:
    s = (color or "").strip()
    import re as _re

    m = _re.match(r"^#([0-9a-fA-F]{6})$", s)
    if not m:
        return [0.0, 0.0, 0.0, 1.0]
    r = int(s[1:3], 16) / 255.0
    g = int(s[3:5], 16) / 255.0
    b = int(s[5:7], 16) / 255.0
    return [r, g, b, 1.0]


def apply_swex_text_lines(
    tree: dict,
    line_map: dict[str, str],
    renderer: FontRenderer,
    line_styles: dict[str, dict] | None = None,
) -> int:
    """Re-render text inside every gr nm='swex_text_<orig>' using
    `line_map[nm] = new_text`. Returns count of groups updated.

    Missing entries / empty strings → group is left as is. To keep the
    passport rows visually consistent: pick stable reference height (median
    of original heights, so the 'y' descender in 'swex city' doesn't push
    that row much taller than the others) and *left-align* every new row
    to a single common X anchor (the leftmost x_min across all original
    rows). Without this, rows of different widths centre-anchor to
    different X positions and the result looks staggered.
    """
    found = _walk_for_swex_text_groups(tree)
    rows: list[tuple[list, int, dict, tuple[float, float, float, float]]] = []
    for parent, idx, gr in found:
        bb = _gr_text_bbox(gr)
        if bb is None:
            continue
        rows.append((parent, idx, gr, bb))
    if not rows:
        return 0
    # Each baked row's local bbox[0] is hand-tuned by the designer to
    # compensate for its layer's X transform — different rows have
    # different local lefts that happen to align visually on canvas.
    # Forcing a shared `common_left` reshuffles them, often dragging
    # shorter texts on top of the photo placeholder. Anchor each row to
    # its OWN bbox[0] instead; the per-line offset_x slider lets the user
    # nudge any single row left/right by hand.
    n = 0
    for parent, idx, gr, bbox in rows:
        nm = gr.get("nm") or ""
        new_text = line_map.get(nm)
        if new_text is None or not str(new_text).strip():
            continue
        cy = (bbox[1] + bbox[3]) / 2.0
        row_left = bbox[0]
        # Reproduce the original render scale.
        m = _SWEX_TEXT_NM_RE.match(nm)
        baked_text = m.group(1) if m else nm
        try:
            layout_baked = renderer.layout(baked_text)
        except Exception:
            layout_baked = None
        baked_h = (
            layout_baked.height
            if (layout_baked and layout_baked.height > 0)
            else float(renderer.x_height)
        )
        baked_scale = (bbox[3] - bbox[1]) / max(1.0, float(baked_h))
        target_h = baked_scale * float(renderer.x_height)
        # Wide horizontal target so renderer uses natural scale (no shrink).
        row_w = 1200.0
        target = (
            -row_w / 2.0,
            cy - target_h / 2.0,
            row_w / 2.0,
            cy + target_h / 2.0,
        )
        try:
            shapes, _ = renderer.render_to_lottie_shapes(str(new_text), target)
        except Exception:
            continue
        if not shapes:
            continue
        # Per-line scale: applied around (row_left, cy) so the leftmost
        # vertex of THIS row stays put.
        style = (line_styles or {}).get(nm) if line_styles else None
        per_scale = float(style.get("scale", 1.0)) if style else 1.0
        per_color = str(style.get("color", "#000000")) if style else "#000000"
        per_ox = float(style.get("offset_x", 0.0)) if style else 0.0
        per_oy = float(style.get("offset_y", 0.0)) if style else 0.0
        cur_left = _shapes_leftmost_x(shapes)
        if cur_left is not None:
            _shift_shapes_x(shapes, row_left - cur_left)
        if abs(per_scale - 1.0) > 1e-6:
            for sh in shapes:
                k = sh.get("ks", {}).get("k", {})
                if not isinstance(k, dict):
                    continue
                for v in k.get("v", []) or []:
                    if isinstance(v, list) and len(v) >= 2:
                        v[0] = row_left + (v[0] - row_left) * per_scale
                        v[1] = cy + (v[1] - cy) * per_scale
                for arr_name in ("i", "o"):
                    for t_ in k.get(arr_name, []) or []:
                        if isinstance(t_, list) and len(t_) >= 2:
                            t_[0] *= per_scale
                            t_[1] *= per_scale
        # Per-line position: translate every vertex (tangents are deltas, so
        # they don't need to move). Lottie uses Y-down, but the user sees a
        # rendered preview — positive Y on the UI slider should push the
        # text downwards on screen, which is +Y in Lottie. Same for X.
        if abs(per_ox) > 1e-6 or abs(per_oy) > 1e-6:
            for sh in shapes:
                k = sh.get("ks", {}).get("k", {})
                if not isinstance(k, dict):
                    continue
                for v in k.get("v", []) or []:
                    if isinstance(v, list) and len(v) >= 2:
                        v[0] += per_ox
                        v[1] += per_oy
        # Pick fill colour: per-line override > default black.
        fill = {
            "ty": "fl",
            "c": {"a": 0, "k": _hex_to_lottie_rgba(per_color)},
            "o": {"a": 0, "k": 100},
            "r": 1,
            "bm": 0,
            "nm": "Fill",
        }
        new_gr = {
            "ty": "gr",
            "nm": nm,
            "it": [*shapes, fill, _identity_tr_item()],
        }
        parent[idx] = new_gr
        n += 1
    return n


def apply_qmark_cleanup(
    tree: dict,
    renderer: FontRenderer,
    photo_svg: bytes | None = None,
    photo_scale: float = 1.0,
) -> int:
    """Replace the baked `swex_qmark` glyph.

    Default: re-render '?' from the font, centred on the photo placeholder.
    If `photo_svg` is provided: parse the SVG and fit it inside the photo
    placeholder instead. The user-supplied logo replaces the question mark
    completely.
    """
    found: list[tuple[list, int, dict]] = []

    def visit(node):
        if isinstance(node, list):
            for c in node:
                visit(c)
            return
        if not isinstance(node, dict):
            return
        for k in ("layers", "assets", "shapes", "it"):
            v = node.get(k)
            if isinstance(v, list):
                for i, child in enumerate(v):
                    if (
                        isinstance(child, dict)
                        and child.get("ty") == "gr"
                        and child.get("nm") == "swex_qmark"
                    ):
                        found.append((v, i, child))
                for c in v:
                    if isinstance(c, (list, dict)):
                        visit(c)

    visit(tree)
    if not found:
        return 0
    # Pre-parse SVG once (used for every qmark group, normally just one).
    svg_shapes: list[dict] | None = None
    svg_bbox: tuple[float, float, float, float] | None = None
    if photo_svg:
        try:
            svg_shapes, svg_bbox = svg_to_lottie_shapes(photo_svg)
        except Exception:
            svg_shapes, svg_bbox = None, None
        if not svg_shapes:
            svg_shapes, svg_bbox = None, None
    n = 0
    for parent, idx, gr in found:
        bb = _gr_text_bbox(gr)
        if bb is None:
            continue
        cx_orig = (bb[0] + bb[2]) / 2.0
        cy_orig = (bb[1] + bb[3]) / 2.0
        if svg_shapes and svg_bbox:
            # Fit the SVG inside the photo placeholder (use original qmark
            # footprint, ~1.0× of its bbox so the logo fills the black box
            # without bleeding past it).
            ps = max(0.3, min(3.0, float(photo_scale) if photo_scale else 1.0))
            size = max(bb[2] - bb[0], bb[3] - bb[1]) * 1.05 * ps
            scaled = _scale_logo_into_local_box(
                svg_shapes,
                svg_bbox,
                center_local=(cx_orig, cy_orig),
                target_local_w=size,
                target_local_h=size,
                fit_ratio=0.9,
            )
            shapes = scaled
            # Keep parent transform, but force a white fill so single-colour
            # SVGs are visible on the black photo placeholder. (If the SVG
            # already has its own fills they'll be honoured later by Lottie
            # rendering; here we add a fallback fill at the end.)
            existing_extras = [
                c for c in gr.get("it", []) if isinstance(c, dict) and c.get("ty") == "tr"
            ]
            if not existing_extras:
                existing_extras = [_identity_tr_item()]
            parent[idx] = {
                "ty": "gr",
                "nm": "swex_qmark",
                "it": [
                    *shapes,
                    {
                        "ty": "fl",
                        "c": {"a": 0, "k": [1, 1, 1, 1]},
                        "o": {"a": 0, "k": 100},
                        "r": 1,
                        "bm": 0,
                        "nm": "Fill",
                    },
                    *existing_extras,
                ],
            }
            n += 1
            continue
        # Default font-rendered '?' path (re-rendered for crispness).
        size = max(bb[2] - bb[0], bb[3] - bb[1]) * 1.35
        target = (
            cx_orig - size / 2.0,
            cy_orig - size / 2.0,
            cx_orig + size / 2.0,
            cy_orig + size / 2.0,
        )
        try:
            shapes, _ = renderer.render_to_lottie_shapes("?", target)
        except Exception:
            continue
        if not shapes:
            continue
        pts: list[tuple[float, float]] = []
        for sh in shapes:
            k = sh.get("ks", {}).get("k", {})
            for v in k.get("v", []) or []:
                pts.append((v[0], v[1]))
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            new_cx = (min(xs) + max(xs)) / 2.0
            new_cy = (min(ys) + max(ys)) / 2.0
            dx = cx_orig - new_cx
            dy = cy_orig - new_cy
            if abs(dx) > 1e-3 or abs(dy) > 1e-3:
                for sh in shapes:
                    k = sh.get("ks", {}).get("k", {})
                    for v in k.get("v", []) or []:
                        v[0] += dx
                        v[1] += dy
        existing_extras = [
            c for c in gr.get("it", []) if isinstance(c, dict) and c.get("ty") in ("fl", "st", "tr")
        ]
        if not any(c.get("ty") == "fl" for c in existing_extras):
            existing_extras.append(
                {
                    "ty": "fl",
                    "c": {"a": 0, "k": [1, 1, 1, 1]},
                    "o": {"a": 0, "k": 100},
                    "r": 1,
                    "bm": 0,
                    "nm": "Fill",
                }
            )
        if not any(c.get("ty") == "tr" for c in existing_extras):
            existing_extras.append(_identity_tr_item())
        parent[idx] = {
            "ty": "gr",
            "nm": "swex_qmark",
            "it": [*shapes, *existing_extras],
        }
        n += 1
    return n


def replace_swex_bytes(
    tgs_bytes: bytes,
    new_text: str,
    renderer: FontRenderer,
    *,
    transform: dict | None = None,
    content_color: str = "#ffffff",
    line_map: dict[str, str] | None = None,
    line_styles: dict[str, dict] | None = None,
    photo_svg: bytes | None = None,
    photo_scale: float = 1.0,
) -> tuple[bytes, dict]:
    """In-memory variant: takes gzipped TGS bytes and a prebuilt FontRenderer,
    so the font is loaded once for the whole pack.

    `transform` is a per-emoji `{scale, offset_x, offset_y, rotation_deg}` override
    in the swex group's local coordinates; None or identity places the text exactly
    where the letters were.
    """
    j = json.loads(gzip.decompress(tgs_bytes))
    containers = find_swex_containers(j)
    total_inserted = 0
    for parent_it, idxs, bbox in containers:
        new_shapes, _scale = renderer.render_to_lottie_shapes(new_text, bbox)
        fitted_transform = _fit_transform_to_canvas(
            transform,
            _shapes_bbox(new_shapes),
            canvas_w=float(j.get("w", 512)),
            canvas_h=float(j.get("h", 512)),
        )
        new_shapes = _transform_shapes_direct(new_shapes, bbox, fitted_transform)
        fill_stroke = _clone_fill_stroke(parent_it, content_color)
        insert_at = min(idxs)
        for i in sorted(idxs, reverse=True):
            del parent_it[i]
        for offset, sh in enumerate(new_shapes):
            parent_it.insert(insert_at + offset, sh)
        for offset, it in enumerate(fill_stroke):
            parent_it.insert(insert_at + len(new_shapes) + offset, it)
        total_inserted += len(new_shapes)
    lines_updated = 0
    if line_map:
        lines_updated = apply_swex_text_lines(j, line_map, renderer, line_styles)
    qmark_updated = apply_qmark_cleanup(j, renderer, photo_svg=photo_svg, photo_scale=photo_scale)
    out_bytes = gzip.compress(json.dumps(j, separators=(",", ":")).encode())
    return out_bytes, {
        "containers_found": len(containers),
        "glyphs_inserted": total_inserted,
        "lines_updated": lines_updated,
        "qmark_updated": qmark_updated,
        "new_text": new_text,
        "size": len(out_bytes),
    }


def replace_swex(
    tgs_path: Path | str,
    new_text: str,
    font_path: Path | str,
    out_path: Path | str,
) -> dict:
    """File→file convenience wrapper around `replace_swex_bytes`."""
    in_bytes = Path(tgs_path).read_bytes()
    renderer = FontRenderer(font_path)
    out_bytes, stats = replace_swex_bytes(in_bytes, new_text, renderer)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)
    return stats


def _static_scale(prop) -> tuple[float, float]:
    """Representative (sx, sy) of a Lottie scale property.

    For animated properties this is the keyframe with the largest |sx| + |sy|:
    many master tiles slide in through precomp refs starting at scales like
    [4, 46.2], so the first keyframe isn't representative.
    """
    if not isinstance(prop, dict):
        return (1.0, 1.0)
    if prop.get("a") == 0:
        s = prop.get("k")
        if isinstance(s, list) and len(s) >= 2:
            return (float(s[0]) / 100.0, float(s[1]) / 100.0)
        return (1.0, 1.0)
    k = prop.get("k")
    if not isinstance(k, list) or not k:
        return (1.0, 1.0)
    best: tuple[float, float] | None = None
    best_mag = -1.0
    for kf in k:
        if not isinstance(kf, dict):
            continue
        s = kf.get("s")
        if isinstance(s, list) and len(s) >= 2:
            sx, sy = float(s[0]) / 100.0, float(s[1]) / 100.0
            mag = abs(sx) + abs(sy)
            if mag > best_mag:
                best_mag = mag
                best = (sx, sy)
    return best if best is not None else (1.0, 1.0)


def _static_pos(prop) -> tuple[float, float]:
    """(x, y) of a Lottie position/anchor: the static value or the keyframe mean."""
    if not isinstance(prop, dict):
        return (0.0, 0.0)
    if prop.get("a") == 0:
        s = prop.get("k")
        if isinstance(s, list) and len(s) >= 2:
            return (float(s[0]), float(s[1]))
        return (0.0, 0.0)
    k = prop.get("k")
    if not isinstance(k, list) or not k:
        return (0.0, 0.0)
    xs: list[float] = []
    ys: list[float] = []
    for kf in k:
        if not isinstance(kf, dict):
            continue
        s = kf.get("s")
        if isinstance(s, list) and len(s) >= 2:
            xs.append(float(s[0]))
            ys.append(float(s[1]))
    if not xs:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _apply_xform(ks_or_tr, sx, sy, ox, oy):
    """Apply Lottie ks/tr transform on top of accumulated affine.

    Treats rotation/skew as identity (good enough for our static logo
    placement — master_pack tiles don't rotate the swex container).
    Returns (new_sx, new_sy, new_ox, new_oy) such that:
        new_canvas = new_ox + new_sx * local_x, similarly for y.
    """
    if not isinstance(ks_or_tr, dict):
        return (sx, sy, ox, oy)
    lsx, lsy = _static_scale(ks_or_tr.get("s", {}))
    lpx, lpy = _static_pos(ks_or_tr.get("p", {}))
    lax, lay = _static_pos(ks_or_tr.get("a", {}))
    new_sx = sx * lsx
    new_sy = sy * lsy
    new_ox = ox + sx * (lpx - lsx * lax)
    new_oy = oy + sy * (lpy - lsy * lay)
    return (new_sx, new_sy, new_ox, new_oy)


def _swex_canvas_xform_candidates(
    tree: dict,
) -> list[tuple[float, float, float, float]]:
    """Walk Lottie tree (resolving asset refIds *and* layer parenting) and
    return every (sx, sy, ox, oy) under which a swex letter is reached.

    Tile #108-style designs use 5+ precomp refs to the same swex asset, each
    at a different animated scale; this returns every visible occurrence so
    the caller can pick the most useful one (or average them).
    """
    assets: dict = {}
    for asset in tree.get("assets") or []:
        if isinstance(asset, dict) and "id" in asset:
            assets[asset["id"]] = asset

    found: list[tuple[float, float, float, float]] = []

    def walk_shape(shape, sx, sy, ox, oy, depth=0):
        if depth > 24 or not isinstance(shape, dict):
            return
        nm = shape.get("nm", "")
        ty = shape.get("ty")
        if nm in LETTER_NAMES:
            found.append((sx, sy, ox, oy))
            return
        if ty == "gr":
            it = shape.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            csx, csy, cox, coy = sx, sy, ox, oy
            if tr is not None:
                csx, csy, cox, coy = _apply_xform(tr, sx, sy, ox, oy)
            for c in it:
                if c is tr:
                    continue
                walk_shape(c, csx, csy, cox, coy, depth + 1)

    def walk_composition(layers_list, outer_xform, depth=0):
        if depth > 24 or not layers_list:
            return
        by_ind: dict = {}
        for L in layers_list:
            if isinstance(L, dict) and L.get("ind") is not None:
                by_ind[L["ind"]] = L

        cache: dict = {}
        resolving: set = set()

        def resolve(layer):
            ind = layer.get("ind")
            if ind in cache:
                return cache[ind]
            if ind in resolving:
                return outer_xform
            resolving.add(ind)
            parent_ind = layer.get("parent")
            if parent_ind is not None and parent_ind in by_ind:
                parent_xform = resolve(by_ind[parent_ind])
            else:
                parent_xform = outer_xform
            my_xform = _apply_xform(layer.get("ks"), *parent_xform)
            resolving.discard(ind)
            cache[ind] = my_xform
            return my_xform

        for L in layers_list:
            if not isinstance(L, dict):
                continue
            layer_xform = resolve(L)
            ty = L.get("ty")
            if ty == 0:  # precomp reference
                asset = assets.get(L.get("refId"))
                if asset:
                    walk_composition(asset.get("layers") or [], layer_xform, depth + 1)
            elif ty == 4:  # shape layer
                for shape in L.get("shapes") or []:
                    walk_shape(shape, *layer_xform)

    walk_composition(tree.get("layers") or [], (1.0, 1.0, 0.0, 0.0))
    return found


def _swex_canvas_xform(tree: dict):
    """Backwards-compatible single-best xform. Picks the candidate with the
    largest |sx|+|sy| (the version of swex that reaches full size at its
    most rest-state keyframe)."""
    cands = _swex_canvas_xform_candidates(tree)
    if not cands:
        return None
    return max(cands, key=lambda x: abs(x[0]) + abs(x[1]))


def _walk_and_drop_swex_letters(tree: dict) -> int:
    """Remove the s/w/e/x shapes from every parent list they were found in."""
    containers = find_swex_containers(tree)
    removed = 0
    for parent_it, idxs, _ in containers:
        for i in sorted(idxs, reverse=True):
            del parent_it[i]
            removed += 1
    return removed


def _scale_logo_into_local_box(
    logo_shapes: list[dict],
    logo_bbox: tuple[float, float, float, float],
    *,
    center_local: tuple[float, float],
    target_local_w: float,
    target_local_h: float,
    fit_ratio: float = 0.92,
) -> list[dict]:
    """Scale and centre the logo in the group's local coordinates.

    The resulting shapes go straight into parent_it and inherit its fill and
    transform, so the logo reads as part of the badge rather than a sticker on top.
    """
    lxmin, lymin, lxmax, lymax = logo_bbox
    lw = max(1e-3, lxmax - lxmin)
    lh = max(1e-3, lymax - lymin)

    sx = (target_local_w * fit_ratio) / lw
    sy = (target_local_h * fit_ratio) / lh
    s = min(sx, sy)

    cx, cy = center_local
    final_w = lw * s
    final_h = lh * s
    offset_x = cx - final_w / 2.0 - lxmin * s
    offset_y = cy - final_h / 2.0 - lymin * s

    R = 2
    out: list[dict] = []
    for sh in logo_shapes:
        k = sh["ks"]["k"]
        new_v = [[round(v[0] * s + offset_x, R), round(v[1] * s + offset_y, R)] for v in k["v"]]
        new_i = [[round(t[0] * s, R), round(t[1] * s, R)] for t in k["i"]]
        new_o = [[round(t[0] * s, R), round(t[1] * s, R)] for t in k["o"]]
        out.append(
            {
                "ty": "sh",
                "ks": {
                    "a": 0,
                    "k": {
                        "v": new_v,
                        "i": new_i,
                        "o": new_o,
                        "c": k.get("c", False),
                    },
                },
            }
        )
    return out


def _build_logo_layer(
    logo_shapes: list[dict],
    logo_bbox: tuple[float, float, float, float],
    *,
    canvas_w: float,
    canvas_h: float,
    target_px: float,
    fit_ratio: float,
    composition_op: float = 180.0,
    center: tuple[float, float] | None = None,
    target_w_px: float | None = None,
    target_h_px: float | None = None,
) -> dict:
    """Build a single shape layer holding the logo with a white fill.

    The logo is fitted, preserving aspect ratio, into target_w_px × target_h_px
    when the manifest provides the badge size, or into a target_px square otherwise.
    """
    lxmin, lymin, lxmax, lymax = logo_bbox
    lw = max(1e-3, lxmax - lxmin)
    lh = max(1e-3, lymax - lymin)
    cx_local = (lxmin + lxmax) / 2.0
    cy_local = (lymin + lymax) / 2.0

    box_w = target_w_px if target_w_px is not None else target_px
    box_h = target_h_px if target_h_px is not None else target_px
    sx = (box_w * fit_ratio) / lw
    sy = (box_h * fit_ratio) / lh
    s = min(sx, sy)

    R = 2
    centred_shapes: list[dict] = []
    for sh in logo_shapes:
        k = sh["ks"]["k"]
        new_v = [[round((v[0] - cx_local) * s, R), round((v[1] - cy_local) * s, R)] for v in k["v"]]
        new_i = [[round(t[0] * s, R), round(t[1] * s, R)] for t in k["i"]]
        new_o = [[round(t[0] * s, R), round(t[1] * s, R)] for t in k["o"]]
        centred_shapes.append(
            {
                "ty": "sh",
                "ks": {
                    "a": 0,
                    "k": {
                        "v": new_v,
                        "i": new_i,
                        "o": new_o,
                        "c": k.get("c", False),
                    },
                },
            }
        )

    cx_canvas, cy_canvas = center if center is not None else (canvas_w / 2.0, canvas_h / 2.0)
    return {
        "ddd": 0,
        "ind": 999999,
        "ty": 4,  # shape layer
        "nm": "logo",
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [cx_canvas, cy_canvas, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "ao": 0,
        "shapes": [
            {
                "ty": "gr",
                "it": [
                    *centred_shapes,
                    {
                        "ty": "fl",
                        "c": {"a": 0, "k": [1, 1, 1, 1]},  # white
                        "o": {"a": 0, "k": 100},
                        "r": 1,
                        "bm": 0,
                    },
                    {
                        "ty": "tr",
                        "p": {"a": 0, "k": [0, 0]},
                        "a": {"a": 0, "k": [0, 0]},
                        "s": {"a": 0, "k": [100, 100]},
                        "r": {"a": 0, "k": 0},
                        "o": {"a": 0, "k": 100},
                    },
                ],
            }
        ],
        "ip": 0,
        "op": composition_op,  # visible for the whole composition
        "st": 0,
        "bm": 0,
    }


def _resolve_logo_canvas_center(
    tree: dict,
    canvas_w: float,
    canvas_h: float,
    target_px: float,
    margin: float = 8.0,
) -> tuple[float, float]:
    """Fallback logo centre on the canvas.

    Averages the canvas centres of the swex groups that fall inside the safe zone;
    with none, uses the canvas centre. The result is clamped so the logo stays
    inside the safe zone.
    """
    cs = find_swex_containers(tree)
    cands = _swex_canvas_xform_candidates(tree)
    cw_half = canvas_w / 2.0
    ch_half = canvas_h / 2.0
    half_target = target_px / 2.0
    safe_xmin = half_target + margin
    safe_xmax = canvas_w - half_target - margin
    safe_ymin = half_target + margin
    safe_ymax = canvas_h - half_target - margin

    centers: list[tuple[float, float]] = []
    for sx, sy, ox, oy in cands:
        if not cs:
            continue
        bb = cs[0][2]
        cx_loc = (bb[0] + bb[2]) / 2.0
        cy_loc = (bb[1] + bb[3]) / 2.0
        cx_can = ox + sx * cx_loc
        cy_can = oy + sy * cy_loc
        if abs(sx) < 0.2 or abs(sy) < 0.2:
            continue
        if (
            cx_can < -canvas_w * 0.2
            or cx_can > canvas_w * 1.2
            or cy_can < -canvas_h * 0.2
            or cy_can > canvas_h * 1.2
        ):
            continue
        centers.append((cx_can, cy_can))

    if centers:
        avg_x = sum(c[0] for c in centers) / len(centers)
        avg_y = sum(c[1] for c in centers) / len(centers)
    else:
        avg_x, avg_y = cw_half, ch_half

    avg_x = cw_half if safe_xmax < safe_xmin else max(safe_xmin, min(safe_xmax, avg_x))
    avg_y = ch_half if safe_ymax < safe_ymin else max(safe_ymin, min(safe_ymax, avg_y))
    return (avg_x, avg_y)


def replace_swex_with_logo_bytes(
    tgs_bytes: bytes,
    logo_shapes: list[dict],
    logo_bbox: tuple[float, float, float, float],
    *,
    fit_ratio: float = 0.92,
    canvas_target_px: float = 260.0,  # legacy / unused (kept for compat)
    override_center: object | None = None,  # accepted but ignored (legacy)
    override_size: object | None = None,
    override_min_px: float | None = None,
    override_max_px: float | None = None,
    override_pad: float | None = None,
    fallback_target_scale: float = 1.45,
    size_pad: float = 1.45,
    min_visual_px: float = 165.0,  # minimum logo side on the canvas
    max_visual_px: float = 320.0,
    transform: dict | None = None,  # per-emoji {scale,offset_x,offset_y,rotation_deg}
    content_color: str = "#ffffff",
    line_map: dict[str, str] | None = None,
    line_renderer: FontRenderer | None = None,
    line_styles: dict[str, dict] | None = None,
    photo_svg: bytes | None = None,
    photo_scale: float = 1.0,
    letter_expand: float = 1.0,
) -> tuple[bytes, dict]:
    """Replace the swex letters with the user's logo inside their own group.

    Inserting into `parent_it` instead of a top-level layer means the logo inherits
    the group's fill, transform and animation.

    Size: `override_size` is (w, h) in canvas pixels from the manifest, projected
    back into local coordinates through the parent scale, plus `size_pad`.
    Otherwise `fallback_target_scale` × the letters' bbox.

    Position is the centre of the letters' local bbox. `override_center` is
    accepted for pack_builder compatibility but unused: for in-place insertion the
    parent group already determines the position.
    """
    j = json.loads(gzip.decompress(tgs_bytes))
    containers = find_swex_containers(j)

    total_inserted = 0
    for parent_it, idxs, letter_bbox in containers:
        lxmin, lymin, lxmax, lymax = letter_bbox
        cx_local = (lxmin + lxmax) / 2.0
        cy_local = (lymin + lymax) / 2.0
        letter_w = max(1e-3, lxmax - lxmin) * letter_expand
        letter_h = max(1e-3, lymax - lymin) * letter_expand

        scaled = _scale_logo_into_local_box(
            logo_shapes,
            logo_bbox,
            center_local=(cx_local, cy_local),
            target_local_w=letter_w,
            target_local_h=letter_h,
            fit_ratio=1.0,
        )
        fitted_transform = _fit_transform_to_canvas(
            transform,
            _shapes_bbox(scaled),
            canvas_w=float(j.get("w", 512)),
            canvas_h=float(j.get("h", 512)),
        )
        scaled = _transform_shapes_direct(scaled, letter_bbox, fitted_transform)
        fill_stroke = _clone_fill_stroke(parent_it, content_color)

        insert_at = min(idxs)
        for i in sorted(idxs, reverse=True):
            del parent_it[i]
        for offset, sh in enumerate(scaled):
            parent_it.insert(insert_at + offset, sh)
        for offset, it in enumerate(fill_stroke):
            parent_it.insert(insert_at + len(scaled) + offset, it)
        total_inserted += len(scaled)

    lines_updated = 0
    qmark_updated = 0
    if line_renderer is not None:
        if line_map:
            lines_updated = apply_swex_text_lines(
                j,
                line_map,
                line_renderer,
                line_styles,
            )
        qmark_updated = apply_qmark_cleanup(
            j,
            line_renderer,
            photo_svg=photo_svg,
            photo_scale=photo_scale,
        )
    out_bytes = gzip.compress(json.dumps(j, separators=(",", ":")).encode())
    return out_bytes, {
        "containers_found": len(containers),
        "shapes_inserted": total_inserted,
        "lines_updated": lines_updated,
        "qmark_updated": qmark_updated,
        "canvas": (float(j.get("w", 512)), float(j.get("h", 512))),
        "size": len(out_bytes),
    }
