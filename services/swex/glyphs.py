"""Render text as Lottie shape items using a TrueType font.

Lottie path format reminder:
    {"ty":"sh", "ks":{"a":0, "k":{"v":[...vertices], "i":[...in tangents],
                                  "o":[...out tangents], "c": bool}}}
Tangents are RELATIVE to their vertex (Lottie convention),
opposite of SVG which uses absolute control points.
"""

from dataclasses import dataclass
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

# Lottie expects Y-down; TrueType is Y-up — we flip Y at the end.


class _LottiePathPen(BasePen):
    """Collect cubic-bezier subpaths and emit them as Lottie 'sh' dicts."""

    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.subpaths: list[dict] = []
        self._cur: dict | None = None
        self._first_pt: tuple[float, float] | None = None
        self._last_pt: tuple[float, float] | None = None

    def _start(self, pt):
        self._cur = {"v": [], "i": [], "o": [], "c": False}
        self._first_pt = pt
        self._cur["v"].append([pt[0], pt[1]])
        self._cur["i"].append([0.0, 0.0])
        self._cur["o"].append([0.0, 0.0])
        self._last_pt = pt

    def _moveTo(self, pt):
        if self._cur is not None:
            self.subpaths.append(self._cur)
        self._start(pt)

    def _lineTo(self, pt):
        # straight line: zero-length tangents
        self._cur["v"].append([pt[0], pt[1]])
        self._cur["i"].append([0.0, 0.0])
        self._cur["o"].append([0.0, 0.0])
        # previous vertex's "out" stays zero
        self._last_pt = pt

    def _curveToOne(self, pt1, pt2, pt3):
        # cubic bezier from _last_pt to pt3, controls pt1, pt2
        # set previous vertex's out = pt1 - prev
        prev = self._cur["v"][-1]
        self._cur["o"][-1] = [pt1[0] - prev[0], pt1[1] - prev[1]]
        self._cur["v"].append([pt3[0], pt3[1]])
        self._cur["i"].append([pt2[0] - pt3[0], pt2[1] - pt3[1]])
        self._cur["o"].append([0.0, 0.0])
        self._last_pt = pt3

    def _qCurveToOne(self, pt1, pt2):
        # Promote quadratic (P0, pt1, pt2) to cubic by the standard
        # one-control-point formula: C1 = P0 + 2/3*(pt1 - P0), C2 = pt2 + 2/3*(pt1 - pt2).
        prev = self._cur["v"][-1]
        c1 = (prev[0] + 2 / 3 * (pt1[0] - prev[0]), prev[1] + 2 / 3 * (pt1[1] - prev[1]))
        c2 = (pt2[0] + 2 / 3 * (pt1[0] - pt2[0]), pt2[1] + 2 / 3 * (pt1[1] - pt2[1]))
        self._curveToOne(c1, c2, pt2)

    def _closePath(self):
        if self._cur is None:
            return
        # if last vertex equals first, drop the duplicate
        if (
            self._first_pt is not None
            and len(self._cur["v"]) >= 2
            and self._cur["v"][-1] == [self._first_pt[0], self._first_pt[1]]
        ):
            # transfer the 'in' tangent of the duplicate back to the first vertex
            self._cur["i"][0] = self._cur["i"][-1]
            self._cur["v"].pop()
            self._cur["i"].pop()
            self._cur["o"].pop()
        self._cur["c"] = True
        self.subpaths.append(self._cur)
        self._cur = None

    def _endPath(self):
        if self._cur is None:
            return
        self._cur["c"] = False
        self.subpaths.append(self._cur)
        self._cur = None


@dataclass
class Glyph:
    """One rendered glyph: a list of Lottie subpaths plus advance width."""

    subpaths: list[dict]
    advance: float
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    char: str


@dataclass
class TextLayout:
    """Result of laying out a string of glyphs."""

    glyphs: list[Glyph]
    width: float
    height: float
    baseline_y: float  # offset from top of bbox to baseline
    all_y_min: float
    all_y_max: float
    units_per_em: int


