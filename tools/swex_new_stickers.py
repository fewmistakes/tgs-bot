"""Process the second sticker pack (`new_stickers/`): detect the placeholder
text run inside each TGS and swap it for `swex` rendered as named s/w/e/x
sibling items — the convention the runtime replacer expects.

Detection: the visible text in these stickers is baked into curves, sitting as
a run of sibling `gr` or `sh` nodes inside some parent list. Each letter is
1-3 sub-paths. The naive local-bbox heuristic fails because the same file may
contain multiple letter-like clusters (watch numbers, frame outlines, decoration)
and the one the user sees may not have the largest local bbox.

We solve this by computing the CANVAS-SPACE bounding box of each candidate:
walking through layer parenting + precomp refs + group transforms, accumulating
an affine (sx, sy, ox, oy). The candidate whose canvas-space bbox sits in the
visible area, has a reasonable on-screen height, and looks most text-like wins.

Output:
  emoji_stickers/<id>.tgs                 — original copy
  emoji_stickers_swex/<id>.tgs            — swex-templated
  emoji_stickers_swex_preview/<id>.png    — middle-frame raster preview
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.swex.glyphs import FontRenderer
from tools.swex_emoji_stickers import (
    _fill_color,
    _identity_tr,
    _render_swex_named_letters,
    render_png_preview,
)

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = Path("C:/Users/USER/Downloads/Telegram Desktop/new_stickers")
ORIG_DIR = ROOT / "emoji_stickers"
OUT_DIR = ROOT / "emoji_stickers_swex"
PREVIEW_DIR = ROOT / "emoji_stickers_swex_preview"
FONT_PATH = ROOT / "fonts" / "SeymourOne-Regular.ttf"
POSITIONS_MANIFEST = ROOT / "tools" / "sticker_positions.json"

# OCR — used to auto-find text bboxes on each rendered preview. Cached so
# re-runs don't re-OCR every sticker. The cache lives next to the manifest.
OCR_CACHE_PATH = ROOT / "tools" / "_ocr_bboxes.json"
PNG_CACHE_DIR = ROOT / "tools" / "_picker_pngs"
TESSERACT_BIN = "C:/msys64/mingw64/bin/tesseract.exe"
TESSDATA_DIR = ROOT / "tessdata"

_ocr_bboxes: dict[str, list[dict]] | None = None


def _ocr_text_bboxes(sid: str) -> list[dict]:
    """Return a list of `{cx, cy, w, h, conf}` for text found in the
    sticker's rendered preview. Returns empty list if nothing was found,
    or if pytesseract / language data are unavailable.

    Results are cached to disk so a re-run after a manifest tweak doesn't
    pay the OCR cost again.
    """
    global _ocr_bboxes
    if _ocr_bboxes is None:
        if OCR_CACHE_PATH.exists():
            try:
                _ocr_bboxes = json.loads(OCR_CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                _ocr_bboxes = {}
        else:
            _ocr_bboxes = {}
    if sid in _ocr_bboxes:
        return _ocr_bboxes[sid]
    boxes = _run_ocr_for(sid)
    _ocr_bboxes[sid] = boxes
    OCR_CACHE_PATH.write_text(
        json.dumps(_ocr_bboxes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return boxes


def _run_ocr_for(sid: str) -> list[dict]:
    """Render the original sticker (if not cached) and run OCR on it.

    Tries multiple PSM modes and both inverted / original images to maximise
    the chance of catching stylised text. Returns canvas-space bboxes for
    every detection with confidence > 40.
    """
    png_path = PNG_CACHE_DIR / f"{sid}.png"
    if not png_path.exists():
        # render preview from the original
        src = SRC_DIR / f"{sid}.tgs"
        if not src.exists():
            return []
        PNG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from lottie.exporters.svg import export_svg
            from lottie.parsers.tgs import parse_tgs

            anim = parse_tgs(str(src))
            mid = int((anim.in_point + anim.out_point) / 2)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".svg", delete=False
            ) as fh:
                export_svg(anim, fh, frame=mid)
                svg_path = fh.name
            subprocess.run(
                [
                    "C:/msys64/mingw64/bin/rsvg-convert.exe",
                    "-w",
                    "512",
                    "-h",
                    "512",
                    svg_path,
                    "-o",
                    str(png_path),
                ],
                check=True,
                capture_output=True,
            )
            os.unlink(svg_path)
        except Exception as e:
            print(f"  render fail {sid}: {e}")
            return []
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError:
        return []
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_BIN
    os.environ["TESSDATA_PREFIX"] = str(TESSDATA_DIR)
    if not TESSDATA_DIR.exists():
        return []

    img = Image.open(png_path).convert("RGB")
    canvas_w, canvas_h = img.size  # 512
    # Single upscale + invert sweep — enough for our pack, faster than the
    # full 8-combo grid we tried during development.
    detections: list[dict] = []
    for inv in (False, True):
        base = ImageOps.invert(img) if inv else img
        big = base.resize((canvas_w * 3, canvas_h * 3))
        for lang in ("eng", "rus"):
            for psm in (6, 11):
                try:
                    data = pytesseract.image_to_data(
                        big,
                        lang=lang,
                        config=f"--tessdata-dir {TESSDATA_DIR} --psm {psm}",
                        output_type=pytesseract.Output.DICT,
                    )
                except Exception:
                    continue
                for i in range(len(data["text"])):
                    t = data["text"][i].strip()
                    c = int(data["conf"][i])
                    if not t or c < 40 or len(t) < 2:
                        continue
                    # Quality filter: reject "garbage" OCR results where
                    # Tesseract misreads geometry as text. Common pattern:
                    # short string with parens/braces/punctuation, or no
                    # alphabetic chars at all. Lock-handle was misread as
                    # "fa)" — exactly this pattern.
                    alpha_chars = sum(1 for ch in t if ch.isalpha())
                    if alpha_chars < max(2, len(t) - 1):
                        continue
                    if any(ch in t for ch in "()[]{}|\\/<>@#$%^&*~`"):
                        continue
                    left = data["left"][i] / 3.0
                    top = data["top"][i] / 3.0
                    w = data["width"][i] / 3.0
                    h = data["height"][i] / 3.0
                    if h < 8 or h > 200 or w < 8:
                        continue
                    detections.append(
                        {
                            "cx": left + w / 2.0,
                            "cy": top + h / 2.0,
                            "w": w,
                            "h": h,
                            "conf": c,
                            "text": t,
                        }
                    )
    # Cluster overlapping detections — Tesseract often emits multiple
    # near-duplicates from the different PSM/invert combos.
    merged: list[dict] = []
    for d in sorted(detections, key=lambda x: -x["conf"]):
        for m in merged:
            if (
                abs(d["cx"] - m["cx"]) < max(d["w"], m["w"]) * 0.4
                and abs(d["cy"] - m["cy"]) < max(d["h"], m["h"]) * 0.6
            ):
                # absorb — keep the higher-conf entry's text but expand bbox
                x0 = min(d["cx"] - d["w"] / 2, m["cx"] - m["w"] / 2)
                y0 = min(d["cy"] - d["h"] / 2, m["cy"] - m["h"] / 2)
                x1 = max(d["cx"] + d["w"] / 2, m["cx"] + m["w"] / 2)
                y1 = max(d["cy"] + d["h"] / 2, m["cy"] + m["h"] / 2)
                m["cx"] = (x0 + x1) / 2
                m["cy"] = (y0 + y1) / 2
                m["w"] = x1 - x0
                m["h"] = y1 - y0
                break
        else:
            merged.append(dict(d))
    return merged


def _load_positions_manifest() -> dict:
    """Load picker-built per-sticker positions; absence is OK."""
    if not POSITIONS_MANIFEST.exists():
        return {}
    try:
        return json.loads(POSITIONS_MANIFEST.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARN: failed to parse {POSITIONS_MANIFEST}: {e}")
        return {}


def _hex_to_rgb(hx: str) -> tuple[float, float, float]:
    s = (hx or "").lstrip("#").strip()
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return (0.0, 0.0, 0.0)
    return (
        int(s[0:2], 16) / 255.0,
        int(s[2:4], 16) / 255.0,
        int(s[4:6], 16) / 255.0,
    )


def _load(p: Path) -> dict:
    return json.loads(gzip.decompress(p.read_bytes()))


def _save(p: Path, tree: dict) -> None:
    p.write_bytes(gzip.compress(json.dumps(tree, separators=(",", ":")).encode()))


def _static_scale(prop) -> tuple[float, float]:
    """Read (sx, sy) — handles static, animated (mid keyframe)."""
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
    # take the keyframe with the largest |sx|+|sy| (most-visible state)
    best, best_mag = (1.0, 1.0), -1.0
    for kf in k:
        if isinstance(kf, dict):
            s = kf.get("s")
            if isinstance(s, list) and len(s) >= 2:
                sx, sy = float(s[0]) / 100.0, float(s[1]) / 100.0
                mag = abs(sx) + abs(sy)
                if mag > best_mag:
                    best_mag, best = mag, (sx, sy)
    return best


def _static_pos(prop) -> tuple[float, float]:
    """Read (x, y) — handles static or average of keyframes."""
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
    xs, ys = [], []
    for kf in k:
        if isinstance(kf, dict):
            s = kf.get("s")
            if isinstance(s, list) and len(s) >= 2:
                xs.append(float(s[0]))
                ys.append(float(s[1]))
    if not xs:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _apply_xform(ks_or_tr, sx, sy, ox, oy):
    """Apply a Lottie ks/tr transform on top of the accumulated affine."""
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


def _sh_vert_count(sh: dict) -> int:
    """Vertex count of a sh node, handling both static and animated paths.

    Static: `ks.a == 0`, `ks.k` is `{v: [...], i: [...], o: [...], c: bool}`.
    Animated: `ks.a == 1`, `ks.k` is a list of keyframes; each keyframe's
    `s[0]` is a shape-state dict with its own `v`/`i`/`o`/`c`. We treat the
    first keyframe as representative — letter morph animations don't add
    new vertices mid-animation, so the count is stable across keyframes.
    """
    ks = sh.get("ks", {}) if isinstance(sh, dict) else {}
    if not isinstance(ks, dict):
        return 0
    k = ks.get("k")
    if ks.get("a") == 0 and isinstance(k, dict):
        return len(k.get("v") or [])
    if isinstance(k, list) and k and isinstance(k[0], dict):
        s = k[0].get("s")
        if isinstance(s, list) and s and isinstance(s[0], dict):
            return len(s[0].get("v") or [])
        if isinstance(s, dict):
            return len(s.get("v") or [])
    return 0


def _gr_sh_stats(gr: dict) -> tuple[int, int, int] | None:
    """Return (sh_count, total_verts, max_verts) of every sh descendant.

    Designers (e.g. the pink-woman butt sticker) often wrap each letter's
    fill+outline pair in its OWN inner gr, so the direct children of the
    letter-gr are sub-grs, not sh nodes. Counting only direct sh missed
    those entirely. Walk recursively, stopping if depth gets silly. Also
    handles animated sh (morphing path) via `_sh_vert_count`.
    """
    sh_count = total = mx = 0

    def visit(node, depth=0):
        nonlocal sh_count, total, mx
        if depth > 6 or not isinstance(node, dict):
            return
        if node.get("ty") == "sh":
            n = _sh_vert_count(node)
            if n > 0:
                sh_count += 1
                total += n
                mx = max(mx, n)
            return
        for k in ("it", "shapes"):
            v = node.get(k)
            if isinstance(v, list):
                for c in v:
                    visit(c, depth + 1)

    for s in gr.get("it") or []:
        visit(s)
    if sh_count == 0:
        return None
    return sh_count, total, mx


def _sh_vertices(sh: dict) -> list[tuple[float, float]]:
    """Vertex list of a sh node (static or first keyframe of animated)."""
    if not isinstance(sh, dict):
        return []
    ks = sh.get("ks", {})
    if not isinstance(ks, dict):
        return []
    k = ks.get("k")
    src = None
    if ks.get("a") == 0 and isinstance(k, dict):
        src = k.get("v")
    elif isinstance(k, list) and k and isinstance(k[0], dict):
        s = k[0].get("s")
        if isinstance(s, list) and s and isinstance(s[0], dict):
            src = s[0].get("v")
        elif isinstance(s, dict):
            src = s.get("v")
    if not isinstance(src, list):
        return []
    return [(float(v[0]), float(v[1])) for v in src if isinstance(v, list) and len(v) >= 2]


def _sh_letter_bbox(sh: dict) -> tuple[float, float, float, float] | None:
    """Bbox of one sh node's vertices (static or animated path)."""
    pts = _sh_vertices(sh)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_of_node(node: dict) -> tuple[float, float, float, float] | None:
    """Union bbox of every sh vertex below `node`, applying group tr offsets."""
    pts: list[tuple[float, float]] = []

    def visit(n, ox=0.0, oy=0.0, sc=1.0, depth=0):
        if depth > 30 or not isinstance(n, dict):
            return
        if n.get("ty") == "sh":
            for v in _sh_vertices(n):
                pts.append((ox + sc * v[0], oy + sc * v[1]))
            return
        if n.get("ty") == "gr":
            it = n.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            nox, noy, nsc = ox, oy, sc
            if tr is not None:
                tp = tr.get("p", {}).get("k") if isinstance(tr.get("p"), dict) else None
                if isinstance(tp, list) and len(tp) >= 2 and not isinstance(tp[0], dict):
                    nox = ox + sc * float(tp[0])
                    noy = oy + sc * float(tp[1])
                ts = tr.get("s", {}).get("k") if isinstance(tr.get("s"), dict) else None
                if isinstance(ts, list) and len(ts) >= 2 and not isinstance(ts[0], dict):
                    nsc = sc * float(ts[0]) / 100.0
            for c in it:
                if c is tr:
                    continue
                visit(c, nox, noy, nsc, depth + 1)
            return
        for k in ("it", "shapes"):
            v = n.get(k)
            if isinstance(v, list):
                for c in v:
                    visit(c, ox, oy, sc, depth + 1)

    visit(node)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _is_text_like_gr(gr: dict) -> bool:
    stats = _gr_sh_stats(gr)
    if stats is None:
        return False
    sh_count, _, max_verts = stats
    return 1 <= sh_count <= 4 and 4 <= max_verts <= 80


