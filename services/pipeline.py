"""Render pipeline.

Frames are composited, watermarked and piped to ffmpeg one at a time instead
of materialising the whole canvas-sized sequence, which ran small VPS
instances out of memory at 1920×530.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from config import Config
from services.color_changer import parse_hex, tint_rgba_array
from services.tgs_renderer import expected_frame_count, render_frames_streaming
from services.user_config import UserConfig
from services.watermark import _hex, _load_font, _position_xy
from utils.logger import get_logger

log = get_logger("pipeline")


def output_extension(fmt: str) -> str:
    return ".mp4"


def _resize_cover(img: Image.Image, target: tuple[int, int]) -> Image.Image:
    tw, th = target
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _make_matte(canvas_size: tuple[int, int], rgb: tuple[int, int, int]) -> np.ndarray:
    cw, ch = canvas_size
    base = np.empty((ch, cw, 3), dtype=np.uint8)
    base[..., 0] = rgb[0]
    base[..., 1] = rgb[1]
    base[..., 2] = rgb[2]
    return base


def _decode_static_image(path: Path, canvas_size: tuple[int, int]) -> np.ndarray:
    img = Image.open(path).convert("RGB")
    img = _resize_cover(img, canvas_size)
    return np.array(img, dtype=np.uint8)


def _stream_video_frames(
    path: Path, canvas_size: tuple[int, int], n_frames: int
) -> Iterator[np.ndarray]:
    """Yield n_frames RGB frames from a video, scaled to cover canvas."""
    cw, ch = canvas_size
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required for video backgrounds")

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
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(probe.stdout.strip() or "0")
    except (subprocess.CalledProcessError, ValueError):
        duration = 0.0

    if duration <= 0.05:
        # Too short to be a video: treat as a still image.
        single = _decode_static_image(path, canvas_size)
        for _ in range(n_frames):
            yield single
        return

    out_fps = max(1.0, n_frames / duration)
    vf = f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},fps={out_fps}"
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        vf,
        "-vframes",
        str(n_frames),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    assert proc.stdout is not None

    frame_size = cw * ch * 3
    last: np.ndarray | None = None
    yielded = 0
    while yielded < n_frames:
        chunk = proc.stdout.read(frame_size)
        if not chunk or len(chunk) < frame_size:
            break
        arr = np.frombuffer(chunk, dtype=np.uint8).reshape(ch, cw, 3).copy()
        last = arr
        yield arr
        yielded += 1
    proc.stdout.close()
    proc.wait(timeout=5)

    # pad with last frame if ffmpeg gave us fewer
    while yielded < n_frames and last is not None:
        yield last
        yielded += 1


def _scale_sticker(frame: np.ndarray, target_h: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if h == target_h:
        return frame
    scale = target_h / h
    new_w = max(1, round(w * scale))
    img = Image.fromarray(frame, "RGBA").resize((new_w, target_h), Image.LANCZOS)
    return np.array(img, dtype=np.uint8)


def _blit_rgba_onto_rgb(bg_rgb: np.ndarray, sticker_rgba: np.ndarray, x: int, y: int) -> np.ndarray:
    """Alpha-blend sticker_rgba onto a copy of bg_rgb. Result is RGB uint8."""
    bh, bw = bg_rgb.shape[:2]
    oh, ow = sticker_rgba.shape[:2]

    out = bg_rgb.copy()

    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(bw, x + ow), min(bh, y + oh)
    if x0 >= x1 or y0 >= y1:
        return out

    ox0, oy0 = x0 - x, y0 - y
    ox1, oy1 = ox0 + (x1 - x0), oy0 + (y1 - y0)

    base_slice = out[y0:y1, x0:x1].astype(np.float32)
    overlay_slice = sticker_rgba[oy0:oy1, ox0:ox1].astype(np.float32)
    a = overlay_slice[..., 3:4] / 255.0
    rgb = overlay_slice[..., :3] * a + base_slice * (1 - a)
    out[y0:y1, x0:x1] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def _build_watermark_overlay(
    canvas_size: tuple[int, int],
    text: str,
    *,
    color_hex: str,
    position: str,
    font_key: str,
    opacity: float,
    is_preview: bool,
) -> np.ndarray | None:
    """Return (H, W, 4) uint8 RGBA overlay or None if no text."""
    if not text:
        return None
    cw, ch = canvas_size
    if is_preview:
        font_size = max(28, int(ch * 0.18))
        position = "center"
        opacity = 0.92
    else:
        font_size = max(18, int(ch * 0.085))

    font = _load_font(font_key, font_size)
    overlay = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad = max(12, int(ch * 0.04))
    x, y = _position_xy(position, (cw, ch), (tw, th), pad)

    rgba = _hex(color_hex)
    rgba = (*rgba[:3], int(255 * max(0.0, min(1.0, opacity))))
    draw.text(
        (x - bbox[0], y - bbox[1]),
        text,
        font=font,
        fill=rgba,
    )
    return np.array(overlay, dtype=np.uint8)


def _apply_watermark_inplace(rgb_frame: np.ndarray, overlay: np.ndarray) -> None:
    """Blend pre-rendered RGBA overlay onto an RGB frame, in-place."""
    a = overlay[..., 3:4].astype(np.float32) / 255.0
    f = rgb_frame.astype(np.float32)
    blended = overlay[..., :3].astype(np.float32) * a + f * (1.0 - a)
    rgb_frame[...] = np.clip(blended, 0, 255).astype(np.uint8)


def _run_pipeline(
    tgs_path: Path,
    user_cfg: UserConfig,
    cfg: Config,
    out_path: Path,
    *,
    bg_path: Path | None = None,
    bg_kind: str = "none",
    preview_watermark: str | None = None,
) -> Path:
    started = time.perf_counter()

    # Compute expected frame count from TGS metadata (no rendering yet),
    # so we can size the background stream before rendering starts.
    sticker_size = max(1, round(user_cfg.height * cfg.sticker_scale))
    n = expected_frame_count(tgs_path, user_cfg.fps, cfg.max_render_seconds)

    cw, ch = user_cfg.width, user_cfg.height
    sticker_h = round(ch * cfg.sticker_scale)

    # Pre-build bg: matte (constant) | static image (one buffer) | video stream
    bg_rgb_color = parse_hex(user_cfg.bg_color) or cfg.matte_rgb
    bg_static: np.ndarray | None = None
    bg_iter: Iterator[np.ndarray] | None = None

    if bg_kind == "none" or bg_path is None:
        bg_static = _make_matte((cw, ch), bg_rgb_color)
    elif bg_kind == "image":
        bg_static = _decode_static_image(bg_path, (cw, ch))
    elif bg_kind == "video":
        bg_iter = _stream_video_frames(bg_path, (cw, ch), n)
    else:
        raise ValueError(f"unknown bg_kind={bg_kind!r}")

    # Tint colour resolved once; applied per-frame in the streaming loop.
    tint_rgb: tuple[int, int, int] | None = None
    if user_cfg.emoji_color_mode == "tint" and user_cfg.emoji_color:
        tint_rgb = parse_hex(user_cfg.emoji_color)

    # Pre-build watermark overlays (user's + optional preview marker).
    # Preview shows BOTH so the user can verify their own watermark.
    wm_user_overlay: np.ndarray | None = None
    if user_cfg.watermark.enabled and user_cfg.watermark.text:
        wm = user_cfg.watermark
        wm_user_overlay = _build_watermark_overlay(
            (cw, ch),
            wm.text,
            color_hex=wm.color,
            position=wm.position,
            font_key=wm.font_key,
            opacity=0.85,
            is_preview=False,
        )
    wm_preview_overlay: np.ndarray | None = None
    if preview_watermark:
        wm_preview_overlay = _build_watermark_overlay(
            (cw, ch),
            preview_watermark,
            color_hex="#FFFFFF",
            position="center",
            font_key=user_cfg.watermark.font_key,
            opacity=0.85,
            is_preview=True,
        )

    # Stream to ffmpeg via stdin (raw RGB)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bufsize_kbps = cfg.h264_max_kbps * 2
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{cw}x{ch}",
        "-r",
        str(user_cfg.fps),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-preset",
        cfg.h264_preset,
        "-tune",
        "animation",
        "-crf",
        str(cfg.h264_crf),
        "-maxrate",
        f"{cfg.h264_max_kbps}k",
        "-bufsize",
        f"{bufsize_kbps}k",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-movflags",
        "+faststart",
        "-an",
        str(out_path),
    ]
    log.info(
        "stream → ffmpeg: %dx%d @ %dfps, %d frames, crf=%d preset=%s",
        cw,
        ch,
        user_cfg.fps,
        n,
        cfg.h264_crf,
        cfg.h264_preset,
    )

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    sticker_stream = render_frames_streaming(
        tgs_path=tgs_path,
        out_size=sticker_size,
        fps=user_cfg.fps,
        max_seconds=cfg.max_render_seconds,
        workers=cfg.workers,
        supersample=cfg.supersample,
    )

    # Unified bg provider: yields static buffer, or video frames padded with
    # the last frame so the loop survives small frame-count mismatches
    # between the metadata-based estimate and rlottie's actual count.
    def _bg_provider() -> Iterator[np.ndarray]:
        if bg_static is not None:
            while True:
                yield bg_static
        assert bg_iter is not None
        last: np.ndarray | None = None
        for f in bg_iter:
            last = f
            yield f
        while last is not None:
            yield last

    bg_stream = _bg_provider()

    produced = 0
    try:
        for sticker in sticker_stream:
            if tint_rgb is not None:
                sticker = tint_rgba_array(sticker, tint_rgb)
            scaled = _scale_sticker(sticker, sticker_h)
            del sticker

            sw, sh = scaled.shape[1], scaled.shape[0]
            x = (cw - sw) // 2
            y = (ch - sh) // 2

            bg = next(bg_stream)
            composed = _blit_rgba_onto_rgb(bg, scaled, x, y)
            del scaled

            if wm_user_overlay is not None:
                _apply_watermark_inplace(composed, wm_user_overlay)
            if wm_preview_overlay is not None:
                _apply_watermark_inplace(composed, wm_preview_overlay)

            proc.stdin.write(composed.tobytes())
            del composed
            produced += 1
    except Exception:
        proc.kill()
        raise
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()

    log.info("streamed %d frames to ffmpeg in %.2fs", produced, time.perf_counter() - started)

    rc = proc.wait()
    err = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
    if rc != 0:
        log.error("ffmpeg stderr: %s", err.strip())
        raise RuntimeError(f"ffmpeg failed (code {rc})")

    size_kb = out_path.stat().st_size / 1024
    log.info(
        "pipeline done: %s (%.0f KB) in %.2fs",
        out_path.name,
        size_kb,
        time.perf_counter() - started,
    )
    return out_path


async def render_async(
    tgs_path: Path,
    user_cfg: UserConfig,
    cfg: Config,
    out_path: Path,
    *,
    bg_path: Path | None = None,
    bg_kind: str = "none",
    preview_watermark: str | None = None,
) -> Path:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _run_pipeline(
            tgs_path,
            user_cfg,
            cfg,
            out_path,
            bg_path=bg_path,
            bg_kind=bg_kind,
            preview_watermark=preview_watermark,
        ),
    )
