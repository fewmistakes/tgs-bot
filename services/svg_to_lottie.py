"""Convert an SVG file to a list of Lottie 'sh' shape items.

We parse path d-attributes via svgpathtools, walk the SVG tree applying
nested transforms, and emit one Lottie 'sh' item per subpath. Fills /
strokes are dropped — the surrounding TGS layer's fill applies to the
inserted shapes (single-colour logo, integrated with the master pack
artwork colour).

Y axis: SVG is Y-down, same as Lottie — no flip required.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import svgpathtools

_Bbox = tuple[float, float, float, float]
_Mat = tuple[float, float, float, float, float, float]
_IDENTITY: _Mat = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_viewbox(root: ET.Element) -> _Bbox:
    vb = root.attrib.get("viewBox")
    if vb:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) == 4:
            try:
                x, y, w, h = (float(p) for p in parts)
                return (x, y, x + w, y + h)
            except ValueError:
                pass

    # fallback: width/height
    def _num(s: str | None, default: float) -> float:
        if not s:
            return default
        m = re.match(r"^([0-9.+-]+)", s.strip())
        return float(m.group(1)) if m else default

    w = _num(root.attrib.get("width"), 100.0)
    h = _num(root.attrib.get("height"), 100.0)
    return (0.0, 0.0, w, h)


def _parse_transform(s: str) -> _Mat:
    """Parse SVG transform list into a 2D matrix (a b c d e f).

    Composed left-to-right (transforms apply to children: parent * local).
    Supports translate, scale, rotate, matrix. Skew not implemented.
    """
    a, b, c, d, e, f = _IDENTITY
    if not s:
        return a, b, c, d, e, f
    for m in re.finditer(r"(\w+)\s*\(([^)]+)\)", s):
        op = m.group(1).lower()
        nums = [float(x) for x in re.split(r"[\s,]+", m.group(2).strip()) if x]
        la, lb, lc, ld, le, lf = _IDENTITY
        if op == "translate":
            le = nums[0] if nums else 0.0
            lf = nums[1] if len(nums) > 1 else 0.0
        elif op == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            la, ld = sx, sy
        elif op == "rotate":
            import math

            theta = math.radians(nums[0]) if nums else 0.0
            ca, sa = math.cos(theta), math.sin(theta)
            la, lb, lc, ld = ca, sa, -sa, ca
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                # rotate around (cx,cy) = T(cx,cy) * R * T(-cx,-cy)
                # Compose into local matrix
                le = cx - (la * cx + lc * cy)
                lf = cy - (lb * cx + ld * cy)
        elif op == "matrix" and len(nums) == 6:
            la, lb, lc, ld, le, lf = nums
        # parent (a..f) ∘ local (la..lf)
        na = a * la + c * lb
        nb = b * la + d * lb
        nc = a * lc + c * ld
        nd = b * lc + d * ld
        ne = a * le + c * lf + e
        nf = b * le + d * lf + f
        a, b, c, d, e, f = na, nb, nc, nd, ne, nf
    return a, b, c, d, e, f


def _compose(parent: _Mat, child: _Mat) -> _Mat:
    pa, pb, pc, pd, pe, pf = parent
    la, lb, lc, ld, le, lf = child
    return (
        pa * la + pc * lb,
        pb * la + pd * lb,
        pa * lc + pc * ld,
        pb * lc + pd * ld,
        pa * le + pc * lf + pe,
        pb * le + pd * lf + pf,
    )


def _apply(t: _Mat, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = t
    return a * x + c * y + e, b * x + d * y + f


def svg_to_lottie_shapes(svg_bytes: bytes) -> tuple[list[dict], _Bbox]:
    """Parse SVG → (shapes, bbox).

    `shapes` is a list of Lottie 'sh' dicts. `bbox` is the union of all
    vertex points actually emitted (in transformed SVG coordinates).
    Returns ([], (0,0,0,0)) if the SVG has no recognisable paths.
    """
    text = svg_bytes.decode("utf-8", errors="ignore").lstrip("﻿")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], (0.0, 0.0, 0.0, 0.0)

    shapes: list[dict] = []
    pts: list[tuple[float, float]] = []

    def walk(el: ET.Element, parent_t: _Mat) -> None:
        tr = el.attrib.get("transform", "")
        t = _compose(parent_t, _parse_transform(tr)) if tr else parent_t
        tag = _strip_ns(el.tag)
        if tag == "path":
            d = el.attrib.get("d")
            if d:
                try:
                    path = svgpathtools.parse_path(d)
                except Exception:
                    return
                _emit_path(path, t, shapes, pts)
        elif tag == "rect":
            try:
                x = float(el.attrib.get("x", 0))
                y = float(el.attrib.get("y", 0))
                w = float(el.attrib.get("width", 0))
                h = float(el.attrib.get("height", 0))
                if w > 0 and h > 0:
                    d = f"M{x},{y} L{x + w},{y} L{x + w},{y + h} L{x},{y + h} Z"
                    _emit_path(svgpathtools.parse_path(d), t, shapes, pts)
            except (ValueError, KeyError):
                pass
        elif tag in ("circle", "ellipse"):
            try:
                cx = float(el.attrib.get("cx", 0))
                cy = float(el.attrib.get("cy", 0))
                if tag == "circle":
                    r = float(el.attrib.get("r", 0))
                    rx = ry = r
                else:
                    rx = float(el.attrib.get("rx", 0))
                    ry = float(el.attrib.get("ry", 0))
                if rx > 0 and ry > 0:
                    # 4-arc cubic approximation of an ellipse
                    k = 0.5522847498
                    d = (
                        f"M{cx - rx},{cy} "
                        f"C{cx - rx},{cy - ry * k} {cx - rx * k},{cy - ry} {cx},{cy - ry} "
                        f"C{cx + rx * k},{cy - ry} {cx + rx},{cy - ry * k} {cx + rx},{cy} "
                        f"C{cx + rx},{cy + ry * k} {cx + rx * k},{cy + ry} {cx},{cy + ry} "
                        f"C{cx - rx * k},{cy + ry} {cx - rx},{cy + ry * k} {cx - rx},{cy} Z"
                    )
                    _emit_path(svgpathtools.parse_path(d), t, shapes, pts)
            except (ValueError, KeyError):
                pass
        for child in el:
            walk(child, t)

    walk(root, _IDENTITY)
    if not pts:
        return [], (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return shapes, (min(xs), min(ys), max(xs), max(ys))


def _emit_path(path, t: _Mat, shapes: list, pts: list) -> None:
    """Walk an svgpathtools Path → emit one Lottie 'sh' per subpath."""
    cur_v: list[list[float]] = []
    cur_i: list[list[float]] = []
    cur_o: list[list[float]] = []
    sub_start: tuple[float, float] | None = None
    last_pt: tuple[float, float] | None = None

    def _flush() -> None:
        nonlocal cur_v, cur_i, cur_o, sub_start, last_pt
        if not cur_v:
            return
        closed = False
        if (
            sub_start is not None
            and last_pt is not None
            and len(cur_v) >= 2
            and abs(last_pt[0] - sub_start[0]) < 1e-3
            and abs(last_pt[1] - sub_start[1]) < 1e-3
        ):
            # Last vertex equals first — drop dup, transfer in-tangent, mark closed
            cur_i[0] = cur_i[-1]
            cur_v.pop()
            cur_i.pop()
            cur_o.pop()
            closed = True
        shapes.append(
            {
                "ty": "sh",
                "ks": {
                    "a": 0,
                    "k": {
                        "v": cur_v,
                        "i": cur_i,
                        "o": cur_o,
                        "c": closed,
                    },
                },
            }
        )
        cur_v = []
        cur_i = []
        cur_o = []
        sub_start = None
        last_pt = None

    for seg in path:
        sx, sy = seg.start.real, seg.start.imag
        ex, ey = seg.end.real, seg.end.imag
        sxt, syt = _apply(t, sx, sy)
        ext, eyt = _apply(t, ex, ey)
        # New subpath if segment doesn't start where the last one ended
        if last_pt is None or (abs(sxt - last_pt[0]) > 1e-3 or abs(syt - last_pt[1]) > 1e-3):
            _flush()
            cur_v.append([sxt, syt])
            cur_i.append([0.0, 0.0])
            cur_o.append([0.0, 0.0])
            sub_start = (sxt, syt)
            pts.append((sxt, syt))
        # Cubic control points (always cubic in Lottie)
        if isinstance(seg, svgpathtools.CubicBezier):
            c1 = (seg.control1.real, seg.control1.imag)
            c2 = (seg.control2.real, seg.control2.imag)
        elif isinstance(seg, svgpathtools.QuadraticBezier):
            ctrl = (seg.control.real, seg.control.imag)
            # Q → C: P1 = P0 + 2/3*(Q-P0); P2 = P3 + 2/3*(Q-P3)
            c1 = (sx + 2 / 3 * (ctrl[0] - sx), sy + 2 / 3 * (ctrl[1] - sy))
            c2 = (ex + 2 / 3 * (ctrl[0] - ex), ey + 2 / 3 * (ctrl[1] - ey))
        elif isinstance(seg, svgpathtools.Arc):
            # Approximate arc as a straight line — Kittl flattens arcs to
            # cubics already, so this branch rarely triggers.
            c1 = (sx, sy)
            c2 = (ex, ey)
        else:  # Line and anything else
            c1 = (sx, sy)
            c2 = (ex, ey)
        c1t = _apply(t, c1[0], c1[1])
        c2t = _apply(t, c2[0], c2[1])
        prev = cur_v[-1]
        cur_o[-1] = [c1t[0] - prev[0], c1t[1] - prev[1]]
        cur_v.append([ext, eyt])
        cur_i.append([c2t[0] - ext, c2t[1] - eyt])
        cur_o.append([0.0, 0.0])
        pts.append((ext, eyt))
        last_pt = (ext, eyt)

    _flush()