def _is_text_like_sh(sh: dict) -> bool:
    n = _sh_vert_count(sh)
    return 4 <= n <= 80


def _find_runs_in_list(parent_list: list) -> list[tuple[list[int], str]]:
    """Yield (indices, kind) for every contiguous 3-12 letter-like sub-run."""
    out = []
    # gr siblings
    idxs = [
        i
        for i, c in enumerate(parent_list)
        if isinstance(c, dict) and c.get("ty") == "gr" and _is_text_like_gr(c)
    ]
    runs = _contiguous_runs(parent_list, idxs)
    for r in runs:
        for n in range(3, min(13, len(r)) + 1):
            for s in range(len(r) - n + 1):
                out.append((r[s : s + n], "gr"))
    # sh siblings
    idxs = [
        i
        for i, c in enumerate(parent_list)
        if isinstance(c, dict) and c.get("ty") == "sh" and _is_text_like_sh(c)
    ]
    runs = _contiguous_runs(parent_list, idxs)
    for r in runs:
        for n in range(3, min(13, len(r)) + 1):
            for s in range(len(r) - n + 1):
                out.append((r[s : s + n], "sh"))
    return out


def _contiguous_runs(parent_list: list, idxs: list[int]) -> list[list[int]]:
    """Split idxs into runs that are contiguous in the parent list (allowing
    interleaved tr/fl/st/mm/gs items as gaps that don't break the run)."""
    if not idxs:
        return []
    runs = []
    cur = [idxs[0]]
    for i in idxs[1:]:
        gap_ok = True
        for j in range(cur[-1] + 1, i):
            n = parent_list[j]
            if isinstance(n, dict) and n.get("ty") not in ("tr", "fl", "st", "mm", "gs"):
                gap_ok = False
                break
        if gap_ok:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    return runs


def _shape_run_bbox(parent_list: list, idxs: list[int], kind: str):
    bboxes = []
    for i in idxs:
        c = parent_list[i]
        bb = _bbox_of_node(c) if kind == "gr" else _sh_letter_bbox(c)
        if bb is None:
            return None
        bboxes.append(bb)
    xmin = min(b[0] for b in bboxes)
    ymin = min(b[1] for b in bboxes)
    xmax = max(b[2] for b in bboxes)
    ymax = max(b[3] for b in bboxes)
    return (xmin, ymin, xmax, ymax), bboxes


