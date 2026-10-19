"""Text watermark overlay.

Applied either as the user's own watermark or as the forced @bot_username
mark on free previews.

Font lookup order: well-known system paths, a recursive glob, matplotlib's
bundled DejaVu, and finally PIL's built-in bitmap font.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

log = get_logger("watermark")


def _find_first(patterns: list[str]) -> str | None:
    for pat in patterns:
        if os.path.exists(pat):
            return pat
    return None


def _glob_first(patterns: list[str]) -> str | None:
    for pat in patterns:
        for hit in glob.glob(pat, recursive=True):
            if os.path.exists(hit):
                return hit
    return None


def _matplotlib_font(name: str) -> str | None:
    try:
        import matplotlib  # noqa: F401
        import matplotlib.font_manager as fm

        path = fm.findfont(fm.FontProperties(family=name))
        return path if path and os.path.exists(path) else None
    except Exception:
        return None


def _resolve_font_paths() -> dict[str, list[str]]:
    base_paths = {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/Library/Fonts/Arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ],
        "mono": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ],
        "serif": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        ],
    }

    glob_patterns = {
        "regular": [
            "/usr/share/fonts/**/DejaVuSans.ttf",
            "/usr/share/fonts/**/Liberation*Regular*.ttf",
            "/usr/share/fonts/**/NotoSans-Regular.ttf",
        ],
        "bold": [
            "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/**/Liberation*Bold*.ttf",
            "/usr/share/fonts/**/NotoSans-Bold.ttf",
        ],
        "mono": [
            "/usr/share/fonts/**/DejaVuSansMono.ttf",
            "/usr/share/fonts/**/Liberation*Mono*.ttf",
        ],
        "serif": [
            "/usr/share/fonts/**/DejaVuSerif.ttf",
            "/usr/share/fonts/**/Liberation*Serif*.ttf",
        ],
    }

    mpl_families = {
        "regular": "DejaVu Sans",
        "bold": "DejaVu Sans",  # matplotlib only ships one weight by default
        "mono": "DejaVu Sans Mono",
        "serif": "DejaVu Serif",
    }

    out: dict[str, list[str]] = {}
    for key in base_paths:
        candidates: list[str] = []
        # exact known paths
        for p in base_paths[key]:
            if os.path.exists(p):
                candidates.append(p)
        # recursive glob
        if not candidates:
            hit = _glob_first(glob_patterns[key])
            if hit:
                candidates.append(hit)
        # matplotlib
        if not candidates:
            hit = _matplotlib_font(mpl_families[key])
            if hit:
                candidates.append(hit)
        out[key] = candidates
    return out


_FONT_CANDIDATES = _resolve_font_paths()
log.info("font paths resolved: %s", {k: (v[0] if v else None) for k, v in _FONT_CANDIDATES.items()})


FONT_LABELS = {
    "regular": "Обычный",
    "bold": "Жирный",
    "mono": "Моно",
    "serif": "Серифный",
}


_FONTS_CACHE_DIR = Path(__file__).parent.parent / "data" / "fonts"
_FONTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# Real TTF/OTF magic numbers — used to reject HTML 404 pages and partial files
_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


def _looks_like_font(data: bytes) -> bool:
    return len(data) > 1000 and data[:4] in _FONT_MAGIC


def _gfont_path(family: str) -> str | None:
    """Download a Google Font by family name, cache locally, return path or None.

    Tries (in order):
      1. Google Fonts CSS API (with an old IE User-Agent so we get TTF URLs,
         not woff2 — PIL/FreeType can't read woff2).
      2. google/fonts GitHub raw — multiple licence dirs and static/variable
         file layouts.

    Validates every download against the TTF magic header and refuses to
    cache HTML/empty responses, so a single failed fetch can't poison the
    cache forever.
    """
    safe = family.replace(" ", "_").replace("/", "_")
    cached = _FONTS_CACHE_DIR / f"{safe}.ttf"

    # Validate any existing cache file before trusting it.
    if cached.exists():
        try:
            with open(cached, "rb") as f:
                head = f.read(4)
            if head in _FONT_MAGIC and cached.stat().st_size > 1000:
                return str(cached)
            log.warning("stale/invalid cached font %s — re-downloading", cached.name)
            cached.unlink()
        except OSError:
            pass

    import re
    import urllib.request

    _UA_OLD = "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)"
    _UA_REAL = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    def _fetch(url: str, ua: str) -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": ua,
                "Accept": "text/css,*/*;q=0.1",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read()

    family_q = family.replace(" ", "+")

    # Source 1: Google Fonts CSS API (IE UA → TTF urls)
    for css_url in (
        f"https://fonts.googleapis.com/css?family={family_q}",
        f"https://fonts.googleapis.com/css2?family={family_q}",
    ):
        try:
            css = _fetch(css_url, _UA_OLD).decode("utf-8", "ignore")
            m = re.search(r"url\((https?://[^)]+\.(?:ttf|otf))\)", css)
            if not m:
                continue
            data = _fetch(m.group(1), _UA_OLD)
            if _looks_like_font(data):
                cached.write_bytes(data)
                log.info("font %r downloaded from GFonts (%d KB)", family, len(data) // 1024)
                return str(cached)
        except Exception as exc:
            log.debug("GFonts CSS for %r failed (%s): %s", family, css_url, exc)

    # Source 2: google/fonts GitHub mirror — common static patterns first
    # Square brackets in variable-font filenames (e.g. "Montserrat[wght].ttf")
    # MUST be percent-encoded — GitHub rejects raw brackets with 404.
    import urllib.parse

    slug_lc = family.lower().replace(" ", "")
    no_space = family.replace(" ", "")
    raw_host = "https://raw.githubusercontent.com/google/fonts/main"

    common_axis = ("wght", "opsz,wght", "wdth,wght", "slnt,wght", "ital,wght")
    candidates: list[str] = []
    for licdir in ("ofl", "apache", "ufl"):
        base = f"{raw_host}/{licdir}/{slug_lc}"
        candidates += [
            f"{base}/{no_space}-Regular.ttf",
            f"{base}/static/{no_space}-Regular.ttf",
            f"{base}/{no_space}-Regular.otf",
            f"{base}/{no_space}.ttf",
        ]
        for axis in common_axis:
            candidates.append(f"{base}/{no_space}{urllib.parse.quote(f'[{axis}]')}.ttf")

    for url in candidates:
        try:
            data = _fetch(url, _UA_REAL)
        except Exception as exc:
            log.debug("github %s failed: %s", url, exc)
            continue
        if _looks_like_font(data):
            cached.write_bytes(data)
            log.info("font %r downloaded from GitHub (%d KB)", family, len(data) // 1024)
            return str(cached)

    # Source 3: GitHub Contents API — list the directory and grab the
    # first .ttf that isn't italic/bold-only. Robust to any
    # naming scheme but uses 1 request per licdir.
    api_host = "https://api.github.com/repos/google/fonts/contents"
    for licdir in ("ofl", "apache", "ufl"):
        try:
            listing = _fetch(f"{api_host}/{licdir}/{slug_lc}", _UA_REAL)
        except Exception:
            continue
        try:
            import json as _json

            entries = _json.loads(listing)
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        # prefer non-italic regular-ish faces
        ttfs = [
            e["name"]
            for e in entries
            if isinstance(e, dict)
            and e.get("type") == "file"
            and e.get("name", "").lower().endswith((".ttf", ".otf"))
        ]
        ttfs.sort(key=lambda n: ("italic" in n.lower(), len(n)))
        for name in ttfs:
            url = f"{raw_host}/{licdir}/{slug_lc}/{urllib.parse.quote(name)}"
            try:
                data = _fetch(url, _UA_REAL)
            except Exception as exc:
                log.debug("github api-listed %s failed: %s", url, exc)
                continue
            if _looks_like_font(data):
                cached.write_bytes(data)
                log.info(
                    "font %r downloaded via GH API (%s, %d KB)", family, name, len(data) // 1024
                )
                return str(cached)

    log.warning("could not download font %r — system fallback will be used", family)
    return None


def _load_font(key: str, size: int) -> ImageFont.FreeTypeFont:
    """Load font by key.

    Key can be:
      - a legacy alias ("bold", "regular", "mono", "serif")
      - any Google Fonts family name ("Seymour One", "Montserrat", …)
    """
    # Check legacy alias mapping
    for path in _FONT_CANDIDATES.get(key, []):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    # Try as Google Fonts family name (includes default "Seymour One")
    gf_path = _gfont_path(key)
    if gf_path:
        try:
            return ImageFont.truetype(gf_path, size)
        except OSError:
            pass

    # Fallback to system regular
    for path in _FONT_CANDIDATES.get("regular", []):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    log.warning("no usable font for key=%r — PIL bitmap fallback", key)
    return ImageFont.load_default()


def _hex(value: str, default=(255, 255, 255, 255)) -> tuple[int, int, int, int]:
    v = value.lstrip("#")
    if len(v) != 6:
        return default
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)
    except ValueError:
        return default


def _position_xy(
    pos: str, canvas_wh: tuple[int, int], text_wh: tuple[int, int], pad: int
) -> tuple[int, int]:
    cw, ch = canvas_wh
    tw, th = text_wh
    if pos == "tl":
        return (pad, pad)
    if pos == "tr":
        return (cw - tw - pad, pad)
    if pos == "bl":
        return (pad, ch - th - pad)
    if pos == "br":
        return (cw - tw - pad, ch - th - pad)
    # centre
    return ((cw - tw) // 2, (ch - th) // 2)


def apply_watermark(
    frames: list[np.ndarray],
    text: str,
    *,
    color_hex: str = "#FFFFFF",
    position: str = "br",
    font_key: str = "bold",
    opacity: float = 0.85,
    is_preview: bool = False,
) -> list[np.ndarray]:
    """Apply text watermark to every frame, in-place mutation of copies.

    For preview mode (is_preview=True), text is bigger and centred regardless
    of `position` — that's the @bot_username stamp on previews.
    """
    if not text or not frames:
        return frames

    # Build the text overlay ONCE — every frame gets the same blit.
    h, w = frames[0].shape[:2]
    # font size scales with canvas height
    if is_preview:
        font_size = max(28, int(h * 0.18))
        position = "center"
        opacity = 0.92
    else:
        font_size = max(18, int(h * 0.085))

    font = _load_font(font_key, font_size)

    # Render text on transparent overlay
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad = max(12, int(h * 0.04))
    x, y = _position_xy(position, (w, h), (tw, th), pad)

    rgba = _hex(color_hex)
    rgba = (*rgba[:3], int(255 * max(0.0, min(1.0, opacity))))
    draw.text(
        (x - bbox[0], y - bbox[1]),
        text,
        font=font,
        fill=rgba,
    )

    overlay_arr = np.array(overlay, dtype=np.uint8)
    a = overlay_arr[..., 3:4].astype(np.float32) / 255.0  # (h, w, 1)

    out: list[np.ndarray] = []
    for frame in frames:
        f = frame.astype(np.float32)
        rgb = overlay_arr[..., :3].astype(np.float32) * a + f[..., :3] * (1 - a)
        new = np.empty_like(frame)
        new[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        new[..., 3] = frame[..., 3]
        out.append(new)
    log.info(
        "watermark applied: '%s' pos=%s font=%s preview=%s",
        text[:30],
        position,
        font_key,
        is_preview,
    )
    return out
