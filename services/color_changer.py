"""Hex colour parsing and HSV tinting that keeps the sticker's highlights and shadows."""

from __future__ import annotations

import colorsys
import re

import numpy as np

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(text: str) -> tuple[int, int, int] | None:
    """Parse '#ff3344' / 'ff3344' → (r,g,b) 0-255. None if invalid."""
    if not text:
        return None
    m = _HEX_RE.match(text.strip())
    if not m:
        return None
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def tint_rgba_array(
    rgba: np.ndarray,
    target_rgb: tuple[int, int, int],
    *,
    strength: float = 1.0,
) -> np.ndarray:
    """Re-colour an RGBA frame to a target hue while preserving luminance.

    Algorithm:
        1. Convert RGB → HSV.
        2. Replace H with target_h, scale S toward target_s.
        3. Keep V (value/brightness) — this preserves highlights & shadows
           that make the original sticker readable.
        4. Blend with original by `strength` (1.0 = full tint).
        5. Alpha channel is preserved untouched.

    Args:
        rgba:        (H, W, 4) uint8.
        target_rgb:  desired colour 0-255.
        strength:    0.0 = no change, 1.0 = full re-colour.

    Returns:
        New (H, W, 4) uint8 array.
    """
    if rgba.dtype != np.uint8:
        raise TypeError("expected uint8 RGBA array")
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("expected (H, W, 4) shape")

    rgb = rgba[..., :3].astype(np.float32) / 255.0
    alpha = rgba[..., 3:4]  # keep as-is

    # vectorised RGB → HSV
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.max(rgb, axis=-1)
    minc = np.min(rgb, axis=-1)
    v = maxc
    delta = maxc - minc

    # saturation
    s = np.where(maxc > 0, delta / np.where(maxc == 0, 1, maxc), 0)

    # hue
    rc = np.where(delta > 0, (maxc - r) / np.where(delta == 0, 1, delta), 0)
    gc = np.where(delta > 0, (maxc - g) / np.where(delta == 0, 1, delta), 0)
    bc = np.where(delta > 0, (maxc - b) / np.where(delta == 0, 1, delta), 0)

    h = np.zeros_like(maxc)
    h = np.where((maxc == r) & (delta > 0), (bc - gc), h)
    h = np.where((maxc == g) & (delta > 0), 2.0 + (rc - bc), h)
    h = np.where((maxc == b) & (delta > 0), 4.0 + (gc - rc), h)
    h = (h / 6.0) % 1.0

    # target HSV
    tr, tg, tb = (c / 255.0 for c in target_rgb)
    th, ts, _tv = colorsys.rgb_to_hsv(tr, tg, tb)

    # apply: keep V, override H, lerp S toward target_s
    new_h = np.full_like(h, th)
    # if pixel is grey-ish (low S), don't over-saturate it — keep proportional
    new_s = np.clip(s * 0 + ts * np.where(s > 0.05, 1.0, s / 0.05), 0, 1)
    new_v = v

    # vectorised HSV → RGB
    i = np.floor(new_h * 6.0).astype(np.int32) % 6
    f = (new_h * 6.0) - np.floor(new_h * 6.0)
    p = new_v * (1.0 - new_s)
    q = new_v * (1.0 - new_s * f)
    t = new_v * (1.0 - new_s * (1.0 - f))

    out_r = np.choose(i, [new_v, q, p, p, t, new_v])
    out_g = np.choose(i, [t, new_v, new_v, q, p, p])
    out_b = np.choose(i, [p, p, t, new_v, new_v, q])

    new_rgb = np.stack([out_r, out_g, out_b], axis=-1)

    if strength < 1.0:
        new_rgb = rgb * (1 - strength) + new_rgb * strength

    new_rgb = np.clip(new_rgb * 255.0, 0, 255).astype(np.uint8)
    return np.concatenate([new_rgb, alpha], axis=-1)