# Per-sticker target position overrides. For stickers where the generic
# scoring picks the wrong run, this directs detection to the run closest to
# (target_x, target_y) on the 512×512 canvas — found by manual inspection of
# the rendered preview. Add a new entry only when the default scoring fails.
PER_STICKER_TARGETS: dict[str, tuple[float, float]] = {
    # purple skull jester — TEXT on laptop, want all 4 letters of TEXT not 3
    "5438330599997745209": (390, 403),
    # cute monkey holding a sign — TEXT on the sign
    "5440627157665620890": (425, 210),
}

# Stickers where the placeholder text is baked twice (precomp + top-level copy,
# or animated through two instances). Replacing only the best run leaves the
# duplicate visible. For these we replace EVERY run that passes the basic
# text-likeness threshold.
REPLACE_ALL_RUNS: set[str] = {
    "5390879127001667175",  # watch — "ангельский" appears in two precomps
    "5438580966526329523",  # gingerbread — TEXT in multiple frames
    "5440430061616404747",  # envelope — TEXT duplicated
    # Keep this list short: REPLACE_ALL can delete the visible run as a
    # "duplicate" and leave the placeholder hidden inside a precomp. Only add
    # stickers where the text is provably baked twice and single-best fails.
}

# Stickers where the visible TEXT lives in a position the runtime swex
# replacement can't reach safely (deep precomp / covered by later layers / etc.).
# For these we DELETE every detected text-like run (so the original
# "TEXT"/"ангельский" never bleeds through) and add a single top-level shape
# layer with `s/w/e/x` named letters at canvas centre — that layer renders
# last so it's always on top, and the runtime sees exactly one swex
# container as required.
FORCE_OVERLAY: set[str] = {
    "5438580966526329523",  # gingerbread — swex hides behind bomb circle otherwise
    # Text is an animated path in a nested group the detector can't reach
    # without false positives elsewhere.
    "5393351168443322946",
    # Not 5390879127001667175 (watch): a centred overlay is hidden by the aperture wheel.
}

# Per-sticker manual bbox overrides for stickers where OCR or heuristic
# misfire. Format: { sticker_id: (cx, cy, w, h) }. Bypass OCR and
# heuristic entirely — wipe shapes inside this bbox and put text there.
MANUAL_BBOX: dict[str, tuple[float, float, float, float]] = {
    # Empty by default. Add a `sticker_id: (cx, cy, w, h)` entry ONLY for
    # stickers where the auto detector clearly misfires after extensive
    # testing — the over-eager 50-entry list from earlier kept clobbering
    # legitimate art (truck details, headphone cups, frog limbs, watch
    # numbers). Default flow handles most stickers; per-sticker overrides
    # exist for the few stubborn outliers.
}

# Extra delete-bboxes for stickers where the placeholder is in TWO places
# (e.g. the pink-woman sticker has text on both sides; only one side gets the nick).
# These boxes are deleted but NO text is placed in them.
EXTRA_DELETE_BBOXES: dict[str, list[tuple[float, float, float, float]]] = {
    # Empty by default. Add ONLY when a sticker has the placeholder in
    # multiple places and the main bbox can't reach the secondary copy.
}


def find_text_run(tree: dict, sticker_id: str | None = None):
    """Return (parent_list, indices, local_bbox) for the best text run.

    Walks every layer accumulating canvas-space transforms (resolving precomp
    refIds and layer parenting). For each candidate run, computes canvas bbox
    via the transform and scores by:
      - is the centre inside the canvas? (visible)
      - is the height between 25 and 250 px? (reads like text)
      - aspect ratio? (text is wider than tall, but curved text ~ 1.3+)
      - height consistency among letters?
      - letter count?

    If `sticker_id` is in `PER_STICKER_TARGETS`, the candidate closest to that
    target (after passing the basic text-likeness filters) wins regardless of
    its global score.
    """
    canvas_w = float(tree.get("w", 512))
    canvas_h = float(tree.get("h", 512))

    assets: dict = {}
    for a in tree.get("assets") or []:
        if isinstance(a, dict) and "id" in a:
            assets[a["id"]] = a

    candidates = []  # (score, parent_list, idxs, local_bbox, canvas_bbox)

    def visit_shape(node, sx, sy, ox, oy, depth=0):
        if depth > 24 or not isinstance(node, dict):
            return
        ty = node.get("ty")
        if ty == "gr":
            it = node.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            csx, csy, cox, coy = sx, sy, ox, oy
            if tr is not None:
                csx, csy, cox, coy = _apply_xform(tr, sx, sy, ox, oy)
            # Score every run found inside this gr's `it` list
            _consider_runs(it, csx, csy, cox, coy)
            for c in it:
                if c is tr:
                    continue
                visit_shape(c, csx, csy, cox, coy, depth + 1)
            return
        # other shape types: nothing to recurse into for our purposes

    def _consider_runs(parent_list, sx, sy, ox, oy):
        for idxs, kind in _find_runs_in_list(parent_list):
            result = _shape_run_bbox(parent_list, idxs, kind)
            if result is None:
                continue
            local_bbox, letter_bboxes = result
            lx0, ly0, lx1, ly1 = local_bbox
            xs = [ox + sx * lx0, ox + sx * lx1]
            ys = [oy + sy * ly0, oy + sy * ly1]
            cbb = (min(xs), min(ys), max(xs), max(ys))
            # Look at the sibling fill (placed after the letters in the
            # designer's typical structure) to seed the colour bonus.
            fill_rgb = None
            for child in parent_list:
                if isinstance(child, dict) and child.get("ty") == "fl":
                    c = child.get("c", {})
                    if isinstance(c, dict) and c.get("a") == 0:
                        k = c.get("k")
                        if isinstance(k, list) and len(k) >= 3:
                            fill_rgb = (float(k[0]), float(k[1]), float(k[2]))
                            break
            score = _score_candidate(
                letter_bboxes, cbb, canvas_w, canvas_h, len(idxs), fill_color=fill_rgb
            )
            if score is None:
                continue
            candidates.append((score, parent_list, idxs, local_bbox, cbb))

    def visit_layers(layers_list, outer_sx, outer_sy, outer_ox, outer_oy, depth=0):
        if depth > 24 or not layers_list:
            return
        by_ind = {
            L["ind"]: L for L in layers_list if isinstance(L, dict) and L.get("ind") is not None
        }
        cache, resolving = {}, set()

        def resolve(layer):
            ind = layer.get("ind")
            if ind in cache:
                return cache[ind]
            if ind in resolving:
                return (outer_sx, outer_sy, outer_ox, outer_oy)
            resolving.add(ind)
            parent_ind = layer.get("parent")
            if parent_ind is not None and parent_ind in by_ind:
                parent_xform = resolve(by_ind[parent_ind])
            else:
                parent_xform = (outer_sx, outer_sy, outer_ox, outer_oy)
            my_xform = _apply_xform(layer.get("ks"), *parent_xform)
            resolving.discard(ind)
            cache[ind] = my_xform
            return my_xform

        for L in layers_list:
            if not isinstance(L, dict):
                continue
            lsx, lsy, lox, loy = resolve(L)
            ty = L.get("ty")
            if ty == 0:  # precomp ref
                asset = assets.get(L.get("refId"))
                if asset:
                    visit_layers(asset.get("layers") or [], lsx, lsy, lox, loy, depth + 1)
            elif ty == 4:  # shape layer
                for shape in L.get("shapes") or []:
                    visit_shape(shape, lsx, lsy, lox, loy, depth + 1)

    visit_layers(tree.get("layers") or [], 1.0, 1.0, 0.0, 0.0)
    if not candidates:
        return None

    target = PER_STICKER_TARGETS.get(sticker_id) if sticker_id else None
    if target is not None:
        tx, ty = target

        def dist(c):
            _, _, _, _, cbb = c
            cx = (cbb[0] + cbb[2]) / 2.0
            cy = (cbb[1] + cbb[3]) / 2.0
            return (cx - tx) ** 2 + (cy - ty) ** 2

        candidates.sort(key=dist)
    else:
        candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    # Final canvas-visibility sanity check: if the winner's canvas-space
    # height shrunk below ~25 px or its centre is far off-canvas, treat as
    # "no good candidate" so the caller can fall back to a centred overlay.
    cbb = best[4]
    bw = abs(cbb[2] - cbb[0])
    bh = abs(cbb[3] - cbb[1])
    ccx = (cbb[0] + cbb[2]) / 2.0
    ccy = (cbb[1] + cbb[3]) / 2.0
    if bh < 25 or bw < 25:
        return None
    if (
        ccx < -canvas_w * 0.05
        or ccx > canvas_w * 1.05
        or ccy < -canvas_h * 0.05
        or ccy > canvas_h * 1.05
    ):
        return None
    return best[1], best[2], best[3]


