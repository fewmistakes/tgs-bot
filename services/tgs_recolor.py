"""Recolor a TGS (Lottie) file by walking its JSON tree.

Strategy: replace hue + saturation of every solid fill while preserving
luminance. This keeps the original sticker's highlights / shadows readable
and lets multi-tone artwork adapt naturally to the target colour.
"""

from __future__ import annotations

import colorsys
import gzip
import json


def _replace_color(
    rgb: tuple[float, float, float], target_hsv: tuple[float, float, float]
) -> tuple[float, float, float]:
    r, g, b = rgb
    _h, s, v = colorsys.rgb_to_hsv(r, g, b)
    th, ts, _tv = target_hsv
    # Keep V (brightness). Override H. For grey-ish source pixels
    # don't over-saturate — preserve their proportional saturation.
    new_s = ts if s > 0.05 else s
    return colorsys.hsv_to_rgb(th, new_s, v)


def recolor_tgs_bytes(tgs_bytes: bytes, target_rgb: tuple[int, int, int]) -> bytes:
    """Return new gzipped TGS bytes with all fills retinted to target_rgb."""
    j = json.loads(gzip.decompress(tgs_bytes))
    tr, tg, tb = (c / 255.0 for c in target_rgb)
    target_hsv = colorsys.rgb_to_hsv(tr, tg, tb)

    def visit(node):
        if isinstance(node, dict):
            ty = node.get("ty")
            # Solid fill (fl) and stroke (st) both have a colour at .c.k
            if ty in ("fl", "st"):
                k = node.get("c", {}).get("k")
                if (
                    isinstance(k, list)
                    and len(k) >= 3
                    and all(isinstance(x, (int, float)) for x in k[:3])
                ):
                    nr, ng, nb = _replace_color((float(k[0]), float(k[1]), float(k[2])), target_hsv)
                    k[0], k[1], k[2] = nr, ng, nb
                # animated colour (.c.a == 1) — recolor each keyframe
                elif isinstance(k, list):
                    for kf in k:
                        if isinstance(kf, dict):
                            for arr_key in ("s", "e"):
                                arr = kf.get(arr_key)
                                if (
                                    isinstance(arr, list)
                                    and len(arr) >= 3
                                    and all(isinstance(x, (int, float)) for x in arr[:3])
                                ):
                                    nr, ng, nb = _replace_color(
                                        (float(arr[0]), float(arr[1]), float(arr[2])),
                                        target_hsv,
                                    )
                                    arr[0], arr[1], arr[2] = nr, ng, nb
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for it in node:
                visit(it)

    visit(j)
    return gzip.compress(json.dumps(j, separators=(",", ":")).encode())
