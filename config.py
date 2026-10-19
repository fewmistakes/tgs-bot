"""Centralised configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

load_dotenv()

BASE_DIR: Final[Path] = Path(__file__).parent.resolve()
DATA_DIR: Final[Path] = BASE_DIR / "data"
TMP_DIR: Final[Path] = DATA_DIR / "tmp"
DB_PATH: Final[Path] = DATA_DIR / "bot.db"
PROFILE_DB_PATH: Final[Path] = DATA_DIR / "profile.db"
PROMO_DB_PATH: Final[Path] = DATA_DIR / "promo.db"
REPORTS_DB_PATH: Final[Path] = DATA_DIR / "reports.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_set(value: str | None) -> set[int]:
    if not value:
        return set()
    out: set[int] = set()
    for piece in value.replace(";", ",").split(","):
        piece = piece.strip()
        if piece.lstrip("-").isdigit():
            out.add(int(piece))
    return out


def _hex_to_rgb(value: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not value:
        return default
    v = value.strip().lstrip("#")
    if len(v) != 6:
        return default
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return default


@dataclass(slots=True, frozen=True)
class Config:
    bot_token: str
    admin_ids: set[int] = field(default_factory=set)
    bot_username: str = ""

    output_width: int = 1920
    output_height: int = 530
    output_fps: int = 60
    output_format: str = "mp4"

    h264_crf: int = 23
    h264_preset: str = "slow"
    h264_max_kbps: int = 2000

    gif_quality: int = 100
    use_gifski: bool = True

    sticker_scale: float = 0.85
    supersample: float = 2.0
    max_render_seconds: float = 6.0
    workers: int = max(1, (os.cpu_count() or 2) - 1)
    matte_rgb: tuple[int, int, int] = (0, 0, 0)
    max_tgs_size_mb: int = 5
    max_bg_size_mb: int = 30

    # Defaults only: once written to the DB, the admin panel values take precedence.
    default_render_price: int = 5
    default_free_renders: int = 0
    starter_balance_kop: int = 3000

    support_username: str = ""
    privacy_url: str = ""
    terms_url: str = ""

    cryptobot_token: str = ""
    cryptobot_testnet: bool = False
    yoomoney_quickpay_url: str = ""
    yoomoney_min_amount: int = 10
    sbp_quickpay_url: str = ""
    sbp_min_amount: int = 10
    stars_rate: int = 1

    platega_merchant_id: str = ""
    platega_token: str = ""

    # Public HTTPS URL of the pack editor; the aiohttp server binds to host:port
    # and is expected to sit behind a TLS-terminating reverse proxy.
    webapp_url: str = ""
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip() else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw and raw.strip() else default


def load_config() -> Config:
    token = _str("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    fmt = _str("OUTPUT_FORMAT", "mp4").lower()
    if fmt not in {"mp4", "gif", "video"}:
        raise RuntimeError(f"OUTPUT_FORMAT must be mp4/gif/video, got {fmt!r}")

    return Config(
        bot_token=token,
        admin_ids=_int_set(os.getenv("ADMIN_IDS")),
        bot_username=_str("BOT_USERNAME").lstrip("@"),
        output_width=_int("OUTPUT_WIDTH", 1920),
        output_height=_int("OUTPUT_HEIGHT", 530),
        output_fps=_int("OUTPUT_FPS", 60),
        output_format=fmt,
        h264_crf=_int("H264_CRF", 23),
        h264_preset=_str("H264_PRESET", "slow"),
        h264_max_kbps=_int("H264_MAX_KBPS", 2000),
        gif_quality=_int("GIF_QUALITY", 100),
        use_gifski=_bool(os.getenv("USE_GIFSKI"), True),
        sticker_scale=_float("STICKER_SCALE", 0.85),
        supersample=_float("SUPERSAMPLE", 2.0),
        max_render_seconds=_float("MAX_RENDER_SECONDS", 6.0),
        workers=_int("WORKERS", max(1, (os.cpu_count() or 2) - 1)),
        matte_rgb=_hex_to_rgb(os.getenv("MATTE_COLOR"), (0, 0, 0)),
        max_tgs_size_mb=_int("MAX_TGS_SIZE_MB", 5),
        max_bg_size_mb=_int("MAX_BG_SIZE_MB", 30),
        default_render_price=_int("RENDER_PRICE", 10),
        default_free_renders=_int("FREE_RENDERS", 0),
        starter_balance_kop=_int("STARTER_BALANCE_RUB", 30) * 100,
        support_username=_str("SUPPORT_USERNAME").lstrip("@"),
        privacy_url=_str("PRIVACY_URL"),
        terms_url=_str("TERMS_URL"),
        cryptobot_token=_str("CRYPTOBOT_TOKEN"),
        cryptobot_testnet=_bool(os.getenv("CRYPTOBOT_TESTNET"), False),
        yoomoney_quickpay_url=_str("YOOMONEY_QUICKPAY_URL"),
        yoomoney_min_amount=_int("YOOMONEY_MIN_AMOUNT", 10),
        sbp_quickpay_url=_str("SBP_QUICKPAY_URL"),
        sbp_min_amount=_int("SBP_MIN_AMOUNT", 10),
        stars_rate=_int("STARS_RATE", 1),
        platega_merchant_id=_str("PLATEGA_MERCHANT_ID"),
        platega_token=_str("PLATEGA_TOKEN"),
        webapp_url=_str("WEBAPP_URL"),
        webapp_host=_str("WEBAPP_HOST", "0.0.0.0"),
        webapp_port=_int("WEBAPP_PORT", 8080),
    )