def find_all_text_runs(tree: dict, score_floor: float = 0.0):
    """Return EVERY (parent_list, idxs, local_bbox) that scored above floor.

    Some stickers contain the same text baked twice (designer used a precomp
    plus a top-level copy) — replacing only the highest-scoring instance
    leaves the duplicate visible. We dedupe by (id(parent_list), tuple(idxs))
    so each run is reported at most once.
    """
    canvas_w = float(tree.get("w", 512))
    canvas_h = float(tree.get("h", 512))
    assets: dict = {}
    for a in tree.get("assets") or []:
        if isinstance(a, dict) and "id" in a:
            assets[a["id"]] = a

    found: list[tuple[float, list, list[int], tuple]] = []
    seen: set[tuple[int, tuple[int, ...]]] = set()

    def _scan_list(parent_list, sx, sy, ox, oy):
        """Score every text-like run that lives directly inside parent_list."""
        for idxs, kind in _find_runs_in_list(parent_list):
            r = _shape_run_bbox(parent_list, idxs, kind)
            if r is None:
                continue
            local_bbox, lb = r
            xs = [ox + sx * local_bbox[0], ox + sx * local_bbox[2]]
            ys = [oy + sy * local_bbox[1], oy + sy * local_bbox[3]]
            cbb = (min(xs), min(ys), max(xs), max(ys))
            fill_rgb = None
            for child in parent_list:
                if isinstance(child, dict) and child.get("ty") == "fl":
                    c = child.get("c", {})
                    if isinstance(c, dict) and c.get("a") == 0:
                        k = c.get("k")
                        if isinstance(k, list) and len(k) >= 3:
                            fill_rgb = (float(k[0]), float(k[1]), float(k[2]))
                            break
            score = _score_candidate(lb, cbb, canvas_w, canvas_h, len(idxs), fill_color=fill_rgb)
            if score is None or score < score_floor:
                continue
            key = (id(parent_list), tuple(idxs))
            if key in seen:
                continue
            seen.add(key)
            found.append((score, parent_list, idxs, local_bbox))

    def visit_shape(node, sx, sy, ox, oy, depth=0):
        if depth > 24 or not isinstance(node, dict):
            return
        if node.get("ty") == "gr":
            it = node.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            csx, csy, cox, coy = sx, sy, ox, oy
            if tr is not None:
                csx, csy, cox, coy = _apply_xform(tr, sx, sy, ox, oy)
            _scan_list(it, csx, csy, cox, coy)
            for c in it:
                if c is tr:
                    continue
                visit_shape(c, csx, csy, cox, coy, depth + 1)

    def visit_layers(layers_list, osx, osy, oox, ooy, depth=0):
        if depth > 24 or not layers_list:
            return
        by_ind = {
            L["ind"]: L for L in layers_list if isinstance(L, dict) and L.get("ind") is not None
        }
        cache, resolving = {}, set()

        def resolve(L):
            ind = L.get("ind")
            if ind in cache:
                return cache[ind]
            if ind in resolving:
                return (osx, osy, oox, ooy)
            resolving.add(ind)
            pi = L.get("parent")
            px = resolve(by_ind[pi]) if pi is not None and pi in by_ind else (osx, osy, oox, ooy)
            cache[ind] = _apply_xform(L.get("ks"), *px)
            resolving.discard(ind)
            return cache[ind]

        for L in layers_list:
            if not isinstance(L, dict):
                continue
            sx, sy, ox, oy = resolve(L)
            ty = L.get("ty")
            if ty == 0:
                a = assets.get(L.get("refId"))
                if a:
                    visit_layers(a.get("layers") or [], sx, sy, ox, oy, depth + 1)
            elif ty == 4:
                for sh in L.get("shapes") or []:
                    visit_shape(sh, sx, sy, ox, oy, depth + 1)

    visit_layers(tree.get("layers") or [], 1.0, 1.0, 0.0, 0.0)
    return found


