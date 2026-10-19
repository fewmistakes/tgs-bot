"""GIF encoding with gifski, falling back to Pillow when the binary is missing.

gifski is installed separately: `cargo install gifski`, `snap install gifski`
or a release build from https://gif.ski/.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from utils.logger import get_logger

log = get_logger("gif_encoder")


def _gifski_available() -> bool:
    return shutil.which("gifski") is not None


def _encode_with_gifski(
    frames: list[np.ndarray],
    out_path: Path,
    fps: int,
    quality: int,
) -> None:
    """Pipe PNG frames into gifski."""
    if not (1 <= quality <= 100):
        quality = max(1, min(100, quality))

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        # gifski likes numerically-sorted PNG inputs
        png_paths: list[str] = []
        for i, frame in enumerate(frames):
            p = tmp_dir / f"frame_{i:05d}.png"
            Image.fromarray(frame, "RGBA").save(p, format="PNG", optimize=False)
            png_paths.append(str(p))

        cmd = [
            "gifski",
            "--fps",
            str(fps),
            "--quality",
            str(quality),
            # --motion-quality controls inter-frame compression vs. clarity.
            # Pinning to 100 trades a slightly larger file for crisp animation.
            "--motion-quality",
            str(quality),
            # --lossy-quality 100 ≈ lossless palette mapping
            "--lossy-quality",
            str(quality),
            "--output",
            str(out_path),
            *png_paths,
        ]
        log.info(
            "gifski: %d frames @ %d fps q=%d → %s",
            len(frames),
            fps,
            quality,
            out_path.name,
        )
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            # Older gifski versions don't know --motion-quality / --lossy-quality.
            # Retry with just --quality if we hit that.
            stderr = (proc.stderr or "").lower()
            if (
                "unrecognized" in stderr
                or "unexpected argument" in stderr
                or "found argument" in stderr
            ):
                log.warning("gifski rejected extra flags — retrying with --quality only")
                cmd = [
                    "gifski",
                    "--fps",
                    str(fps),
                    "--quality",
                    str(quality),
                    "--output",
                    str(out_path),
                    *png_paths,
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                log.error("gifski stderr: %s", proc.stderr.strip())
                raise RuntimeError(f"gifski failed (code {proc.returncode})")


def _encode_with_pillow(
    frames: list[np.ndarray],
    out_path: Path,
    fps: int,
) -> None:
    """Last-resort encoder using Pillow's GIF saver.

    Detects whether frames are fully opaque (alpha == 255 everywhere). If so,
    uses the full 256-colour palette without a transparency index — which
    eliminates the 1-bit-alpha jaggies that hurt sticker quality. Falls back
    to 255 colours + transparent index when the input is genuinely transparent.
    """
    duration_ms = max(20, round(1000 / max(1, fps)))

    # Decide once: are all input frames opaque?
    all_opaque = all((f[..., 3] == 255).all() for f in frames)
    log.info(
        "pillow GIF: %d frames, %s mode → %s",
        len(frames),
        "opaque (256 colours)" if all_opaque else "transparent (255 colours + alpha)",
        out_path.name,
    )

    pil_frames: list[Image.Image] = []
    for arr in frames:
        img_rgba = Image.fromarray(arr, "RGBA")

        if all_opaque:
            # No alpha needed — quantise across the full 256-colour palette
            rgb = img_rgba.convert("RGB")
            p = rgb.quantize(
                colors=256,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.FLOYDSTEINBERG,
            )
            pil_frames.append(p)
        else:
            # Composite on transparent and reserve one palette slot for alpha
            alpha = img_rgba.split()[3]
            rgb = img_rgba.convert("RGB")
            p = rgb.quantize(
                colors=255,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.FLOYDSTEINBERG,
            )
            mask = Image.eval(alpha, lambda a: 255 if a < 128 else 0)
            p.paste(255, mask=mask)
            p.info["transparency"] = 255
            pil_frames.append(p)

    save_kwargs: dict = {
        "format": "GIF",
        "save_all": True,
        "append_images": pil_frames[1:],
        "duration": duration_ms,
        "loop": 0,
        "disposal": 2,
        "optimize": False,
    }
    if not all_opaque:
        save_kwargs["transparency"] = 255

    pil_frames[0].save(out_path, **save_kwargs)


def encode_gif(
    frames: list[np.ndarray],
    out_path: Path,
    fps: int = 30,
    quality: int = 95,
    prefer_gifski: bool = True,
) -> Path:
    """Encode RGBA frames to an animated GIF.

    Returns the output path on success; raises on hard failure.
    """
    if not frames:
        raise ValueError("no frames to encode")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if prefer_gifski and _gifski_available():
        _encode_with_gifski(frames, out_path, fps, quality)
    else:
        if prefer_gifski:
            log.warning("gifski not on PATH — using Pillow fallback (lower quality)")
        _encode_with_pillow(frames, out_path, fps)

    return out_path
