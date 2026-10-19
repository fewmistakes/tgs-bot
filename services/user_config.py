"""User-facing render configuration.

Stored in DB as JSON (per saved preset). Also held in memory per-user for
the current "live" session via JobStore (see states.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ColorMode = Literal["off", "tint"]
BgKind = Literal["none", "image", "video"]
OutputFormat = Literal["mp4", "gif", "video"]
WatermarkPos = Literal["center", "tl", "tr", "bl", "br"]


# Preset resolutions shown in the picker
RESOLUTIONS = [
    ("1920×530 (баннер)", 1920, 530),
    ("1920×600", 1920, 600),
    ("1920×1080 (Full HD)", 1920, 1080),
    ("1080×1080 (1:1)", 1080, 1080),
    ("1080×1920 (вертикаль)", 1080, 1920),
    ("1280×720 (HD)", 1280, 720),
    ("800×800", 800, 800),
]

# Quick-pick palette for bg/emoji colours
PRESET_BG_COLORS = [
    ("Чёрный", "#000000"),
    ("Белый", "#FFFFFF"),
    ("Графит", "#1a1a1a"),
    ("Серый", "#F2E9E4"),
    ("Тёплый", "#FFEFD5"),
    ("Морской", "#0a1f44"),
    ("Лес", "#1f3a2e"),
    ("Кровь", "#3a0000"),
]

PRESET_EMOJI_COLORS = [
    ("Красный", "#FF3344"),
    ("Оранжевый", "#FF8B1A"),
    ("Жёлтый", "#FFD23F"),
    ("Зелёный", "#3DDC84"),
    ("Бирюза", "#1EC4D4"),
    ("Синий", "#2979FF"),
    ("Фиолет", "#9C27FF"),
    ("Розовый", "#FF4FA3"),
    ("Чёрный", "#1A1A1A"),
    ("Белый", "#F5F5F5"),
]


@dataclass(slots=True)
class WatermarkSettings:
    enabled: bool = False
    text: str = ""
    color: str = "#FFFFFF"
    position: WatermarkPos = "br"  # bottom-right by default
    font_key: str = "Seymour One"  # Google Fonts name, used in watermark renderer


@dataclass(slots=True)
class UserConfig:
    """Everything a user can tweak before rendering."""

    width: int = 1920
    height: int = 530
    fps: int = 60
    fmt: OutputFormat = "mp4"

    bg_color: str = "#000000"  # used when bg_kind == 'none'
    emoji_color_mode: ColorMode = "off"
    emoji_color: str | None = None  # hex like "#ff3344"

    watermark: WatermarkSettings = field(default_factory=WatermarkSettings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> UserConfig:
        wm = d.get("watermark") or {}
        return cls(
            width=int(d.get("width", 1920)),
            height=int(d.get("height", 530)),
            fps=int(d.get("fps", 60)),
            fmt=d.get("fmt", "mp4"),
            bg_color=d.get("bg_color", "#000000"),
            emoji_color_mode=d.get("emoji_color_mode", "off"),
            emoji_color=d.get("emoji_color"),
            watermark=WatermarkSettings(
                enabled=bool(wm.get("enabled", False)),
                text=wm.get("text", "") or "",
                color=wm.get("color", "#FFFFFF"),
                position=wm.get("position", "br"),
                font_key=wm.get("font_key", "Seymour One"),
            ),
        )

    def summary_html(self) -> str:
        from utils.emoji import E

        wm = "вкл" if self.watermark.enabled and self.watermark.text else "выкл"
        emoji_c = self.emoji_color if self.emoji_color_mode == "tint" and self.emoji_color else "—"
        return (
            f"{E.BRUSH} Цвет фона: <code>{self.bg_color}</code>\n"
            f"{E.RESOLUTION} Разрешение: <code>{self.width}×{self.height}</code> · "
            f"<code>{self.fps}fps</code>\n"
            f"{E.CODE} Формат: <code>{self.fmt.upper()}</code>\n"
            f"{E.MEDIA} Цвет эмодзи: <code>{emoji_c}</code>\n"
            f"{E.PENCIL} Вотермарка: <code>{wm}</code>"
        )