def _score_candidate(
    letter_bboxes,
    canvas_bbox,
    canvas_w,
    canvas_h,
    n,
    fill_color: tuple[float, float, float] | None = None,
):
    """Score a candidate using its CANVAS-space bbox.

    Reject runs that:
      - sit far outside the canvas (off-screen)
      - render at a height that doesn't read as text (<20 or >280 px)
      - have ridiculous aspect ratios (too thin, too tall)
      - have inconsistent letter heights (decoration, not text)
    """
    cx0, cy0, cx1, cy1 = canvas_bbox
    cbw = abs(cx1 - cx0)
    cbh = abs(cy1 - cy0)
    if cbw <= 0 or cbh <= 0:
        return None
    if not (20 <= cbh <= 280):
        return None
    aspect = cbw / cbh
    if aspect < 1.1:
        return None
    ccx = (cx0 + cx1) / 2.0
    ccy = (cy0 + cy1) / 2.0
    if ccx < -canvas_w * 0.1 or ccx > canvas_w * 1.1:
        return None
    if ccy < -canvas_h * 0.1 or ccy > canvas_h * 1.1:
        return None

    heights = [b[3] - b[1] for b in letter_bboxes]
    med_h = sorted(heights)[len(heights) // 2]
    if med_h <= 0:
        return None
    spread = sum(abs(h - med_h) for h in heights) / (len(heights) * med_h + 1e-6)
    consistency = max(0.0, 1.0 - spread)
    if consistency < 0.35:
        return None

    cx_center = canvas_w / 2.0
    cy_center = canvas_h / 2.0
    dist = ((ccx - cx_center) ** 2 + (ccy - cy_center) ** 2) ** 0.5
    centre_bonus = max(0.1, 1.0 - dist / (max(canvas_w, canvas_h) * 0.6))

    # Bonus for distinctive solid fills (white / black) — placeholder text
    # in these stickers is almost always one of those, while decoration may
    # use varied palette colours.
    fill_bonus = 1.0
    if fill_color is not None:
        r, g, b = fill_color[:3]
        # Rec.709 luminance
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        # white or near-white
        if lum > 0.85 and abs(r - g) < 0.2 and abs(g - b) < 0.2:
            fill_bonus = 1.6
        # near-black
        elif lum < 0.15:
            fill_bonus = 1.3

    # HARD preference for the placeholder lengths used in this pack:
    # • 4 letters → "TEXT"
    # • 10 letters → "ангельский"
    # Anything else is decoration / partial detection. Massive multiplier
    # so a length-4 or length-10 run almost always wins over a length-3
    # or length-7 false positive.
    if n == 4 or n == 10:
        length_bonus = 4.0
    elif n in (3, 5, 9, 11):  # close to placeholder length — soft bonus
        length_bonus = 1.5
    else:
        length_bonus = 1.0

    return consistency * (aspect**0.3) * cbh * (n**1.2) * centre_bonus * fill_bonus * length_bonus


def _first_fill_color(node, depth=0):
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


def _do_replace(parent_list: list, idxs: list[int], bbox: tuple, renderer: FontRenderer) -> bool:
    """Splice swex named letters into `parent_list` in place of `idxs`."""
    letter_items = _render_swex_named_letters(renderer, bbox)
    if not letter_items:
        return False
    fill_rgba = None
    for i in idxs:
        col = _first_fill_color(parent_list[i])
        if col is not None:
            fill_rgba = col
            break
    if fill_rgba is None:
        fill_rgba = [1.0, 1.0, 1.0, 1.0]
    fill_item = _fill_color(fill_rgba)
    fill_item["nm"] = "Fill"
    insert_at = min(idxs)
    for i in sorted(idxs, reverse=True):
        del parent_list[i]
    items = [*letter_items, fill_item]
    for offset, item in enumerate(items):
        parent_list.insert(insert_at + offset, item)
    return True


def _add_overlay_with_plate(
    tree: dict,
    renderer: FontRenderer,
    cx: float,
    cy: float,
    w: float,
    h: float,
    *,
    plate: bool = True,
    plate_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    text_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> bool:
    """Insert a top-level shape layer that draws (optional) plate + named
    s/w/e/x letters centred at (cx, cy) with size (w, h).

    Used by the picker-manifest path: the user clicks a position per
    sticker, this lays the plate exactly there so the user's nick is always
    in a known visible spot. Drawn on top so it covers any original placeholder.
    """
    pr, pg, pb = plate_color
    tr_, tg_, tb_ = text_color
    bbox = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)

    shapes: list[dict] = []
    if plate:
        # Rounded-rect plate: build as a 4-vert sh with the corners.
        _x0, _y0, _x1, _y1 = bbox
        shapes.append(
            {
                "ty": "rc",
                "p": {"a": 0, "k": [cx, cy]},
                "s": {"a": 0, "k": [w, h]},
                "r": {"a": 0, "k": min(w, h) * 0.18},
                "nm": "plate_shape",
            }
        )
        shapes.append(
            {
                "ty": "fl",
                "c": {"a": 0, "k": [pr, pg, pb, 1.0]},
                "o": {"a": 0, "k": 100},
                "r": 1,
                "bm": 0,
                "nm": "plate_fill",
            }
        )

    letter_items = _render_swex_named_letters(
        renderer,
        (cx - w * 0.42, cy - h * 0.36, cx + w * 0.42, cy + h * 0.36),
    )
    if not letter_items:
        return False
    fill = {
        "ty": "fl",
        "c": {"a": 0, "k": [tr_, tg_, tb_, 1.0]},
        "o": {"a": 0, "k": 100},
        "r": 1,
        "bm": 0,
        "nm": "Fill",
    }
    text_group = {
        "ty": "gr",
        "nm": "swex_text",
        "it": [*letter_items, fill, _identity_tr()],
    }

    layer = {
        "ddd": 0,
        "ind": 999999,
        "ty": 4,
        "nm": "swex_overlay",
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [0, 0, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "ao": 0,
        "shapes": [*shapes, text_group],
        "ip": float(tree.get("ip", 0)),
        "op": float(tree.get("op", 60)),
        "st": 0,
        "bm": 0,
    }
    layers = tree.setdefault("layers", [])
    layers.insert(0, layer)
    return True


def _add_fallback_swex_layer(tree: dict, renderer: FontRenderer) -> bool:
    """Insert a top-level shape layer with named s/w/e/x items at canvas centre.

    For stickers that don't contain any text placeholder of their own: the
    runtime replacer (services/swex/replacer.py) scans for sibling items named
    s, w, e, x to know where to render the user's nickname. Without them the
    sticker shows up in the pack but the nickname can't be inserted — which
    visually means the original art with no personalization. Adding a small
    centred overlay guarantees runtime substitution works on every tile.
    """
    canvas_w = float(tree.get("w", 512))
    canvas_h = float(tree.get("h", 512))
    cx = canvas_w / 2.0
    cy = canvas_h / 2.0
    # Target rectangle ~30% of canvas wide, ~10% tall — keeps the letters
    # readable but small enough not to dominate the original art.
    tw = canvas_w * 0.3
    th = canvas_h * 0.1
    bbox = (cx - tw / 2.0, cy - th / 2.0, cx + tw / 2.0, cy + th / 2.0)
    letter_items = _render_swex_named_letters(renderer, bbox)
    if not letter_items:
        return False
    fill = _fill_color([1.0, 1.0, 1.0, 1.0])
    fill["nm"] = "Fill"
    layer = {
        "ddd": 0,
        "ind": 999999,
        "ty": 4,
        "nm": "swex_fallback",
        "sr": 1,
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [0, 0, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [100, 100, 100]},
        },
        "ao": 0,
        "shapes": [*letter_items, fill],
        "ip": float(tree.get("ip", 0)),
        "op": float(tree.get("op", 60)),
        "st": 0,
        "bm": 0,
    }
    layers = tree.setdefault("layers", [])
    # Insert as topmost (index 0) so it draws above the art.
    layers.insert(0, layer)
    return True


def _delete_all_text_runs(tree: dict) -> int:
    """Remove every text-like sibling run reachable in the tree.

    Used by FORCE_OVERLAY stickers: we don't trust any positional replacement
    on them, so we strip every detected letter cluster and rely on a separate
    top-level overlay for the swex container.

    Uses TWO passes:
      1. The strict `find_all_text_runs` detector (canvas-aware filters).
      2. An aggressive structural scan: ANY parent list with ≥5 consecutive
         text-like grs gets that whole run deleted, no scoring/canvas-size
         filters applied. Catches the pink-woman "ангельский" where animated
         per-letter sub-grs blow past the strict detector's height/consistency
         caps.
    """
    deleted = 0
    # Pass 1: strict detector
    runs = find_all_text_runs(tree)
    parent_to_idxs: dict[int, set[int]] = {}
    seen_parents: dict[int, list] = {}
    for _score, parent, idxs, _ in runs:
        seen_parents.setdefault(id(parent), parent)
        parent_to_idxs.setdefault(id(parent), set()).update(idxs)
    for pid, idxset in parent_to_idxs.items():
        parent = seen_parents[pid]
        for i in sorted(idxset, reverse=True):
            if 0 <= i < len(parent):
                del parent[i]
                deleted += 1

    # Pass 2: aggressive structural scan — find any sibling list with ≥5
    # consecutive text-like grs and wipe them. Run AFTER pass 1 so we don't
    # double-delete.
    def visit(node):
        nonlocal deleted
        if isinstance(node, list):
            for c in node:
                visit(c)
            return
        if not isinstance(node, dict):
            return
        for k in ("layers", "assets", "shapes", "it"):
            v = node.get(k)
            if not isinstance(v, list):
                continue
            # Find runs of consecutive text-like grs (≥5 in a row).
            run_idxs: list[int] = []
            runs_to_delete: list[list[int]] = []
            for i, c in enumerate(v):
                if isinstance(c, dict) and c.get("ty") == "gr" and _is_text_like_gr(c):
                    run_idxs.append(i)
                else:
                    if len(run_idxs) >= 5:
                        runs_to_delete.append(run_idxs)
                    run_idxs = []
            if len(run_idxs) >= 5:
                runs_to_delete.append(run_idxs)
            # Apply deletions in reverse so indices stay valid.
            for run in reversed(runs_to_delete):
                for i in sorted(run, reverse=True):
                    if 0 <= i < len(v):
                        del v[i]
                        deleted += 1
            # Recurse into remaining children.
            for c in v:
                if isinstance(c, (list, dict)):
                    visit(c)

    visit(tree)
    return deleted


_POSITIONS_CACHE: dict | None = None


def _get_positions_cache() -> dict:
    global _POSITIONS_CACHE
    if _POSITIONS_CACHE is None:
        _POSITIONS_CACHE = _load_positions_manifest()
    return _POSITIONS_CACHE


def _find_run_near_canvas_point(
    tree: dict,
    target_cx: float,
    target_cy: float,
    max_dist: float = 140.0,
):
    """Pick the text-like run whose canvas-space centre is closest to
    (target_cx, target_cy). Returns (parent_list, idxs, local_bbox) or None.

    Used by the picker-manifest path: the user clicks the position on
    canvas, we use that as a navigation hint to find the matching run and
    replace it IN PLACE (so the swex inherits parent transforms / animations
    / clipping — instead of being a fake overlay plate on top).
    """
    float(tree.get("w", 512))
    float(tree.get("h", 512))
    assets: dict = {}
    for a in tree.get("assets") or []:
        if isinstance(a, dict) and "id" in a:
            assets[a["id"]] = a

    best = None  # (dist, parent_list, idxs, local_bbox)

    def consider(parent_list, sx, sy, ox, oy):
        nonlocal best
        for idxs, kind in _find_runs_in_list(parent_list):
            r = _shape_run_bbox(parent_list, idxs, kind)
            if r is None:
                continue
            local_bbox, _lb = r
            xs = [ox + sx * local_bbox[0], ox + sx * local_bbox[2]]
            ys = [oy + sy * local_bbox[1], oy + sy * local_bbox[3]]
            ccx = (min(xs) + max(xs)) / 2.0
            ccy = (min(ys) + max(ys)) / 2.0
            dist = ((ccx - target_cx) ** 2 + (ccy - target_cy) ** 2) ** 0.5
            if dist > max_dist:
                continue
            # Slight preference for longer runs at the same distance — text
            # placeholders are usually 4+ letters; tie-breaking favours the
            # full word over a 3-letter sub-slice.
            score_key = (dist, -len(idxs))
            if best is None or score_key < best[0]:
                best = (score_key, parent_list, idxs, local_bbox)

    def visit_shape(node, sx, sy, ox, oy, depth=0):
        if depth > 24 or not isinstance(node, dict):
            return
        if node.get("ty") == "gr":
            it = node.get("it") or []
            tr = next((c for c in it if isinstance(c, dict) and c.get("ty") == "tr"), None)
            csx, csy, cox, coy = sx, sy, ox, oy
            if tr is not None:
                csx, csy, cox, coy = _apply_xform(tr, sx, sy, ox, oy)
            consider(it, csx, csy, cox, coy)
            for c in it:
                if c is tr:
                    continue
                visit_shape(c, csx, csy, cox, coy, depth + 1)

    def visit_layers(layers_list, osx, osy, oox, ooy, depth=0):
        if depth > 24 or not layers_list:
            return
        by_ind = {
            L["ind"]: L for L in layers_list if isinstance(L, dict) and L.get("ind") is not None
        }
        cache, resolving = {}, set()

        def resolve(L):
            ind = L.get("ind")
            if ind in cache:
                return cache[ind]
            if ind in resolving:
                return (osx, osy, oox, ooy)
            resolving.add(ind)
            pi = L.get("parent")
            px = resolve(by_ind[pi]) if pi is not None and pi in by_ind else (osx, osy, oox, ooy)
            cache[ind] = _apply_xform(L.get("ks"), *px)
            resolving.discard(ind)
            return cache[ind]

        for L in layers_list:
            if not isinstance(L, dict):
                continue
            sx, sy, ox, oy = resolve(L)
            ty = L.get("ty")
            if ty == 0:
                a = assets.get(L.get("refId"))
                if a:
                    visit_layers(a.get("layers") or [], sx, sy, ox, oy, depth + 1)
            elif ty == 4:
                # Also scan the layer's `shapes` list directly — many text
                # placeholders sit as sibling grs at this level (not wrapped
                # in a parent gr).
                consider(L.get("shapes") or [], sx, sy, ox, oy)
                for sh in L.get("shapes") or []:
                    visit_shape(sh, sx, sy, ox, oy, depth + 1)

    visit_layers(tree.get("layers") or [], 1.0, 1.0, 0.0, 0.0)
    if best is None:
        return None
    return best[1], best[2], best[3]


def _delete_shapes_in_canvas_bbox(
    tree: dict,
    target_bbox: tuple[float, float, float, float],
    overlap_threshold: float = 0.4,
    return_top_parent: bool = False,
):
    """Delete every sh whose canvas-space bbox overlaps target_bbox by at
    least `overlap_threshold` of the shape's own area.

    Returns:
      - default: int count of shapes deleted
      - with `return_top_parent=True`: tuple `(count, top_parent_info)` where
        top_parent_info is a dict `{parent_list, sx, sy, ox, oy, count}`
        describing the parent list with the most deletions, or None if
        nothing was deleted. The caller can use this to insert replacement
        text INTO that same list, so the new text inherits all parent
        transforms / animations / masks.
    """
    canvas_w = float(tree.get("w", 512))
    canvas_h = float(tree.get("h", 512))
    tx0, ty0, tx1, ty1 = target_bbox
    if tx0 > tx1:
        tx0, tx1 = tx1, tx0
    if ty0 > ty1:
        ty0, ty1 = ty1, ty0

    assets: dict = {}
    for a in tree.get("assets") or []:
        if isinstance(a, dict) and "id" in a:
            assets[a["id"]] = a

    deleted = 0
    per_parent: dict[int, dict] = {}

    def shape_bbox_in_canvas(sh, sx, sy, ox, oy):
        verts = _sh_vertices(sh)
        if not verts:
            return None
        cxs = [ox + sx * v[0] for v in verts]
        cys = [oy + sy * v[1] for v in verts]
        return (min(cxs), min(cys), max(cxs), max(cys))

    def overlap(a, b):
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        ix0 = max(ax0, bx0)
        iy0 = max(ay0, by0)
        ix1 = min(ax1, bx1)
        iy1 = min(ay1, by1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        iarea = (ix1 - ix0) * (iy1 - iy0)
        aarea = max(1e-3, (ax1 - ax0) * (ay1 - ay0))
        return iarea / aarea

    def process_list(parent_list, sx, sy, ox, oy):
        nonlocal deleted
        # Delete from the end so indices stay valid.
        for i in range(len(parent_list) - 1, -1, -1):
            c = parent_list[i]
            if not isinstance(c, dict):
                continue
            ty = c.get("ty")
            if ty == "sh":
                bb = shape_bbox_in_canvas(c, sx, sy, ox, oy)
                if bb is None:
                    continue
                w = bb[2] - bb[0]
                h = bb[3] - bb[1]
                # Hard size guard: never delete shapes bigger than 60% of canvas.
                if w > canvas_w * 0.6 or h > canvas_h * 0.6:
                    continue
                # Letter-size guard: a real letter shape should fit inside
                # the user's target bbox. Relaxed to 2.2× to catch wider
                # stylised glyphs (cursive "ангельский" has letters whose
                # bbox is ~1.8× of one cell). Background shapes are still
                # protected by the 60%-of-canvas rule above.
                t_w = target_bbox[2] - target_bbox[0]
                t_h = target_bbox[3] - target_bbox[1]
                shape_area = w * h
                target_area = max(1.0, t_w * t_h)
                if shape_area > target_area * 2.2:
                    continue
                if overlap(bb, target_bbox) >= overlap_threshold:
                    del parent_list[i]
                    deleted += 1
                    info = per_parent.setdefault(
                        id(parent_list),
                        {
                            "parent_list": parent_list,
                            "sx": sx,
                            "sy": sy,
                            "ox": ox,
                            "oy": oy,
                            "count": 0,
                        },
                    )
                    info["count"] += 1
            elif ty == "gr":
                # Recurse, but with the gr's tr applied to the affine.
                it = c.get("it") or []
                tr = next((x for x in it if isinstance(x, dict) and x.get("ty") == "tr"), None)
                csx, csy, cox, coy = sx, sy, ox, oy
                if tr is not None:
                    csx, csy, cox, coy = _apply_xform(tr, sx, sy, ox, oy)
                process_list(it, csx, csy, cox, coy)
                # If the gr is now empty of sh, leave it — we don't want
                # to accidentally remove transform-only grs that other
                # siblings depend on.

    def visit_layers(layers_list, osx, osy, oox, ooy, depth=0):
        if depth > 24 or not layers_list:
            return
        by_ind = {
            L["ind"]: L for L in layers_list if isinstance(L, dict) and L.get("ind") is not None
        }
        cache, resolving = {}, set()

        def resolve(L):
            ind = L.get("ind")
            if ind in cache:
                return cache[ind]
            if ind in resolving:
                return (osx, osy, oox, ooy)
            resolving.add(ind)
            pi = L.get("parent")
            px = resolve(by_ind[pi]) if pi is not None and pi in by_ind else (osx, osy, oox, ooy)
            cache[ind] = _apply_xform(L.get("ks"), *px)
            resolving.discard(ind)
            return cache[ind]

        for L in layers_list:
            if not isinstance(L, dict):
                continue
            sx, sy, ox, oy = resolve(L)
            ty = L.get("ty")
            if ty == 0:
                a = assets.get(L.get("refId"))
                if a:
                    visit_layers(a.get("layers") or [], sx, sy, ox, oy, depth + 1)
            elif ty == 4:
                process_list(L.get("shapes") or [], sx, sy, ox, oy)

    visit_layers(tree.get("layers") or [], 1.0, 1.0, 0.0, 0.0)
    if return_top_parent:
        top = max(per_parent.values(), key=lambda v: v["count"]) if per_parent else None
        return deleted, top
    return deleted


def _insert_swex_inplace(
    parent_info: dict,
    target_canvas_bbox: tuple[float, float, float, float],
    renderer: FontRenderer,
    text_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> bool:
    """Insert s/w/e/x as a dedicated nested gr — matches master_pack's
    convention exactly so the runtime finds a clean isolated swex container.

    master_pack structure (verified on 001_f09f8ead):
        gr "container" [
          gr "EMOJI" [
            gr "" [ sh:s, sh:w, gr:e, sh:x, tr ]   ← THIS is the swex container
            tr
          ]
          fl
          tr
        ]

    We replicate the innermost `gr [s, w, e, x, tr]` here and insert it into
    the parent_list (which is typically a `gr.it` next to a fill / tr). The
    runtime's `find_swex_containers` walks any list with all four letter
    names as siblings — wrapping them in their own gr means the runtime
    sees ONLY those four items, not mixed with sticker geometry.
    """
    parent_list = parent_info["parent_list"]
    sx = parent_info["sx"]
    sy = parent_info["sy"]
    ox = parent_info["ox"]
    oy = parent_info["oy"]
    if abs(sx) < 1e-6 or abs(sy) < 1e-6:
        return False
    cx0, cy0, cx1, cy1 = target_canvas_bbox
    lx0 = (cx0 - ox) / sx
    ly0 = (cy0 - oy) / sy
    lx1 = (cx1 - ox) / sx
    ly1 = (cy1 - oy) / sy
    local_bbox = (min(lx0, lx1), min(ly0, ly1), max(lx0, lx1), max(ly0, ly1))
    letter_items = _render_swex_named_letters(renderer, local_bbox)
    if not letter_items:
        return False

    # Build the dedicated swex gr — mirrors master_pack/001_f09f8ead exactly:
    # gr.it = [sh:s, sh:w, gr:e, sh:x, identity-tr]
    swex_gr = {
        "ty": "gr",
        "nm": "swex",
        "it": [*letter_items, _identity_tr()],
    }

    tr_, tg_, tb_ = text_color
    fill = {
        "ty": "fl",
        "c": {"a": 0, "k": [tr_, tg_, tb_, 1.0]},
        "o": {"a": 0, "k": 100},
        "r": 1,
        "bm": 0,
        "nm": "Fill",
    }

    # Insert BEFORE the trailing `tr` so we don't break the Lottie
    # convention (last item must be the parent's transform).
    insert_idx = len(parent_list)
    for j in range(len(parent_list) - 1, -1, -1):
        item = parent_list[j]
        if isinstance(item, dict) and item.get("ty") == "tr":
            insert_idx = j
            break
        if isinstance(item, dict) and item.get("ty") in ("sh", "gr", "fl", "st", "rc", "el", "rd"):
            break

    # Check if parent already has a fill — if not, add our white one so
    # the user's nick is visible.
    has_fill = any(isinstance(c, dict) and c.get("ty") == "fl" for c in parent_list)
    insertion = [swex_gr] if has_fill else [swex_gr, fill]
    for offset, item in enumerate(insertion):
        parent_list.insert(insert_idx + offset, item)
    return True


def _ocr_replace(tree: dict, renderer: FontRenderer, sticker_id: str) -> int:
    """Run OCR on the sticker's preview, delete shapes inside every detected
    text bbox, then drop a swex container at the largest bbox's centre.

    Returns 1 if at least one bbox was processed, 0 if OCR found nothing.
    """
    bboxes = _ocr_text_bboxes(sticker_id)
    if not bboxes:
        return 0
    primary = max(bboxes, key=lambda b: b["w"] * b["h"])
    (
        primary["cx"] - primary["w"] / 2.0,
        primary["cy"] - primary["h"] / 2.0,
        primary["cx"] + primary["w"] / 2.0,
        primary["cy"] + primary["h"] / 2.0,
    )
    primary_top = None
    for b in bboxes:
        target = (
            b["cx"] - b["w"] / 2.0,
            b["cy"] - b["h"] / 2.0,
            b["cx"] + b["w"] / 2.0,
            b["cy"] + b["h"] / 2.0,
        )
        pad_w = b["w"] * 0.10
        pad_h = b["h"] * 0.25
        target = (target[0] - pad_w, target[1] - pad_h, target[2] + pad_w, target[3] + pad_h)
        if b is primary:
            _, primary_top = _delete_shapes_in_canvas_bbox(
                tree,
                target,
                overlap_threshold=0.35,
                return_top_parent=True,
            )
        else:
            _delete_shapes_in_canvas_bbox(tree, target, overlap_threshold=0.35)
    # In-place insertion preferred — inherits parent transforms.
    cx, cy = primary["cx"], primary["cy"]
    w = max(120.0, primary["w"] * 1.05)
    h = max(40.0, primary["h"] * 1.10)
    place_target = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    if primary_top is not None and _insert_swex_inplace(primary_top, place_target, renderer):
        return 1
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
    return 1


def _rename_letters_to_swex(parent_list: list, idxs: list[int]) -> bool:
    """Rename the existing letter shapes at `idxs` to s/w/e/x.

    The simplest replacement strategy: instead of deleting the placeholder
    letters and inserting fresh swex glyphs (which loses transforms, masks,
    animation, position), we just RENAME them. The runtime's
    `find_swex_containers` looks for any list where four siblings are named
    s/w/e/x and renders the user's nick in their union bbox — which is
    exactly where the original placeholder lived.

    For N letters we pick 4 spread evenly across the run so the union bbox
    matches the original placeholder's full extent (not just the first 4
    letters). Extra letters get nm cleared so the runtime ignores them.
    """
    n = len(idxs)
    if n < 4:
        return False
    # Pick 4 indices spread evenly across the run.
    pick = [
        idxs[0],
        idxs[max(1, n // 3)],
        idxs[max(2, 2 * n // 3)],
        idxs[n - 1],
    ]
    # Dedupe in case n is tiny (e.g., n=4 → pick already unique).
    seen = []
    for i in pick:
        if i not in seen:
            seen.append(i)
    while len(seen) < 4 and len(seen) < n:
        # fill from middle if dedupe collapsed us
        for i in idxs:
            if i not in seen:
                seen.append(i)
                if len(seen) >= 4:
                    break
    if len(seen) < 4:
        return False
    # Safety: only rename if all 4 picked nodes are sh OR gr-with-trailing-tr.
    # Otherwise Lottie's restructure pass crashes ("Group has no to_matrix")
    # because we'd be turning a gr without a tr into a "letter container".
    for i in seen:
        node = parent_list[i]
        if not isinstance(node, dict):
            return False
        ty = node.get("ty")
        if ty == "sh":
            continue
        if ty == "gr":
            it = node.get("it") or []
            has_trailing_tr = it and isinstance(it[-1], dict) and it[-1].get("ty") == "tr"
            if not has_trailing_tr:
                return False
            continue
        return False
    names = ["s", "w", "e", "x"]
    for i, name in zip(seen, names, strict=False):
        parent_list[i]["nm"] = name
    # DELETE the leftover letters that aren't named s/w/e/x. Just clearing
    # their nm leaves them visible — for "ангельский" we'd see 6 leftover
    # letters next to our 4 named ones. Reverse-iterate so indices stay valid.
    pick_set = set(seen)
    for i in sorted(idxs, reverse=True):
        if i in pick_set:
            continue
        if 0 <= i < len(parent_list):
            del parent_list[i]
    return True


def replace_text_run(
    tree: dict, renderer: FontRenderer, text: str = "swex", sticker_id: str | None = None
) -> int:
    # PRIORITY 1: picker manifest if the user marked anything explicitly.
    # PRIORITY 2: OCR — auto-find text in the rendered preview, delete shapes
    # inside the bbox, drop swex on top. Works for ~50% of stickers; the
    # remaining ones fall through to the heuristic detector below.
    # PRIORITY 3: heuristic single-best detection (in-place sh replacement).
    # PRIORITY 4: fallback — central overlay so the runtime can always
    # substitute the user's nick (even if it lands in a weird spot).
    # docstring marker, real branches below.

    # PRIORITY -1: try renaming existing letter shapes to s/w/e/x.
    # This is the cleanest approach — keeps the original letters' position,
    # transforms, masks, animation; runtime just substitutes the user's
    # nick into the union bbox of those 4 names. Skips deletion/insertion
    # entirely.
    runs = find_all_text_runs(tree)
    if runs:
        # Use the highest-scoring run as the canonical placeholder.
        _best_score, best_parent, best_idxs, _ = max(runs, key=lambda r: r[0])
        if _rename_letters_to_swex(best_parent, best_idxs):
            # Also delete duplicate runs (same word baked in another layer)
            # so the user's nick doesn't render alongside the original.
            best_pid = id(best_parent)
            for _, parent, idxs, _ in runs:
                if id(parent) == best_pid:
                    continue
                for i in sorted(idxs, reverse=True):
                    if 0 <= i < len(parent):
                        del parent[i]
            return 1

    # PRIORITY 0: hardcoded MANUAL_BBOX overrides.
    # Delete shapes inside the bbox AND insert swex INTO the same parent
    # list where the most deletions happened — so the new text inherits
    # the original layer's transforms / animations / masks (looks native,
    # not pasted on top). Falls back to a top-level overlay only if we
    # couldn't find a parent to inject into.
    if sticker_id in MANUAL_BBOX:
        cx, cy, w, h = MANUAL_BBOX[sticker_id]
        target = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
        _, top_parent = _delete_shapes_in_canvas_bbox(
            tree,
            target,
            overlap_threshold=0.30,
            return_top_parent=True,
        )
        for ex_cx, ex_cy, ex_w, ex_h in EXTRA_DELETE_BBOXES.get(sticker_id, []):
            ex_target = (
                ex_cx - ex_w / 2.0,
                ex_cy - ex_h / 2.0,
                ex_cx + ex_w / 2.0,
                ex_cy + ex_h / 2.0,
            )
            _delete_shapes_in_canvas_bbox(tree, ex_target, overlap_threshold=0.30)
        # Try in-place insertion FIRST so it inherits parent transforms.
        if top_parent is not None:
            ok = _insert_swex_inplace(top_parent, target, renderer)
            if ok:
                return 1
        # Fallback: top-level overlay (no plate).
        ok = _add_overlay_with_plate(
            tree,
            renderer,
            cx,
            cy,
            w,
            h,
            plate=False,
            text_color=(1.0, 1.0, 1.0),
        )
        return 1 if ok else 0

    # PRIORITY 1: picker manifest — explicit user override.
    # Two paths:
    #   plate=True  → opaque rectangle + text on top (sticker animation
    #                  underneath, original text still there but covered)
    #   plate=False → DELETE all shapes inside the user's bbox, then add a
    #                  top-level layer with text at the bbox centre. No
    #                  plate visible; original text is removed completely.
    positions = _get_positions_cache()
    if sticker_id and sticker_id in positions:
        p = positions[sticker_id]
        try:
            cx = float(p["cx"])
            cy = float(p["cy"])
            w = float(p.get("w", 200))
            h = float(p.get("h", 60))
            plate = bool(p.get("plate", False))
            plate_color = _hex_to_rgb(str(p.get("plate_color", "#000000")))
            text_color = _hex_to_rgb(str(p.get("text_color", "#ffffff")))

            if not plate:
                # Strip every letter-sized shape inside the user's bbox.
                target = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
                _delete_shapes_in_canvas_bbox(tree, target)
                # Then drop the swex text on top (no plate). Renders above
                # everything so user's nickname is always visible.
                ok = _add_overlay_with_plate(
                    tree,
                    renderer,
                    cx,
                    cy,
                    w,
                    h,
                    plate=False,
                    text_color=text_color,
                )
                return 1 if ok else 0

            # plate=True → opaque rectangle + text on top.
            ok = _add_overlay_with_plate(
                tree,
                renderer,
                cx,
                cy,
                w,
                h,
                plate=True,
                plate_color=plate_color,
                text_color=text_color,
            )
            return 1 if ok else 0
        except Exception as e:
            print(f"  manifest entry for {sticker_id} invalid: {e}")

    # PRIORITY 2: OCR auto-detect (no manifest, no special list).
    if sticker_id:
        try:
            n = _ocr_replace(tree, renderer, sticker_id)
            if n > 0:
                return n
        except Exception as e:
            print(f"  OCR fail for {sticker_id}: {e}")

    if sticker_id in FORCE_OVERLAY:
        # Hardline path: strip every detected text run, then let the caller
        # add a fallback overlay. Skips the usual best-candidate flow because
        # for these stickers the "best" position is invisible or competes
        # with other always-rendered geometry (watch numbers, bomb circle).
        _delete_all_text_runs(tree)
        # Return 0 so process_one triggers _add_fallback_swex_layer for us.
        return 0

    if sticker_id in REPLACE_ALL_RUNS:
        # Stickers with the placeholder baked twice — explicit opt-in
        # because aggressive deletion elsewhere clobbers legitimate
        # geometry (truck windows, headphone cups, frog limbs).
        runs = find_all_text_runs(tree)
        by_parent: dict[int, tuple] = {}
        for score, parent, idxs, bbox in runs:
            cur = by_parent.get(id(parent))
            if cur is None or score > cur[0]:
                by_parent[id(parent)] = (score, parent, idxs, bbox)
        if not by_parent:
            return 0
        ranked = sorted(by_parent.values(), key=lambda x: x[0], reverse=True)
        _, primary_parent, primary_idxs, primary_bbox = ranked[0]
        replaced = 0
        if _do_replace(primary_parent, primary_idxs, primary_bbox, renderer):
            replaced += 1
        primary_pid = id(primary_parent)
        for _, parent, idxs, _ in ranked[1:]:
            if id(parent) == primary_pid:
                continue
            for i in sorted(idxs, reverse=True):
                if 0 <= i < len(parent):
                    del parent[i]
        return replaced

    # DEFAULT behaviour: single best run wins. Anything else is left alone
    # so we don't mutilate intricate stickers whose details happen to
    # cluster like text. Trade-off: a few stickers may show leftover
    # placeholder text alongside the user's nick — fix those by adding their
    # IDs to REPLACE_ALL_RUNS one at a time.
    found = find_text_run(tree, sticker_id=sticker_id)
    if found is None:
        return 0
    parent_list, idxs, bbox = found
    return 1 if _do_replace(parent_list, idxs, bbox, renderer) else 0


def process_one(src: Path, orig_dir: Path, out_dir: Path, renderer: FontRenderer) -> dict:
    info = {"file": src.name}
    orig_target = orig_dir / src.name
    if not orig_target.exists():
        shutil.copy2(src, orig_target)
    info["copied"] = True

    tree = _load(src)
    replaced = replace_text_run(tree, renderer, "swex", sticker_id=src.stem)
    # No designer placeholder: add a centred overlay so the runtime replacer
    # still has something to personalise.
    if replaced == 0 and _add_fallback_swex_layer(tree, renderer):
        info["fallback"] = True
    info["replaced"] = replaced

    out_target = out_dir / src.name
    _save(out_target, tree)
    info["bytes"] = out_target.stat().st_size
    return info


def main():
    ORIG_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    PREVIEW_DIR.mkdir(exist_ok=True)
    if not SRC_DIR.exists():
        print(f"Source folder not found: {SRC_DIR}")
        return
    renderer = FontRenderer(FONT_PATH)
    files = sorted(SRC_DIR.glob("*.tgs"))
    print(f"Processing {len(files)} new stickers...")
    ok = 0
    no_text = []
    for f in files:
        try:
            info = process_one(f, ORIG_DIR, OUT_DIR, renderer)
            ok += 1
            tag = "swex" if info["replaced"] else "AS-IS"
            print(f"  {tag:6s} {f.name}  ({info['bytes']}b)")
            if not info["replaced"]:
                no_text.append(f.name)
        except Exception as e:
            print(f"  FAIL   {f.name}: {e}")
            import traceback

            traceback.print_exc()
    print()
    print(
        f"Processed {ok}/{len(files)} stickers, {len(no_text)} kept as-is (no text run detected)."
    )
    if no_text:
        print("As-is files:")
        for n in no_text:
            print(" ", n)
    print()
    print("Rendering previews...")
    new_ids = {p.stem for p in SRC_DIR.glob("*.tgs")}
    for f in sorted(OUT_DIR.glob("*.tgs")):
        if f.stem not in new_ids:
            continue
        out_png = PREVIEW_DIR / (f.stem + ".png")
        try:
            render_png_preview(f, out_png)
            print(f"  preview {f.name}")
        except Exception as e:
            print(f"  PREVIEW FAIL {f.name}: {e}")


if __name__ == "__main__":
    main()
