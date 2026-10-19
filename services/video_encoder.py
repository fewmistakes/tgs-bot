"""
MP4 video encoder via ffmpeg + libx264.

Why MP4 instead of GIF:
    • H.264 is 10-30× more efficient than GIF for the same visual quality.
    • Telegram's `sendAnimation` accepts MP4 and renders it as a GIF in chat
      (auto-loop, no audio, no controls). The user can't tell the difference.
    • A 1920×530 @ 60fps 3-second clip is typically ~150-500 KB.

Telegram's MP4 requirements (for sendAnimation):
    • Codec:     H.264 (libx264)
    • Pixel fmt: yuv420p   ← required, otherwise some clients won't preview
    • Profile:   baseline / main / high
    • Container: MP4 with `+faststart` (moov atom up front)
    • No audio   (or empty audio)
    • Even dimensions on both axes
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from utils.logger import get_logger

log = get_logger("video_encoder")


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _ensure_even(n: int) -> int:
    """libx264 with yuv420p needs even dimensions."""
    return n if n % 2 == 0 else n - 1


def encode_mp4(
    frames: list[np.ndarray],
    out_path: Path,
    fps: int = 60,
    crf: int = 23,
    preset: str = "slow",
    max_kbps: int = 2000,
) -> Path:
    """Encode RGBA frames to a Telegram-friendly MP4.

    Args:
        frames:   list of (H, W, 4) uint8 arrays. Alpha is *flattened* — MP4
                  has no alpha channel, so any transparent pixels show as
                  black (matching the matte we use elsewhere).
        out_path: destination file (must end with .mp4).
        fps:      output framerate.
        crf:      libx264 constant rate factor. Lower = bigger + better.
                    18 ≈ visually lossless     (~MB-range)
                    20 ≈ excellent             (~500 KB - 1 MB)
                    23 ≈ Telegram-quality      (~150-400 KB)        ← default
                    26 ≈ noticeable artefacts  (~80-150 KB)
        preset:   x264 speed/quality tradeoff: ultrafast..veryslow.
                  `slow` = ~2× slower than medium, ~15-20% better compression.
        max_kbps: hard ceiling on average bitrate. Even if CRF wants to allocate
                  more bits (e.g. for high-motion noisy backgrounds), cap it
                  here. 2000 kbps over 6s ≈ 1.5 MB max.

    Returns:
        out_path on success.
    """
    if not _ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it (`apt install ffmpeg` or `brew install ffmpeg`)."
        )
    if not frames:
        raise ValueError("no frames to encode")

    h, w = frames[0].shape[:2]
    w, h = _ensure_even(w), _ensure_even(h)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)

        # Dump frames as PNG (lossless input to ffmpeg). RGBA→RGB by
        # flattening alpha onto black; everywhere else in the pipeline
        # we already matted the sticker, so this is equivalent to "drop
        # the alpha channel".
        for i, frame in enumerate(frames):
            img = Image.fromarray(frame, "RGBA")
            if (w, h) != img.size:
                img = img.crop((0, 0, w, h))
            # alpha-flatten on black (matches our matte)
            rgb = Image.new("RGB", img.size, (0, 0, 0))
            rgb.paste(img, mask=img.split()[3])
            rgb.save(tmp_dir / f"f_{i:05d}.png", format="PNG", optimize=False)

        # Encode with libx264.
        #
        # Key flags:
        #   -tune animation  → x264 preset tuned for cartoon/synthetic content.
        #                      Increases deblocking strength and assigns more
        #                      bits to flat regions where banding is visible.
        #                      Cuts size by ~10-25% on Lottie-style content.
        #   -maxrate/-bufsize → hard bitrate ceiling. Without this, complex
        #                      backgrounds (photo-textured matte, video bg)
        #                      can blow CRF's bit budget into the multi-MB
        #                      range. With it, the file size is predictable.
        #   -profile:v high  + -level 4.2  → broadly compatible everywhere
        #                      Telegram runs (mobile, web, desktop).
        #   -movflags +faststart → moov atom written first → instant preview
        #                      in chat without downloading the whole file.
        bufsize_kbps = max_kbps * 2
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(tmp_dir / "f_%05d.png"),
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-tune",
            "animation",
            "-crf",
            str(crf),
            "-maxrate",
            f"{max_kbps}k",
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
            "-vf",
            f"scale={w}:{h}:flags=lanczos",
            str(out_path),
        ]
        log.info(
            "ffmpeg H.264: %d frames @ %d fps, crf=%d preset=%s tune=animation maxrate=%dk → %s",
            len(frames),
            fps,
            crf,
            preset,
            max_kbps,
            out_path.name,
        )
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            log.error("ffmpeg stderr: %s", proc.stderr.strip())
            raise RuntimeError(f"ffmpeg failed (code {proc.returncode})")

    size_kb = out_path.stat().st_size / 1024
    log.info("MP4 ready: %.0f KB", size_kb)
    return out_path
