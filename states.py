"""FSM states + per-user transient render-job storage."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.fsm.state import State, StatesGroup

from services.user_config import UserConfig


class MainSG(StatesGroup):
    """Idle / live in main menu."""

    idle = State()


class GifSG(StatesGroup):
    """The user is in the GIF section: incoming stickers and emoji are rendered."""

    idle = State()


class PromoSG(StatesGroup):
    waiting_code = State()


class RefCaptchaSG(StatesGroup):
    """Captcha shown to a NEW user who entered via a referral link, before
    the referrer gets credited — protects against ref farming."""

    waiting_answer = State()


class BgColorSG(StatesGroup):
    waiting_hex = State()


class ResolutionSG(StatesGroup):
    waiting_custom = State()


class EmojiColorSG(StatesGroup):
    waiting_hex = State()


class MediaSG(StatesGroup):
    waiting_media = State()


class WatermarkSG(StatesGroup):
    waiting_text = State()
    waiting_color = State()
    waiting_font = State()


class ConfigsSG(StatesGroup):
    waiting_name = State()
    waiting_load_id = State()


class WalletSG(StatesGroup):
    waiting_amount_yoomoney = State()
    waiting_amount_sbp = State()
    waiting_amount_cryptobot = State()
    waiting_payment_proof = State()  # after "I've paid" (YooMoney / SBP)


class PreviewSG(StatesGroup):
    waiting_tgs = State()


class ConvertSG(StatesGroup):
    waiting_tgs = State()


class PackSG(StatesGroup):
    waiting_nick = State()
    waiting_logo = State()


# Admin
class AdminSG(StatesGroup):
    idle = State()
    waiting_user_id_balance = State()
    waiting_balance_delta = State()
    waiting_price = State()
    waiting_pack_price = State()  # ≤20 tiles (pack_small_price_rub)
    waiting_pack_logo_price = State()  # >20 tiles (pack_full_price_rub)
    waiting_pack_passport_price = State()  # passport surcharge
    waiting_free_renders = State()
    waiting_grant_amount = State()
    waiting_screen_media = State()
    waiting_notify_media = State()  # admin attaches media to a notify event
    waiting_channel_id = State()  # payments channel ID
    waiting_report_channel_id = State()  # channel for confirmed purchase reports
    waiting_ym_url = State()  # YooMoney quickpay link
    waiting_promo_balance = State()  # promo: balance amount, ₽
    waiting_promo_uses = State()  # promo: number of activations
    waiting_example_url = State()  # example pack link


class BroadcastSG(StatesGroup):
    waiting_text = State()
    waiting_media = State()
    waiting_btn_text = State()
    waiting_btn_url = State()


@dataclass(slots=True)
class RenderJob:
    """Live, in-flight render config + uploads for a single user."""

    user_id: int
    cfg: UserConfig = field(default_factory=UserConfig)

    # current TGS to be rendered (set when user uploads)
    tgs_path: Path | None = None
    bg_path: Path | None = None
    bg_kind: str = "none"  # "none" | "image" | "video"

    # transient extras for cleanup
    tmp_files: list[Path] = field(default_factory=list)

    def cleanup_uploads(self) -> None:
        for p in (self.tgs_path, *self.tmp_files):
            try:
                if p and p.exists():
                    p.unlink()
            except OSError:
                pass
        self.tgs_path = None
        self.tmp_files.clear()

    def cleanup_bg(self) -> None:
        if self.bg_path and self.bg_path.exists():
            with contextlib.suppress(OSError):
                self.bg_path.unlink()
        self.bg_path = None
        self.bg_kind = "none"

    def cleanup_all(self) -> None:
        self.cleanup_uploads()
        self.cleanup_bg()


class JobStore:
    """Per-user job store. In-memory, fine for single-process bot."""

    def __init__(self) -> None:
        self._jobs: dict[int, RenderJob] = {}

    def get(self, user_id: int) -> RenderJob:
        j = self._jobs.get(user_id)
        if j is None:
            j = RenderJob(user_id=user_id)
            self._jobs[user_id] = j
        return j

    def reset(self, user_id: int) -> None:
        j = self._jobs.pop(user_id, None)
        if j is not None:
            j.cleanup_all()

    def clear_uploads(self, user_id: int) -> None:
        j = self._jobs.get(user_id)
        if j:
            j.cleanup_uploads()


job_store = JobStore()


@dataclass(slots=True)
class PackState:
    """Per-user state of the pack creator."""

    user_id: int
    nick: str | None = None
    font: str | None = None  # font key from PACK_FONTS
    format: str | None = None  # "sticker" | "emoji"
    logo_svg: bytes | None = None  # raw SVG file bytes (after .svg upload)
    content_rotation: float = 0.0
    logo_rotation: float = 0.0
    content_color: str = "#ffffff"


class PackStore:
    def __init__(self) -> None:
        self._items: dict[int, PackState] = {}

    def get(self, user_id: int) -> PackState:
        s = self._items.get(user_id)
        if s is None:
            s = PackState(user_id=user_id)
            self._items[user_id] = s
        return s

    def reset(self, user_id: int) -> None:
        self._items.pop(user_id, None)


pack_store = PackStore()
