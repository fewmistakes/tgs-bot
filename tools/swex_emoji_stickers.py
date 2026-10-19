"""Process emoji_stickers/*.tgs: replace the W-logo (and special texts) with `swex`.

Pipeline:
  1. Detect the swex W-logo by shape signature (a sh with 15 verts and ~149×136 bbox).
  2. Walk up to the wrapper gr whose `it` list holds the 5 components forming the W;
     replace those 5 components with rendered `swex` text glyphs (1.4× the W bbox).
  3. Special-case stickers:
       - 5805268833123965409.tgs  passport: small W → swex; four text rows → SWEX /
         @swex / swex.tg / swex; black photo rectangle gains a white `?` overlay.
       - 5807538238008597691.tgs  VELOR wordmark: glyph row → swex.

Outputs:
  emoji_stickers_swex/*.tgs           — modified stickers
  emoji_stickers_swex_preview/*.png   — middle-frame raster previews
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.swex.glyphs import FontRenderer

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "emoji_stickers"
OUT_DIR = ROOT / "emoji_stickers_swex"
PREVIEW_DIR = ROOT / "emoji_stickers_swex_preview"
FONT_PATH = ROOT / "fonts" / "SeymourOne-Regular.ttf"

PASSPORT_ID = "5805268833123965409"
VELOR_ID = "5807538238008597691"

# W-logo signature: a sh node with 15 verts and ~149×136 px local bbox.
W_VERTS = 15
W_BBOX_W = (140, 160)
W_BBOX_H = (130, 145)

# 1.0× = exactly the W silhouette's bbox. The W is a tall mark (148×135), so
# rendering "swex" at the same height already produces readable text without
# spilling past where the original W lived.
TEXT_EXPAND = 1.0


def _load(p: Path) -> dict:
    return json.loads(gzip.decompress(p.read_bytes()))


def _save(p: Path, tree: dict) -> None:
    p.write_bytes(gzip.compress(json.dumps(tree, separators=(",", ":")).encode()))


def _static_rotation_from_tr(tr: dict) -> float:
    """Read a static (or first-keyframe) rotation in degrees from a tr/ks block."""
    if not isinstance(tr, dict):
        return 0.0
    r = tr.get("r", {})
    if not isinstance(r, dict):
        return 0.0
    k = r.get("k")
    if r.get("a") == 0 and isinstance(k, (int, float)):
        return float(k)
    if isinstance(k, list) and k and isinstance(k[0], dict):
        s = k[0].get("s")
        if isinstance(s, list) and s:
            return float(s[0])
    return 0.0


def _static_scale_from_tr(tr: dict) -> tuple[float, float]:
    if not isinstance(tr, dict):
        return (1.0, 1.0)
    s = tr.get("s", {})
    if not isinstance(s, dict):
        return (1.0, 1.0)
    k = s.get("k")
    if s.get("a") == 0 and isinstance(k, list) and len(k) >= 2 and not isinstance(k[0], dict):
        return (float(k[0]) / 100.0, float(k[1]) / 100.0)
    if isinstance(k, list) and k and isinstance(k[0], dict):
        sv = k[0].get("s")
        if isinstance(sv, list) and len(sv) >= 2:
            return (float(sv[0]) / 100.0, float(sv[1]) / 100.0)
    return (1.0, 1.0)


def _cumulative_rotation(tree: dict, path: tuple) -> float:
    """Sum rotations of every ancestor tr/ks along the path to `path`.

    Walks from root down, accumulating the rotation of each layer's `ks` and
    each gr's inline `tr`. Used to counter-rotate the swex text so it reads
    upright regardless of how the designer tilted the original mark.
    """
    total = 0.0
    node = tree
    it = iter(path)
    for k in it:
        i = next(it)
        if k == "layers" or k == "assets":
            pass  # the list itself has no transform
        node = node[k][i]
        if not isinstance(node, dict):
            continue
        if "ks" in node and isinstance(node["ks"], dict):
            total += _static_rotation_from_tr(node["ks"])
        if node.get("ty") == "gr":
            for child in node.get("it") or []:
                if isinstance(child, dict) and child.get("ty") == "tr":
                    total += _static_rotation_from_tr(child)
                    break
    return total


def _cumulative_scale_signs(tree: dict, path: tuple) -> tuple[int, int]:
    """Return (sign_x, sign_y) — +1 or -1 — for the cumulative parent scale.

    Designers occasionally use scale.x = -1 or -2 (or scale.y) to flip the
    artwork on a tilted surface (e.g. the ring's `s = [-2, 1.999]`). The W is
    near-symmetric so a flip is invisible; but "swex" reads as mirror text.
    """
    sx_sign, sy_sign = 1, 1
    node = tree
    it = iter(path)
    for k in it:
        i = next(it)
        node = node[k][i]
        if not isinstance(node, dict):
            continue
        if "ks" in node and isinstance(node["ks"], dict):
            sx, sy = _static_scale_from_tr(node["ks"])
            if sx < 0:
                sx_sign = -sx_sign
            if sy < 0:
                sy_sign = -sy_sign
        if node.get("ty") == "gr":
            for child in node.get("it") or []:
                if isinstance(child, dict) and child.get("ty") == "tr":
                    sx, sy = _static_scale_from_tr(child)
                    if sx < 0:
                        sx_sign = -sx_sign
                    if sy < 0:
                        sy_sign = -sy_sign
                    break
    return sx_sign, sy_sign


def _walk_paths(tree: dict, predicate):
    """Yield path tuples to every node where predicate(node) is True.

    A path is a tuple alternating ('key', index) so `tree[key][i]` walks the
    tree. We walk into the standard Lottie list-bearing keys.
    """
    LIST_KEYS = ("it", "shapes", "layers", "assets")
    stack = [((), tree)]
    while stack:
        path, node = stack.pop()
        if not isinstance(node, dict):
            continue
        if predicate(node):
            yield path
        for k in LIST_KEYS:
            v = node.get(k)
            if isinstance(v, list):
                for i, c in enumerate(v):
                    stack.append(((*path, k, i), c))


def _node_at(tree: dict, path: tuple):
    n = tree
    it = iter(path)
    for k in it:
        i = next(it)
        n = n[k][i]
    return n


def _parent_list_at(tree: dict, path: tuple):
    """Return the *list* containing the node addressed by `path`, plus the
    index inside that list.  E.g. for path (..., 'it', 4) returns
    (the_it_list, 4)."""
    if not path:
        raise ValueError("root has no parent list")
    key = path[-2]
    idx = path[-1]
    parent_node = _node_at(tree, path[:-2])
    return parent_node[key], idx


def _bbox_of_shape_vertices(node: dict):
    """Walk a node, collect every sh's vertex list, return union bbox.

    Applies any `tr` (transform) found alongside the shapes to the vertices
    so the bbox reflects the actual visual layout. Without this, components
    that draw their sh at (0, 0) but place it via `tr.p = [X, Y]` would all
    overlap at the origin in the union bbox.
    """
    pts: list[tuple[float, float]] = []

    def visit(n, offset_x=0.0, offset_y=0.0, scale=1.0, depth=0):
        if depth > 30 or not isinstance(n, dict):
            return
        if n.get("ty") == "sh":
            ks = n.get("ks", {}).get("k", {})
            if isinstance(ks, dict):
                for v in ks.get("v") or []:
                    pts.append((offset_x + scale * v[0], offset_y + scale * v[1]))
            return
        if n.get("ty") == "gr":
            # find this group's own tr
            it = n.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            ox, oy, sc = offset_x, offset_y, scale
            if tr is not None:
                tp = tr.get("p", {}).get("k")
                if isinstance(tp, list) and len(tp) >= 2 and not isinstance(tp[0], dict):
                    ox = offset_x + scale * float(tp[0])
                    oy = offset_y + scale * float(tp[1])
                ts = tr.get("s", {}).get("k")
                if isinstance(ts, list) and len(ts) >= 2 and not isinstance(ts[0], dict):
                    # rough — assumes uniform scale, fine for the W components
                    sc = scale * float(ts[0]) / 100.0
            for c in it:
                if c is tr:
                    continue
                visit(c, ox, oy, sc, depth + 1)
            return
        for k in ("it", "shapes"):
            for c in n.get(k) or []:
                visit(c, offset_x, offset_y, scale, depth + 1)

    visit(node)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _is_w_sh(node: dict) -> bool:
    if not isinstance(node, dict) or node.get("ty") != "sh":
        return False
    ks = node.get("ks", {}).get("k", {})
    if not isinstance(ks, dict):
        return False
    v = ks.get("v") or []
    if len(v) != W_VERTS:
        return False
    xs = [p[0] for p in v]
    ys = [p[1] for p in v]
    bw = max(xs) - min(xs)
    bh = max(ys) - min(ys)
    return W_BBOX_W[0] < bw < W_BBOX_W[1] and W_BBOX_H[0] < bh < W_BBOX_H[1]


def _render_swex_shapes(
    renderer: FontRenderer, text: str, target_bbox, expand: float = TEXT_EXPAND
):
    """Render `text` as Lottie sh shapes, expanded to `expand`× the target bbox.

    Centred on the original bbox centre so the new text occupies (roughly) the
    same visual real-estate as the artwork it replaces.
    """
    x0, y0, x1, y1 = target_bbox
    w = x1 - x0
    h = y1 - y0
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    nw = w * expand
    nh = h * expand
    expanded = (cx - nw / 2.0, cy - nh / 2.0, cx + nw / 2.0, cy + nh / 2.0)
    shapes, _ = renderer.render_to_lottie_shapes(text, expanded)
    return shapes


def _render_swex_named_letters(renderer: FontRenderer, target_bbox):
    """Render "swex" as 4 sibling items each named after its letter.

    Mirrors the master_pack convention the webapp's replacer.js expects:
      - 's' → single sh (1 subpath)
      - 'w' → single sh (1 subpath)
      - 'e' → gr containing 2 sh (outer + counter)
      - 'x' → single sh (1 subpath)

    This way the existing webapp flow (findSwexContainers + renderTextToShapes)
    can locate and replace the letters with the user's nickname/logo without
    any sticker-specific logic.
    """

    layout = renderer.layout("swex")
    if layout.width <= 0 or not layout.glyphs:
        return []

    x0, y0, x1, y1 = target_bbox
    tw = x1 - x0
    th = y1 - y0

    natural_scale = th / renderer.x_height
    natural_width = layout.width * natural_scale
    scale = (tw / layout.width) if natural_width > tw else natural_scale

    final_w = layout.width * scale
    off_x = x0 + (tw - final_w) / 2.0
    off_y = y1

    R = 2

    def make_sh(subpath):
        new_v = [
            [round(v[0] * scale + off_x, R), round(v[1] * scale + off_y, R)] for v in subpath["v"]
        ]
        new_i = [[round(t[0] * scale, R), round(t[1] * scale, R)] for t in subpath["i"]]
        new_o = [[round(t[0] * scale, R), round(t[1] * scale, R)] for t in subpath["o"]]
        return {
            "ty": "sh",
            "ks": {"a": 0, "k": {"v": new_v, "i": new_i, "o": new_o, "c": subpath.get("c", True)}},
        }

    items: list[dict] = []
    for glyph in layout.glyphs:
        ch = glyph.char
        subs = glyph.subpaths
        if len(subs) == 1:
            sh = make_sh(subs[0])
            sh["nm"] = ch
            items.append(sh)
        elif len(subs) >= 2:
            shs = [make_sh(s) for s in subs]
            for sh in shs:
                sh["nm"] = ch
            # python-lottie requires every gr to have a tr child or its
            # restructure pass mis-identifies one of the sh nodes as the
            # transform; matches master_pack which always trails an identity tr.
            items.append(
                {
                    "ty": "gr",
                    "nm": ch,
                    "it": [*shs, _identity_tr()],
                }
            )
    return items


def _fill_white():
    return _fill_color([1.0, 1.0, 1.0, 1.0])


def _fill_black():
    return _fill_color([0.0, 0.0, 0.0, 1.0])


def _fill_color(rgba):
    return {
        "ty": "fl",
        "c": {"a": 0, "k": list(rgba)},
        "o": {"a": 0, "k": 100},
        "r": 1,
        "bm": 0,
        "nm": "swex_fill",
    }


def _stroke_color(rgba, width=4.0):
    return {
        "ty": "st",
        "c": {"a": 0, "k": list(rgba)},
        "o": {"a": 0, "k": 100},
        "w": {"a": 0, "k": float(width)},
        "lc": 2,
        "lj": 2,
        "bm": 0,
        "nm": "swex_stroke",
    }


def _contrast_stroke_for(fill_rgba):
    """Return a stroke item that contrasts with `fill_rgba`, or None.

    Picks a thin black outline for near-white fills (so the text reads on
    light-bodied stickers like the trophy/ring). For mid-luminance fills we
    skip — adding contrast there would make the mark feel pasted on.
    """
    if not fill_rgba:
        return None
    r, g, b = fill_rgba[:3]
    # Rec.709 luminance
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if lum > 0.85:
        return _stroke_color([0.0, 0.0, 0.0, 1.0], width=4.0)
    return None


def _first_fill_color(node, depth=0):
    """Return the first static fl/.c.k RGBA found inside `node` (recursive)."""
    if depth > 30 or not isinstance(node, dict):
        return None
    if node.get("ty") == "fl":
        c = node.get("c", {})
        if isinstance(c, dict) and c.get("a") == 0:
            k = c.get("k")
            if isinstance(k, list) and len(k) >= 3:
                return list(k) + ([1.0] if len(k) == 3 else [])
    for k in ("it", "shapes"):
        v = node.get(k)
        if isinstance(v, list):
            for c in v:
                col = _first_fill_color(c, depth + 1)
                if col is not None:
                    return col
    return None


def _identity_tr():
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


def replace_w_logo(tree: dict, renderer: FontRenderer, text: str = "swex") -> int:
    """Find every W-logo cluster and swap it for rendered `text` shapes.

    The structure produced by the designer is:
        wrapper_gr  (the cluster the user perceives as 'logo')
          it[0..N-1] = component grs (each a sub-shape of the W: ~5 of them)
          it[N]      = tr  (positions the whole logo)

    We locate `wrapper_gr` by walking up from the 15-vert silhouette sh,
    compute the union bbox of all sibling component grs, then replace those
    siblings with `text` glyphs at 1.4× the union bbox.

    Returns the number of clusters replaced (typically 1 per sticker).
    """
    paths = list(_walk_paths(tree, _is_w_sh))
    if not paths:
        return 0

    replaced = 0
    # Sort longest paths first — replacing inner clusters before outer ones
    # keeps the indices of outer clusters stable.
    paths.sort(key=len, reverse=True)
    for p in paths:
        # `p` ends ('it', i_sh) — the sh sits inside its own little gr.
        # One level up is the component gr (the 'W silhouette' alone).
        # Two levels up is the wrapper gr whose `it` list we want.
        if len(p) < 4:
            continue
        # Component gr is at path p[:-2]; its parent list is at p[:-4] + ('it',).
        component_path = p[:-2]
        p[:-4]
        try:
            wrapper_list, comp_idx = _parent_list_at(tree, component_path)
        except (KeyError, IndexError):
            continue

        # All component grs are siblings of the W gr in the wrapper list.
        # Components are gr's that contain a sh; tr's are positioning siblings.
        # We treat every gr child as a component to remove (the wrapper's tr
        # is not a gr so it survives).
        comp_indices = [
            i for i, c in enumerate(wrapper_list) if isinstance(c, dict) and c.get("ty") == "gr"
        ]
        if comp_idx not in comp_indices:
            continue

        # Position the text where the W's *visible silhouette* lives.
        # - The 15-vert silhouette sh defines the natural mark size (148×135).
        # - The component gr's `tr.p` (one level up from the sh) places that
        # silhouette inside the wrapper.
        # We use that pair so the swex text occupies exactly the W's footprint,
        # not the union of every decorative side-stroke (which would over-size it
        # by ~45% horizontally on most stickers).
        w_sh_node = _node_at(tree, p)
        local_bbox = _bbox_of_shape_vertices(w_sh_node)
        if local_bbox is None:
            continue
        cx_offset = 0.0
        cy_offset = 0.0
        comp_node = _node_at(tree, component_path)
        comp_tr = next(
            (c for c in (comp_node.get("it") or []) if isinstance(c, dict) and c.get("ty") == "tr"),
            None,
        )
        if comp_tr is not None:
            tp = comp_tr.get("p", {}).get("k")
            if isinstance(tp, list) and len(tp) >= 2 and not isinstance(tp[0], dict):
                cx_offset = float(tp[0])
                cy_offset = float(tp[1])
        target_bbox = (
            local_bbox[0] + cx_offset,
            local_bbox[1] + cy_offset,
            local_bbox[2] + cx_offset,
            local_bbox[3] + cy_offset,
        )

        # Emit 4 sibling items named "s","w","e","x" — the convention the
        # webapp's findSwexContainers / renderTextToShapes pipeline already
        # knows how to swap for a user's nickname or logo. The webapp picks
        # up the union bbox of these four items and renders the user's
        # content into it, inheriting the wrapper's transform (rotation,
        # scale, animation) just like the original W did.
        letter_items = _render_swex_named_letters(renderer, target_bbox)
        if not letter_items:
            continue

        # Pick the colour of the W's original fill so the text reads against
        # whatever background the original mark was painted on.
        fill_rgba = None
        for i in comp_indices:
            col = _first_fill_color(wrapper_list[i])
            if col is not None:
                fill_rgba = col
                break
        if fill_rgba is None:
            fill_rgba = [1.0, 1.0, 1.0, 1.0]
        fill_item = _fill_color(fill_rgba)
        fill_item["nm"] = "Fill"
        extras: list = [fill_item]
        stroke = _contrast_stroke_for(fill_rgba)
        if stroke is not None:
            stroke["nm"] = "Stroke"
            extras.append(stroke)

        insert_at = min(comp_indices)
        for i in sorted(comp_indices, reverse=True):
            del wrapper_list[i]
        # Insert letters first, then fill/stroke before the wrapper's tr.
        for offset, item in enumerate(letter_items):
            wrapper_list.insert(insert_at + offset, item)
        for offset, item in enumerate(extras):
            wrapper_list.insert(insert_at + len(letter_items) + offset, item)
        replaced += 1

    return replaced


def _find_text_row_groups(tree: dict):
    """Yield (parent_list, indices, bbox, text_chars) for every group whose
    children are short-nm 'gr' siblings that read like a text run.

    The passport stores text as a chain of gr nodes named after the literal
    character ('V', 'E', 'L', 'O', '@', '1', '2', '5'…). We find runs of ≥3
    such siblings and treat each run as one text line that should become `swex`.
    """
    rows = []

    def is_letter_gr(node):
        if not isinstance(node, dict) or node.get("ty") != "gr":
            return None
        nm = node.get("nm", "")
        if not isinstance(nm, str) or not nm:
            return None
        # strip duplicate-suffix "e 2", "r 2", etc.
        head = nm.split(" ")[0]
        # one printable character (letter / digit / @ / , / .)
        if len(head) == 1 and head.isprintable() and head not in (",", "."):
            return head
        if len(head) == 1 and head in ("@", ",", "."):
            return head
        if len(head) <= 2 and head.isdigit():
            return head
        return None

    def visit(node, depth=0):
        if depth > 50 or not isinstance(node, dict):
            return
        for k in ("it", "shapes", "layers", "assets"):
            v = node.get(k)
            if isinstance(v, list) and v:
                # collect runs of letter-grs
                run_idxs = []
                run_chars = []
                for i, c in enumerate(v):
                    ch = is_letter_gr(c)
                    if ch is not None:
                        run_idxs.append(i)
                        run_chars.append(ch)
                    else:
                        if len(run_idxs) >= 3:
                            bbs = [_bbox_of_shape_vertices(v[j]) for j in run_idxs]
                            bbs = [b for b in bbs if b is not None]
                            if bbs:
                                xmin = min(b[0] for b in bbs)
                                ymin = min(b[1] for b in bbs)
                                xmax = max(b[2] for b in bbs)
                                ymax = max(b[3] for b in bbs)
                                rows.append(
                                    (
                                        v,
                                        list(run_idxs),
                                        (xmin, ymin, xmax, ymax),
                                        "".join(run_chars),
                                    )
                                )
                        run_idxs = []
                        run_chars = []
                if len(run_idxs) >= 3:
                    bbs = [_bbox_of_shape_vertices(v[j]) for j in run_idxs]
                    bbs = [b for b in bbs if b is not None]
                    if bbs:
                        xmin = min(b[0] for b in bbs)
                        ymin = min(b[1] for b in bbs)
                        xmax = max(b[2] for b in bbs)
                        ymax = max(b[3] for b in bbs)
                        rows.append(
                            (v, list(run_idxs), (xmin, ymin, xmax, ymax), "".join(run_chars))
                        )
                for c in v:
                    visit(c, depth + 1)

    visit(tree)
    return rows


# Map each detected passport line (by its original text) to its swex replacement.
PASSPORT_REPLACEMENTS = [
    # primary identifying text
    ("VELORA", "SWEX"),
    ("velorer", "swex"),
    ("LosAngeles,90011", "swex.tg"),
    ("125MainSt.", "swex.tg"),
    # signature is a hand-drawn shape, not a glyph row — handled separately
]


def _replacement_for(orig_text: str) -> str | None:
    """Pick the swex replacement matching one of the passport's text rows.

    Matching is loose: we look for distinguishing characters of each canonical
    string anywhere in the run (different glyph orderings can shuffle the row's
    chars, but the underlying letters are the same set).
    """
    lc = orig_text.lower()
    if "velora" in lc and "@" not in orig_text:
        return "SWEX"
    if "@" in orig_text:
        return "@swex"
    # street: "125 Main St."
    if "main" in lc or "125" in orig_text:
        return "swex.tg"
    # city, ZIP: "Los Angeles, 90011"
    if "angeles" in lc or "90011" in orig_text:
        return "swex city"
    return None


def handle_passport(tree: dict, renderer: FontRenderer) -> dict:
    """Replace passport texts + photo placeholder. Returns stats dict."""
    stats = {"rows": 0, "photo": 0, "logo": 0}

    # Replace text rows — only the main name row becomes named s/w/e/x
    # (so the webapp's flow can swap it for the user's nickname). The other
    # text rows just become flat text so the passport still reads as an ID
    # but doesn't accidentally hijack the user-replacement targets.
    rows = _find_text_row_groups(tree)
    by_list: dict[int, list] = {}
    for parent_list, idxs, bbox, text in rows:
        by_list.setdefault(id(parent_list), []).append((parent_list, idxs, bbox, text))
    for entries in by_list.values():
        entries.sort(key=lambda e: min(e[1]), reverse=True)
        for parent_list, idxs, bbox, text in entries:
            new_text = _replacement_for(text)
            if new_text is None:
                continue
            lc = text.lower()
            # The VELORA brand line is the "primary identity" — name it as
            # swex letters so the webapp can replace it with the user's
            # nickname. Other lines stay as static text fill.
            is_primary = "velora" in lc and "@" not in text
            # Passport text is printed black on the gray ID card.
            if is_primary:
                items = _render_swex_named_letters(renderer, bbox)
                extras = [_fill_black()]
            else:
                shapes = _render_swex_shapes(renderer, new_text, bbox, expand=1.0)
                items = [
                    {
                        "ty": "gr",
                        "nm": f"swex_text_{new_text}",
                        "it": [*shapes, _fill_black(), _identity_tr()],
                    }
                ]
                extras = []
            if not items:
                continue
            insert_at = min(idxs)
            for i in sorted(idxs, reverse=True):
                del parent_list[i]
            for off, it in enumerate(items + extras):
                parent_list.insert(insert_at + off, it)
            stats["rows"] += 1

    # W-logo (small globe-like mark) → swex
    stats["logo"] = replace_w_logo(tree, renderer)

    # Photo placeholder: the front of the passport contains a shape layer
    # drawing a single 148×148 black rectangle that scale-pops from 0 → 110%.
    # Inject the "?" *inside* that layer so it inherits the pop animation
    # and disappears when the photo does.
    if _add_qmark_to_photo_layer(tree, renderer):
        stats["photo"] = 1

    return stats


def _add_qmark_to_photo_layer(tree: dict, renderer: FontRenderer) -> bool:
    """Find comp_0's photo placeholder layer and append a "?" gr to its shapes."""
    assets = tree.get("assets") or []
    if not assets:
        return False
    comp0 = assets[0]
    for layer in comp0.get("layers") or []:
        if not isinstance(layer, dict) or layer.get("ty") != 4:
            continue
        shapes = layer.get("shapes") or []
        # The photo layer is a single gr whose only sh is a 4-vert ~148×148 rect.
        if len(shapes) != 1:
            continue
        gr = shapes[0]
        if not isinstance(gr, dict) or gr.get("ty") != "gr":
            continue
        it = gr.get("it") or []
        sh_nodes = [c for c in it if isinstance(c, dict) and c.get("ty") == "sh"]
        if len(sh_nodes) != 1:
            continue
        ks = sh_nodes[0].get("ks", {}).get("k", {})
        if not isinstance(ks, dict):
            continue
        v = ks.get("v") or []
        if len(v) != 4:
            continue
        xs = [p[0] for p in v]
        ys = [p[1] for p in v]
        lw = max(xs) - min(xs)
        lh = max(ys) - min(ys)
        if not (140 < lw < 160 and 140 < lh < 160):
            continue
        # Found it. Render a centred "?" at ~55% of the rect's local size,
        # in a new shape with white fill. Lottie draws shapes[0] *last*
        # (= on top), so insert at index 0 to overlay on the black rect.
        bbox = (min(xs), min(ys), max(xs), max(ys))
        qmark = _render_swex_shapes(renderer, "?", bbox, expand=0.55)
        if not qmark:
            return False
        layer["shapes"].insert(
            0,
            {
                "ty": "gr",
                "nm": "swex_qmark",
                "it": [*qmark, _fill_white(), _identity_tr()],
            },
        )
        return True
    return False


def _find_passport_photo(tree: dict):
    """Locate the black photo rectangle on the passport.

    Strategy: walk the main composition's shape layers, pick the largest
    4-vert sh that has a dark fill (black or near-black). Returns
    (layers_list_to_append_to, canvas_bbox_of_the_rect).
    """
    main_layers = tree.get("layers") or []
    # The passport canvas has a Lottie_Main_Comp precomp ref. Resolve assets.
    assets_by_id = {a.get("id"): a for a in tree.get("assets") or [] if isinstance(a, dict)}

    best = None
    best_area = 0.0

    def visit_layer(layer, parent_xform=(1.0, 1.0, 0.0, 0.0)):
        nonlocal best, best_area
        if not isinstance(layer, dict):
            return
        # local xform for this layer
        ks = layer.get("ks") or {}
        sx, sy, ox, oy = parent_xform
        # accept static or single-keyframe
        s = ks.get("s", {}).get("k")
        if isinstance(s, list) and len(s) >= 2 and not isinstance(s[0], dict):
            lsx, lsy = float(s[0]) / 100.0, float(s[1]) / 100.0
        else:
            lsx, lsy = 1.0, 1.0
        ps = ks.get("p", {}).get("k")
        if isinstance(ps, list) and len(ps) >= 2 and not isinstance(ps[0], dict):
            lpx, lpy = float(ps[0]), float(ps[1])
        else:
            lpx, lpy = 0.0, 0.0
        anc = ks.get("a", {}).get("k")
        if isinstance(anc, list) and len(anc) >= 2 and not isinstance(anc[0], dict):
            lax, lay = float(anc[0]), float(anc[1])
        else:
            lax, lay = 0.0, 0.0
        new_sx = sx * lsx
        new_sy = sy * lsy
        new_ox = ox + sx * (lpx - lsx * lax)
        new_oy = oy + sy * (lpy - lsy * lay)

        if layer.get("ty") == 0:  # precomp
            asset = assets_by_id.get(layer.get("refId"))
            if asset:
                for child in asset.get("layers") or []:
                    visit_layer(child, (new_sx, new_sy, new_ox, new_oy))
        elif layer.get("ty") == 4:  # shape
            for shape in layer.get("shapes") or []:
                check_group(shape, new_sx, new_sy, new_ox, new_oy)

    def check_group(node, sx, sy, ox, oy, depth=0):
        nonlocal best, best_area
        if depth > 30 or not isinstance(node, dict):
            return
        ty = node.get("ty")
        if ty == "gr":
            it = node.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            csx, csy, cox, coy = sx, sy, ox, oy
            if tr is not None:
                ts = tr.get("s", {}).get("k", [100, 100])
                if isinstance(ts, list) and len(ts) >= 2 and not isinstance(ts[0], dict):
                    csx = sx * float(ts[0]) / 100.0
                    csy = sy * float(ts[1]) / 100.0
                tp = tr.get("p", {}).get("k", [0, 0])
                if isinstance(tp, list) and len(tp) >= 2 and not isinstance(tp[0], dict):
                    cox = ox + sx * float(tp[0])
                    coy = oy + sy * float(tp[1])
            for child in it:
                check_group(child, csx, csy, cox, coy, depth + 1)
        elif ty == "sh":
            ks = node.get("ks", {}).get("k", {})
            v = ks.get("v") if isinstance(ks, dict) else None
            if isinstance(v, list) and len(v) == 4:
                xs = [p[0] for p in v]
                ys = [p[1] for p in v]
                lw = max(xs) - min(xs)
                lh = max(ys) - min(ys)
                cw = lw * abs(sx)
                ch = lh * abs(sy)
                area = cw * ch
                # Photo rect on the passport is ~165 px square at canvas scale.
                if cw > 130 and ch > 130 and cw < 250 and ch < 250 and area > best_area:
                    cx_local = (min(xs) + max(xs)) / 2.0
                    cy_local = (min(ys) + max(ys)) / 2.0
                    cx_can = ox + sx * cx_local
                    cy_can = oy + sy * cy_local
                    # Photo sits in left half of the passport.
                    if cx_can < 280:
                        best_area = area
                        best = (cx_can, cy_can, cw, ch)

    for layer in main_layers:
        visit_layer(layer)

    if best is None:
        return None
    cx, cy, w, h = best
    bbox = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    return main_layers, bbox


def _build_overlay_layer(shapes: list[dict], *, name: str) -> dict:
    """Top-level shape layer drawing `shapes` filled white at canvas coords."""
    return {
        "ddd": 0,
        "ind": 999999,
        "ty": 4,
        "nm": name,
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [0, 0, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "ao": 0,
        "shapes": [
            {
                "ty": "gr",
                "nm": name,
                "it": [*shapes, _fill_white(), _identity_tr()],
            }
        ],
        "ip": 0,
        "op": 1e6,
        "st": 0,
        "bm": 0,
    }


def handle_velor_wordmark(tree: dict, renderer: FontRenderer) -> int:
    """Replace V/E/L/O/R glyph runs anywhere in the tree.

    Emits the four named s/w/e/x items so the webapp can swap the run with
    the user's nickname (matches the master_pack convention).
    """
    rows = _find_text_row_groups(tree)
    rows.sort(key=lambda r: min(r[1]), reverse=True)
    replaced = 0
    for parent_list, idxs, bbox, text in rows:
        lc = text.lower()
        if not any(ch in lc for ch in ("v", "e", "l", "o", "r")):
            continue
        letter_items = _render_swex_named_letters(renderer, bbox)
        if not letter_items:
            continue
        insert_at = min(idxs)
        for i in sorted(idxs, reverse=True):
            del parent_list[i]
        for off, item in enumerate([*letter_items, _fill_white()]):
            parent_list.insert(insert_at + off, item)
        replaced += 1
    return replaced


def render_png_preview(tgs_path: Path, out_png: Path, size: int = 256):
    """Middle-frame PNG preview via python-lottie → SVG → rsvg-convert."""
    from lottie.exporters.svg import export_svg
    from lottie.parsers.tgs import parse_tgs

    anim = parse_tgs(str(tgs_path))
    mid = int((anim.in_point + anim.out_point) / 2)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".svg", delete=False) as f:
        export_svg(anim, f, frame=mid)
        svg_path = f.name
    try:
        subprocess.run(
            [
                "C:/msys64/mingw64/bin/rsvg-convert.exe",
                "-w",
                str(size),
                "-h",
                str(size),
                svg_path,
                "-o",
                str(out_png),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(svg_path)


def process_one(src: Path, out_dir: Path, renderer: FontRenderer) -> dict:
    tree = _load(src)
    stem = src.stem
    info = {"file": src.name}

    if stem == PASSPORT_ID:
        info["mode"] = "passport"
        info.update(handle_passport(tree, renderer))
    elif stem == VELOR_ID:
        info["mode"] = "velor"
        info["replaced"] = handle_velor_wordmark(tree, renderer)
        # Fall through: this file has no W-logo anyway, so nothing else to do.
    else:
        info["mode"] = "w_logo"
        info["replaced"] = replace_w_logo(tree, renderer)

    out = out_dir / src.name
    _save(out, tree)
    info["out"] = str(out)
    info["bytes"] = out.stat().st_size
    return info


def main():
    OUT_DIR.mkdir(exist_ok=True)
    PREVIEW_DIR.mkdir(exist_ok=True)
    renderer = FontRenderer(FONT_PATH)
    files = sorted(SRC_DIR.glob("*.tgs"))
    print(f"Processing {len(files)} stickers...")
    rows = []
    for f in files:
        try:
            info = process_one(f, OUT_DIR, renderer)
            rows.append(info)
            print(f"  ok {info['mode']:8s} {info['file']}  {info}")
        except Exception as e:
            print(f"  FAIL {f.name}: {e}")
            import traceback

            traceback.print_exc()
    print()
    print("Rendering previews...")
    for f in sorted(OUT_DIR.glob("*.tgs")):
        out_png = PREVIEW_DIR / (f.stem + ".png")
        try:
            render_png_preview(f, out_png)
            print(f"  preview {f.name}")
        except Exception as e:
            print(f"  PREVIEW FAIL {f.name}: {e}")


if __name__ == "__main__":
    main()
