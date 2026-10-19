"""Composites rendered sticker frames over a background.

Backgrounds:
    none   transparent canvas
    image  a still image reused for every frame
    video  a video or GIF decoded by ffmpeg and resampled to the output fps
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from utils.logger import get_logger

log = get_logger("composer")


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _resize_cover(img: Image.Image, target: tuple[int, int]) -> Image.Image:
    """Resize+crop so that `img` exactly fills `target` (CSS object-fit: cover)."""
    tw, th = target
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _decode_video_frames(
    src: Path, target: tuple[int, int], fps: int, n_frames: int
) -> list[np.ndarray]:
    """Use ffmpeg to decode `n_frames` evenly across the video, scaled-cover."""
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is required for video backgrounds")

    tw, th = target
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(probe.stdout.strip() or "0")
    except (subprocess.CalledProcessError, ValueError):
        duration = 0.0

    if duration <= 0.05:
        # Treat as single-frame source
        return _decode_image_frame(src, target, n_frames)

    # We need `n_frames` evenly distributed → output_fps = n_frames / duration
    out_fps = max(1.0, n_frames / duration)

    # vf: scale to cover, then crop center
    vf = f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},fps={out_fps}"

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(src),
        "-vf",
        vf,
        "-vframes",
        str(n_frames),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-",
    ]
    log.info("ffmpeg decode: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, check=True)
    raw = proc.stdout
    frame_size = tw * th * 4
    available = len(raw) // frame_size

    frames: list[np.ndarray] = []
    for i in range(min(available, n_frames)):
        chunk = raw[i * frame_size : (i + 1) * frame_size]
        # .copy() promotes the read-only frombuffer view to a writable array.
        f = np.frombuffer(chunk, dtype=np.uint8).reshape(th, tw, 4).copy()
        f[..., 3] = 255  # background is by definition opaque
        frames.append(f)

    # If ffmpeg gave us fewer frames than asked, pad with last frame
    if frames and len(frames) < n_frames:
        last = frames[-1]
        frames.extend([last] * (n_frames - len(frames)))
    return frames


def _decode_image_frame(src: Path, target: tuple[int, int], n_frames: int) -> list[np.ndarray]:
    img = Image.open(src).convert("RGBA")
    img = _resize_cover(img, target)
    # np.array() (vs np.asarray()) makes a *writable* copy. PIL gives back a
    # read-only buffer otherwise, which blows up the moment we touch alpha.
    arr = np.array(img, dtype=np.uint8)
    arr[..., 3] = 255  # background is opaque
    return [arr] * n_frames


def _alpha_paste(base: np.ndarray, overlay: np.ndarray, x: int, y: int) -> np.ndarray:
    """Alpha-blend `overlay` onto `base` at top-left (x, y).

    Both arrays are uint8 RGBA. Operation is done in-place on a copy.
    """
    bh, bw = base.shape[:2]
    oh, ow = overlay.shape[:2]

    # clip overlay to base bounds
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(bw, x + ow), min(bh, y + oh)
    if x0 >= x1 or y0 >= y1:
        return base.copy()

    ox0, oy0 = x0 - x, y0 - y
    ox1, oy1 = ox0 + (x1 - x0), oy0 + (y1 - y0)

    out = base.copy()
    base_slice = out[y0:y1, x0:x1].astype(np.float32)
    overlay_slice = overlay[oy0:oy1, ox0:ox1].astype(np.float32)

    a = overlay_slice[..., 3:4] / 255.0  # (h, w, 1)
    rgb = overlay_slice[..., :3] * a + base_slice[..., :3] * (1 - a)
    new_a = np.clip(overlay_slice[..., 3:4] + base_slice[..., 3:4] * (1 - a), 0, 255)
    out[y0:y1, x0:x1, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[y0:y1, x0:x1, 3:4] = new_a.astype(np.uint8)
    return out


def _scale_sticker(frame: np.ndarray, target_h: int) -> np.ndarray:
    """Resize a square sticker frame so its height == target_h."""
    h, w = frame.shape[:2]
    if h == target_h:
        return frame
    scale = target_h / h
    new_w = max(1, round(w * scale))
    img = Image.fromarray(frame, "RGBA").resize((new_w, target_h), Image.LANCZOS)
    return np.array(img, dtype=np.uint8)


def compose_frames(
    sticker_frames: list[np.ndarray],
    canvas_size: tuple[int, int],
    *,
    bg_path: Path | None = None,
    bg_kind: str = "none",
    sticker_height_ratio: float = 0.85,
    matte_rgb: tuple[int, int, int] | None = (0, 0, 0),
) -> list[np.ndarray]:
    """Composite the sticker over the requested background.

    Args:
        sticker_frames:        list of (S, S, 4) RGBA frames.
        canvas_size:           (W, H) of the output canvas.
        bg_path:               source media; required for kind != 'none'.
        bg_kind:               'none' | 'image' | 'video'.
        sticker_height_ratio:  fraction of canvas height the sticker occupies.
        matte_rgb:             when bg_kind == 'none', fill the canvas with
                               this opaque colour instead of leaving it
                               transparent. This is **critical for GIF
                               quality** — GIF only supports 1-bit alpha,
                               so transparent edges become jagged. Pre-
                               multiplying onto an opaque matte gives smooth
                               anti-aliased edges and frees up the 256th
                               palette slot. Pass `None` to keep transparent.

    Returns:
        list of (H, W, 4) RGBA frames. If matte was applied, alpha == 255
        on every pixel.
    """
    cw, ch = canvas_size
    n = len(sticker_frames)
    if n == 0:
        raise ValueError("no sticker frames")

    sticker_h = round(ch * sticker_height_ratio)
    log.info(
        "compose: canvas=%dx%d, sticker_h=%d, n=%d, bg=%s, matte=%s",
        cw,
        ch,
        sticker_h,
        n,
        bg_kind,
        f"#{matte_rgb[0]:02x}{matte_rgb[1]:02x}{matte_rgb[2]:02x}" if matte_rgb else "none",
    )

    # Build background frames. Decoders already guarantee alpha=255 on
    # image/video backgrounds (see _decode_image_frame / _decode_video_frames).
    if bg_kind == "none" or bg_path is None:
        if matte_rgb is not None:
            r, g, b = matte_rgb
            base = np.empty((ch, cw, 4), dtype=np.uint8)
            base[..., 0] = r
            base[..., 1] = g
            base[..., 2] = b
            base[..., 3] = 255  # fully opaque — no 1-bit-alpha jaggies
            bg_frames = [base] * n
        else:
            bg_frames = [np.zeros((ch, cw, 4), dtype=np.uint8)] * n
    elif bg_kind == "image":
        bg_frames = _decode_image_frame(bg_path, (cw, ch), n)
    elif bg_kind == "video":
        bg_frames = _decode_video_frames(bg_path, (cw, ch), 30, n)
    else:
        raise ValueError(f"unknown bg_kind={bg_kind!r}")

    # Pre-scale sticker frames if needed (no-op when caller already
    # rendered at the correct sticker_h — see services/pipeline.py)
    scaled = [_scale_sticker(f, sticker_h) for f in sticker_frames]

    # Paste each sticker on its bg frame, centred
    out: list[np.ndarray] = []
    for sticker, bg in zip(scaled, bg_frames, strict=False):
        sw, sh = sticker.shape[1], sticker.shape[0]
        x = (cw - sw) // 2
        y = (ch - sh) // 2
        out.append(_alpha_paste(bg, sticker, x, y))

    # If we matted onto an opaque background, the result is by definition
    # fully opaque. LANCZOS resizing of the sticker can leave alpha values
    # of 254 here and there due to rounding, which would (incorrectly)
    # push the GIF encoder into its "transparent" path and waste a palette
    # slot. Force-fill alpha to 255 to guarantee opaque encoding.
    if matte_rgb is not None and bg_kind == "none":
        for f in out:
            f[..., 3] = 255
    elif bg_kind != "none":
        # User-supplied bg is also opaque (we set alpha=255 on bg above), so
        # the composite is opaque too.
        for f in out:
            f[..., 3] = 255

    return out