class FontRenderer:
    """Loads a TTF and produces Lottie shape data for text strings."""

    def __init__(self, ttf_path: Path | str):
        self.font = TTFont(str(ttf_path))
        self.glyph_set = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.units_per_em = self.font["head"].unitsPerEm
        os2 = self.font["OS/2"]
        self.cap_height = getattr(os2, "sCapHeight", None) or os2.sTypoAscender
        # x-height is the reference for sizing — the original "swex" is lowercase,
        # so we want lowercase letters of the new text to render at the same body height.
        self.x_height = getattr(os2, "sxHeight", None) or self.cap_height

    def _glyph_name(self, char: str) -> str | None:
        cp = ord(char)
        return self.cmap.get(cp)

    def render_glyph(self, char: str) -> Glyph | None:
        name = self._glyph_name(char)
        if name is None or name not in self.glyph_set:
            return None
        gly = self.glyph_set[name]
        recorder = _LottiePathPen(self.glyph_set)
        # Quadratic→cubic happens inside our pen (_qCurveToOne).
        gly.draw(recorder)
        # Y-flip subpaths into Lottie coords (Y-down).
        for sp in recorder.subpaths:
            for arr_key in ("v", "i", "o"):
                for pt in sp[arr_key]:
                    pt[1] = -pt[1]
        # bbox in (now Y-flipped) coords
        all_x: list[float] = []
        all_y: list[float] = []
        for sp in recorder.subpaths:
            for v in sp["v"]:
                all_x.append(v[0])
                all_y.append(v[1])
        bbox = (min(all_x), min(all_y), max(all_x), max(all_y)) if all_x else (0.0, 0.0, 0.0, 0.0)
        advance = gly.width
        return Glyph(subpaths=recorder.subpaths, advance=advance, bbox=bbox, char=char)

    def layout(self, text: str) -> TextLayout:
        glyphs: list[Glyph] = []
        cursor = 0.0
        all_y_min = 0.0
        all_y_max = 0.0
        for ch in text:
            g = self.render_glyph(ch)
            if g is None:
                # missing glyph: treat as space-ish (em/4)
                glyphs.append(
                    Glyph(subpaths=[], advance=self.units_per_em / 4, bbox=(0, 0, 0, 0), char=ch)
                )
                cursor += self.units_per_em / 4
                continue
            # translate this glyph's subpaths by `cursor` in X
            for sp in g.subpaths:
                for arr_key in ("v", "i", "o"):
                    for pt in sp[arr_key]:
                        if arr_key == "v":
                            pt[0] += cursor
            xmin, ymin, xmax, ymax = g.bbox
            g.bbox = (xmin + cursor, ymin, xmax + cursor, ymax)
            all_y_min = min(all_y_min, ymin)
            all_y_max = max(all_y_max, ymax)
            glyphs.append(g)
            cursor += g.advance
        return TextLayout(
            glyphs=glyphs,
            width=cursor,
            height=all_y_max - all_y_min,
            baseline_y=0.0,  # baseline is at y=0 in Y-flipped coords
            all_y_min=all_y_min,
            all_y_max=all_y_max,
            units_per_em=self.units_per_em,
        )

    def render_to_lottie_shapes(
        self, text: str, target_bbox: tuple[float, float, float, float]
    ) -> tuple[list[dict], float]:
        """Render text scaled+positioned to fit into target_bbox (Lottie coords).

        Returns (list of 'sh' shape items, scale_used). Scale shrinks if text is
        wider than target; otherwise stays at the natural cap-height ratio.
        """
        layout = self.layout(text)
        if layout.width <= 0:
            return [], 1.0

        target_x_min, target_y_min, target_x_max, target_y_max = target_bbox
        target_w = target_x_max - target_x_min
        target_h = target_y_max - target_y_min

        # Natural scale: render lowercase letters at the same body-height as the
        # original "swex" lowercase letters. Use x-height as the reference body.
        natural_scale = target_h / self.x_height
        natural_width = layout.width * natural_scale
        # Long nicknames shrink rather than spill past the original glyph bbox.
        scale = target_w / layout.width if natural_width > target_w else natural_scale

        # Final width/height after scaling
        final_w = layout.width * scale
        text_h = layout.height * scale
        # Center horizontally and vertically in target bbox.
        offset_x = target_x_min + (target_w - final_w) / 2.0
        offset_y = (target_y_min + target_y_max) / 2.0 + text_h / 2.0 - layout.all_y_max * scale

        # Round vertex/tangent coords to 2 decimals — Lottie files in the wild
        # don't use full float precision, and full precision blows up JSON size
        # past Telegram's 64KB custom-emoji limit on heavier emojis.
        R = 2
        shapes: list[dict] = []
        for g in layout.glyphs:
            for sp in g.subpaths:
                new_v = [
                    [round(v[0] * scale + offset_x, R), round(v[1] * scale + offset_y, R)]
                    for v in sp["v"]
                ]
                new_i = [[round(t[0] * scale, R), round(t[1] * scale, R)] for t in sp["i"]]
                new_o = [[round(t[0] * scale, R), round(t[1] * scale, R)] for t in sp["o"]]
                shapes.append(
                    {
                        "ty": "sh",
                        "ks": {
                            "a": 0,
                            "k": {
                                "i": new_i,
                                "o": new_o,
                                "v": new_v,
                                "c": sp["c"],
                            },
                        },
                    }
                )
        return shapes, scale
