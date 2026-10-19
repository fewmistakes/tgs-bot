"""TGS sticker rendering.

The backend is picked at runtime:

- rlottie (`rlottie-python`), Samsung's native Lottie engine and the same one
  Telegram clients use;
- python-lottie + cairosvg as a pure-Python fallback, slower but available
  everywhere.

Some premium emoji render fully transparent under rlottie; those are retried
with python-lottie and, failing that, with a monochrome-fill preprocessor.
"""

from __future__ import annotations

import gzip
import io
import json
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from utils.logger import get_logger

log = get_logger("tgs_renderer")


_BACKEND: str | None = None
_LOTTIE_AVAILABLE: bool | None = None


def _has_lottie_fallback() -> bool:
    """Return True if python-lottie + cairo are importable."""
    global _LOTTIE_AVAILABLE
    if _LOTTIE_AVAILABLE is not None:
        return _LOTTIE_AVAILABLE
    try:
        from lottie.exporters.cairo import export_png  # noqa: F401
        from lottie.parsers.tgs import parse_tgs  # noqa: F401

        _LOTTIE_AVAILABLE = True
    except ImportError:
        _LOTTIE_AVAILABLE = False
    return _LOTTIE_AVAILABLE


def _detect_backend() -> str:
    """Return 'rlottie' or 'lottie'. Cached."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND

    try:
        from rlottie_python import LottieAnimation  # noqa: F401

        _BACKEND = "rlottie"
        log.info("renderer backend: rlottie (Samsung C++ engine) — best quality")
        return _BACKEND
    except ImportError:
        pass

    if _has_lottie_fallback():
        _BACKEND = "lottie"
        log.warning(
            "rlottie not available — falling back to python-lottie + cairo. "
            "Install `rlottie-python` for ~3-5× quality boost."
        )
        return _BACKEND

    raise RuntimeError(
        "No TGS renderer available. Install at least one of:\n"
        "  pip install rlottie-python   (preferred)\n"
        "  pip install 'lottie[all]'    (fallback, needs libcairo)"
    )


def _frames_are_blank(frames: list[np.ndarray]) -> bool:
    """Detect when a backend silently produced empty output.

    rlottie sometimes silently fails on TGS files that use Telegram-specific
    Lottie extensions (common in premium emojis) and returns either:
        a) fully transparent frames (alpha=0 everywhere), or
        b) fully opaque uniform-colour frames (e.g. RGBA=0,0,0,255 — solid black).

    Both cases produce a "blank" result after compositing onto the matte.
    Detection: check that there's at least *some* pixel that is both
    non-transparent **and** non-pitch-black, AND that the frame has visual
    variance (it isn't a flat colour).
    """
    if not frames:
        return True
    # Sample up to 8 frames spaced across the animation
    step = max(1, len(frames) // 8)
    sample = frames[::step][:8]

    useful = 0
    total = 0
    for f in sample:
        if f.ndim != 3 or f.shape[2] != 4:
            return False  # non-RGBA — assume content
        alpha = f[..., 3]
        rgb_max = f[..., :3].max(axis=-1)
        # A pixel "contributes" if it's visible (alpha) AND not pitch-black.
        # rlottie's failure mode is alpha=255, RGB=0 — those pixels look
        # identical to a black background and are effectively invisible.
        useful += int(np.count_nonzero((alpha > 8) & (rgb_max > 8)))
        total += alpha.size

    if total == 0:
        return True
    if (useful / total) < 0.0005:
        return True

    # Second guard: even if some pixels look visible, a rendered frame
    # consisting of a single uniform colour has no actual content.
    middle = sample[len(sample) // 2]
    alpha_std = float(middle[..., 3].std())
    rgb_std = float(middle[..., :3].std())
    return bool(alpha_std < 1.0 and rgb_std < 1.0)


def _replace_color_in_property(prop: Any, replace_with: tuple[float, float, float]) -> int:
    """In-place replace black [0,0,0] in a Lottie color property dict.

    Returns count of replacements made. Handles both static (a=0) and
    keyframed (a=1) colours.
    """
    if not isinstance(prop, dict):
        return 0
    k = prop.get("k")
    if k is None:
        return 0
    n = 0

    def _maybe_replace(arr: list) -> bool:
        if (
            isinstance(arr, list)
            and len(arr) >= 3
            and all(isinstance(x, (int, float)) for x in arr[:3])
            and arr[0] < 0.05
            and arr[1] < 0.05
            and arr[2] < 0.05
        ):
            arr[0], arr[1], arr[2] = replace_with
            return True
        return False

    # Static colour: k is [r, g, b] or [r, g, b, a]
    if isinstance(k, list) and (not k or not isinstance(k[0], dict)):
        if _maybe_replace(k):
            n += 1
    # Animated colour: k is a list of keyframes, each with "s" and/or "e"
    elif isinstance(k, list):
        for kf in k:
            if not isinstance(kf, dict):
                continue
            for key in ("s", "e"):
                v = kf.get(key)
                if isinstance(v, list) and _maybe_replace(v):
                    n += 1
    return n


def _walk_and_recolor(obj: Any, replace_with: tuple[float, float, float]) -> int:
    """Walk Lottie JSON and replace black fill/stroke colours.

    Targets only `fl` (fill) and `st` (stroke) shape items — leaves
    positions, scales, opacities, and gradient stops untouched.
    """
    n = 0
    if isinstance(obj, dict):
        ty = obj.get("ty")
        if ty in ("fl", "st") and "c" in obj:
            n += _replace_color_in_property(obj["c"], replace_with)
        for v in obj.values():
            n += _walk_and_recolor(v, replace_with)
    elif isinstance(obj, list):
        for item in obj:
            n += _walk_and_recolor(item, replace_with)
    return n


def _preprocess_monochrome_tgs(
    tgs_path: Path,
    replace_with: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> Path:
    """Rewrite TGS replacing placeholder black fills with `replace_with`.

    Telegram premium emojis use [0,0,0] as a placeholder colour that the
    client replaces with the user's accent colour at display time. Both
    rlottie and python-lottie render the placeholder literally, producing
    an all-black output. This preprocessor swaps black for white (or any
    given colour) so the rendered emoji is actually visible.

    Returns the path to a new gzipped TGS in a temp file. Caller is
    responsible for unlinking it.
    """
    with gzip.open(tgs_path, "rb") as fh:
        data = json.load(fh)
    replaced = _walk_and_recolor(data, replace_with)
    log.info("monochrome preprocess: replaced %d black colours in %s", replaced, tgs_path.name)

    fd, out_str = tempfile.mkstemp(suffix=".tgs", prefix="recolor_")
    import os

    os.close(fd)
    out_path = Path(out_str)
    try:
        with gzip.open(out_path, "wb") as fh:
            fh.write(json.dumps(data).encode("utf-8"))
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    return out_path


def _read_tgs_metadata(tgs_path: Path) -> tuple[int, float, int, int]:
    """Return (total_frames, native_fps, width, height) of the animation."""
    with gzip.open(tgs_path, "rb") as fh:
        data = json.load(fh)
    ip = float(data.get("ip", 0))
    op = float(data.get("op", 0))
    fr = float(data.get("fr", 60))
    w = int(data.get("w", 512))
    h = int(data.get("h", 512))
    total = max(1, round(op - ip))
    return total, fr, w, h


def _render_rlottie(
    tgs_path: Path,
    out_size: int,
    fps: int,
    max_seconds: float,
    workers: int,
    supersample: float = 1.0,
) -> list[np.ndarray]:
    from PIL import Image
    from rlottie_python import LottieAnimation

    # rlottie can read .tgs directly (auto-detects gzip)
    anim = LottieAnimation.from_tgs(str(tgs_path))
    total_frames = anim.lottie_animation_get_totalframe()
    native_fps = anim.lottie_animation_get_framerate()
    duration = total_frames / native_fps  # seconds
    duration = min(duration, max_seconds)

    out_count = max(1, round(duration * fps))
    # which native frames to sample
    indices = np.linspace(0, total_frames - 1, num=out_count, dtype=np.int32).tolist()

    # supersample: render bigger, then LANCZOS-down. Gives crisp edges even
    # when rlottie's native AA isn't enough at small target sizes.
    render_size = max(out_size, round(out_size * supersample))

    log.info(
        "rlottie: native=%d frames @ %.1f fps (%.2fs) → output %d frames @ %d fps "
        "(render %d px, downscale to %d px)",
        total_frames,
        native_fps,
        duration,
        out_count,
        fps,
        render_size,
        out_size,
    )

    # rlottie objects aren't trivially thread-safe across frames in the
    # same handle, so we render serially but keep the loop tight.
    frames: list[np.ndarray] = []
    need_downscale = render_size != out_size
    for idx in indices:
        # render_pillow_frame returns PIL.Image RGBA
        img = anim.render_pillow_frame(
            frame_num=int(idx),
            width=render_size,
            height=render_size,
        )
        if need_downscale:
            img = img.resize((out_size, out_size), Image.LANCZOS)
        frames.append(np.array(img, dtype=np.uint8))

    return frames


def _render_lottie(
    tgs_path: Path,
    out_size: int,
    fps: int,
    max_seconds: float,
    workers: int,
    supersample: float = 1.0,
) -> list[np.ndarray]:
    from lottie.exporters.cairo import export_png
    from lottie.parsers.tgs import parse_tgs
    from PIL import Image

    with tgs_path.open("rb") as fh:
        animation = parse_tgs(fh)

    native_fps = float(animation.frame_rate)
    total = int(animation.out_point - animation.in_point)
    duration = total / native_fps
    duration = min(duration, max_seconds)
    out_count = max(1, round(duration * fps))

    indices = np.linspace(
        animation.in_point, animation.out_point - 1, num=out_count, dtype=np.int32
    ).tolist()

    render_size = max(out_size, round(out_size * supersample))

    log.info(
        "lottie: native=%d frames @ %.1f fps (%.2fs) → output %d frames @ %d fps "
        "(render %d px, downscale to %d px)",
        total,
        native_fps,
        duration,
        out_count,
        fps,
        render_size,
        out_size,
    )

    need_downscale = render_size != out_size

    def render_one(frame_idx: int) -> np.ndarray:
        buf = io.BytesIO()
        scale = render_size / float(animation.width or 512)
        export_png(animation, buf, frame_idx, dpi=96 * scale)
        buf.seek(0)
        img = Image.open(buf).convert("RGBA")
        if need_downscale:
            img = img.resize((out_size, out_size), Image.LANCZOS)
        return np.array(img, dtype=np.uint8)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(render_one, indices))


def render_frames(
    tgs_path: Path,
    out_size: int,
    fps: int,
    max_seconds: float = 6.0,
    workers: int = 2,
    supersample: float = 1.0,
) -> list[np.ndarray]:
    """Render a TGS to a list of square RGBA frames.

    Args:
        tgs_path:    .tgs file on disk.
        out_size:    side length in pixels of the *output* sticker frames.
        fps:         output frame rate.
        max_seconds: cap animation length to this (longer get truncated).
        workers:     parallel workers for the lottie fallback.
        supersample: render at `out_size * supersample` then LANCZOS-down.
                     2.0 is the sweet spot for crisp edges; 1.0 disables it.
    """
    backend = _detect_backend()
    if backend == "rlottie":
        frames = _render_rlottie(tgs_path, out_size, fps, max_seconds, workers, supersample)
        # rlottie can silently render some TGS files (especially premium
        # emojis using Telegram-specific extensions) as fully transparent.
        # If that happened, retry with python-lottie which handles them.
        if _frames_are_blank(frames):
            log.warning(
                "rlottie produced blank frames for %s — trying fallbacks",
                tgs_path.name,
            )

            # Fallback 1: python-lottie (catches some unsupported features)
            if _has_lottie_fallback():
                try:
                    fb = _render_lottie(
                        tgs_path,
                        out_size,
                        fps,
                        max_seconds,
                        workers,
                        supersample,
                    )
                    if not _frames_are_blank(fb):
                        log.info("python-lottie fallback succeeded for %s", tgs_path.name)
                        return fb
                    log.warning("python-lottie also produced blank for %s", tgs_path.name)
                except Exception:
                    log.exception("python-lottie fallback raised for %s", tgs_path.name)

            # Fallback 2: monochrome-fix preprocessor (Telegram premium emojis)
            try:
                fixed = _preprocess_monochrome_tgs(tgs_path)
                try:
                    fb = _render_rlottie(
                        fixed,
                        out_size,
                        fps,
                        max_seconds,
                        workers,
                        supersample,
                    )
                    if not _frames_are_blank(fb):
                        log.info("monochrome-fix preprocessor succeeded for %s", tgs_path.name)
                        return fb
                    log.error(
                        "all fallbacks (lottie + monochrome-fix) failed for "
                        "%s — TGS appears to be unrenderable",
                        tgs_path.name,
                    )
                finally:
                    fixed.unlink(missing_ok=True)
            except Exception:
                log.exception("monochrome-fix preprocessor raised for %s", tgs_path.name)
        return frames
    return _render_lottie(tgs_path, out_size, fps, max_seconds, workers, supersample)


def get_metadata(tgs_path: Path) -> tuple[int, float, int, int]:
    """Quick peek at TGS without rendering: (total_frames, fps, w, h)."""
    return _read_tgs_metadata(tgs_path)


def expected_frame_count(
    tgs_path: Path,
    fps: int,
    max_seconds: float = 6.0,
) -> int:
    """Number of frames render_frames/render_frames_streaming will yield.

    Same arithmetic the rlottie/lottie backends use, so callers can size
    background streams correctly *before* sticker rendering starts.
    """
    total, native_fps, _, _ = _read_tgs_metadata(tgs_path)
    duration = min(total / max(native_fps, 1.0), max_seconds)
    return max(1, round(duration * fps))


def render_frames_streaming(
    tgs_path: Path,
    out_size: int,
    fps: int,
    max_seconds: float = 6.0,
    workers: int = 2,
    supersample: float = 1.0,
) -> Iterator[np.ndarray]:
    """Stream RGBA frames one at a time — same pixels as render_frames(),
    but doesn't hold all frames in RAM. Saves ~150 MB on a 180-frame run.

    Performs the same blank-output check as render_frames() on the first
    few frames; if they're blank, falls back to the list-based path
    (which has the python-lottie + monochrome-fix recovery chain).
    """
    backend = _detect_backend()
    if backend != "rlottie":
        # python-lottie backend uses a thread pool and is the rare path —
        # not worth a streaming variant. Yield from the existing list.
        for f in render_frames(tgs_path, out_size, fps, max_seconds, workers, supersample):
            yield f
        return

    from PIL import Image
    from rlottie_python import LottieAnimation

    anim = LottieAnimation.from_tgs(str(tgs_path))
    total_frames = anim.lottie_animation_get_totalframe()
    native_fps = anim.lottie_animation_get_framerate()
    duration = min(total_frames / native_fps, max_seconds)
    out_count = max(1, round(duration * fps))

    indices = np.linspace(0, total_frames - 1, num=out_count, dtype=np.int32).tolist()
    render_size = max(out_size, round(out_size * supersample))
    need_downscale = render_size != out_size

    log.info(
        "rlottie streaming: native=%d frames @ %.1f fps (%.2fs) → output "
        "%d frames @ %d fps (render %d px, downscale to %d px)",
        total_frames,
        native_fps,
        duration,
        out_count,
        fps,
        render_size,
        out_size,
    )

    def _render_one(idx: int) -> np.ndarray:
        img = anim.render_pillow_frame(
            frame_num=int(idx),
            width=render_size,
            height=render_size,
        )
        if need_downscale:
            img = img.resize((out_size, out_size), Image.LANCZOS)
        return np.array(img, dtype=np.uint8)

    # Render a small head buffer for blank detection. 8 frames at 450×450
    # RGBA is ~6 MB — cheap, and lets us bail out cleanly to the recovery
    # path before we've yielded anything to the consumer.
    head_size = min(8, len(indices))
    head: list[np.ndarray] = [_render_one(indices[i]) for i in range(head_size)]
    if _frames_are_blank(head):
        log.warning(
            "rlottie streaming: head frames look blank for %s — falling "
            "back to list-based render_frames() with recovery chain",
            tgs_path.name,
        )
        head.clear()
        for f in render_frames(tgs_path, out_size, fps, max_seconds, workers, supersample):
            yield f
        return

    for f in head:
        yield f
    head.clear()
    for i in range(head_size, len(indices)):
        yield _render_one(indices[i])
