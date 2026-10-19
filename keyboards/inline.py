"""All inline keyboards. Premium emojis go via icon_custom_emoji_id."""

from __future__ import annotations

from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from keyboards.cb import CB
from services.user_config import (
    PRESET_BG_COLORS,
)
from utils.emoji import EmojiID


def _btn(
    text: str, cb_or_url: str, *, emoji: str | None = None, url: bool = False
) -> InlineKeyboardButton:
    kw: dict = {"text": text}
    if url:
        kw["url"] = cb_or_url
    else:
        kw["callback_data"] = cb_or_url
    if emoji:
        kw["icon_custom_emoji_id"] = emoji
    return InlineKeyboardButton(**kw)


def _two_col(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Lay buttons out in two columns; an odd last button gets a full-width row."""
    rows: list[list[InlineKeyboardButton]] = []
    n = len(buttons)
    pairs_end = n - 1 if n % 2 == 1 else n
    i = 0
    while i < pairs_end:
        rows.append([buttons[i], buttons[i + 1]])
        i += 2
    if n % 2 == 1:
        rows.append([buttons[-1]])
    return rows


def kb_main(*, balance_rub: float, free: int) -> InlineKeyboardMarkup:
    bal_label = f"Кошелёк · {balance_rub:.0f}₽"
    if free > 0:
        bal_label += f" · {free}"

    rows = [
        [_btn(bal_label, CB.WALLET, emoji=EmojiID.WALLET)],
        [
            _btn("Gif", CB.GIF_HOME, emoji="5355313692529424034"),
            _btn("Emoji", CB.PACK_HOME, emoji="5346136511354004471"),
        ],
        [
            _btn("Прочее", CB.OTHER_HOME, emoji="5345869179704606041"),
            _btn("Информация", CB.INFO, emoji="5983568653751160844"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(target: str = CB.MAIN) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("◁  Назад", target, emoji=EmojiID.BACK),
            ]
        ]
    )


def kb_bg_picker() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for label, hexc in PRESET_BG_COLORS:
        pair.append(_btn(label, f"{CB.BG_PRESET}{hexc.lstrip('#').lower()}", emoji=EmojiID.BRUSH))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_btn("Подобрать цвет", CB.BG_CUSTOM, emoji=EmojiID.PENCIL)])
    rows.append([_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_resolution() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Ввести разрешение", CB.RES_CUSTOM, emoji=EmojiID.RESOLUTION)],
            [_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)],
        ]
    )


def kb_format(current: str) -> InlineKeyboardMarkup:
    """GIF / file format switch; the active option uses style=success."""

    def make(label: str, key: str, emoji_id: str) -> InlineKeyboardButton:
        btn = InlineKeyboardButton(
            text=label,
            callback_data=f"{CB.FMT_SET}{key}",
            icon_custom_emoji_id=emoji_id,
        )
        if current == key:
            btn = InlineKeyboardButton(
                text=label,
                callback_data=f"{CB.FMT_SET}{key}",
                icon_custom_emoji_id=emoji_id,
                style="success",
            )
        return btn

    rows = [
        [
            make("GIF", "gif", EmojiID.CODE),
            make("Файл", "mp4", EmojiID.DOWNLOAD),
        ],
        [
            _btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_media(*, has_media: bool) -> InlineKeyboardMarkup:
    if has_media:
        rows = [
            [
                _btn("Загрузить медиа", CB.MEDIA_UPLOAD, emoji="5963103826075456248"),
                _btn("Удалить медиа", CB.MEDIA_DELETE, emoji="5841541824803509441"),
            ],
            [_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)],
        ]
    else:
        rows = [
            [_btn("Загрузить медиа", CB.MEDIA_UPLOAD, emoji="5963103826075456248")],
            [_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_emoji_color(*, has_color: bool) -> InlineKeyboardMarkup:
    """Colour palette web app; HEX can also be typed into the chat."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Открыть палитру",
                web_app=WebAppInfo(url="https://htmlcolorcodes.com/color-picker/"),
                icon_custom_emoji_id="5814690801665446789",
            )
        ],
    ]
    if has_color:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Убрать цвет",
                    callback_data=CB.EMC_OFF,
                    icon_custom_emoji_id="5841541824803509441",
                )
            ]
        )
    rows.append([_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_configs(presets: Iterable[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in presets:
        rows.append(
            [
                _btn(p["name"], f"{CB.CFG_LOAD}{p['id']}", emoji=EmojiID.APPS),
                _btn("✕", f"{CB.CFG_DELETE}{p['id']}"),
            ]
        )
    rows.append([_btn("Сохранить текущие", CB.CFG_SAVE_NEW, emoji=EmojiID.CHECK)])
    rows.append([_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


WM_FONTS = [("Жирный", "bold"), ("Обычный", "regular"), ("Серифный", "serif"), ("Моно", "mono")]
WM_POSITIONS = [("← ↑", "tl"), ("↑ →", "tr"), ("← ↓", "bl"), ("↓ →", "br"), ("Центр", "center")]


def kb_watermark(*, enabled: bool, has_text: bool) -> InlineKeyboardMarkup:
    toggle_label = "Выключить" if enabled else "Включить"
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn("Текст", CB.WM_TEXT, emoji=EmojiID.WRITE),
            _btn("Цвет", CB.WM_COLOR, emoji=EmojiID.BRUSH),
        ],
    ]
    pos_row1 = [
        _btn(label, f"{CB.WM_POS}{key}", emoji=EmojiID.GEO) for label, key in WM_POSITIONS[:3]
    ]
    pos_row2 = [
        _btn(label, f"{CB.WM_POS}{key}", emoji=EmojiID.GEO) for label, key in WM_POSITIONS[3:]
    ]
    rows.append(pos_row1)
    rows.append(pos_row2)
    fonts_row = [
        _btn(label, f"{CB.WM_FONT}{key}", emoji=EmojiID.FONT) for label, key in WM_FONTS[:2]
    ]
    fonts_row2 = [
        _btn(label, f"{CB.WM_FONT}{key}", emoji=EmojiID.FONT) for label, key in WM_FONTS[2:]
    ]
    rows.append(fonts_row)
    rows.append(fonts_row2)
    if has_text:
        rows.append(
            [
                _btn(
                    toggle_label,
                    CB.WM_TOGGLE,
                    emoji=EmojiID.CHECK if not enabled else EmojiID.CROSS,
                )
            ]
        )
        rows.append([_btn("Очистить", CB.WM_CLEAR, emoji=EmojiID.TRASH)])
    rows.append([_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_wallet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("СБП", CB.PAY_SBP, emoji="5318972874726339331"),
                _btn("CryptoBot", CB.PAY_CRYPTO, emoji="5345833033259834487"),
            ],
            [_btn("◁  Назад", CB.MAIN, emoji=EmojiID.BACK)],
        ]
    )


def kb_amount_picker(provider: str, *, min_amount: int = 10) -> InlineKeyboardMarkup:
    """Standard quick-pick amounts."""
    options = [a for a in (10, 25, 50, 100, 250, 500, 1000) if a >= min_amount]
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for amt in options:
        pair.append(_btn(f"{amt}₽", f"{CB.PAY_AMOUNT}{provider}:{amt}", emoji=EmojiID.MONEY))
        if len(pair) == 3:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_btn("◁  Назад", CB.WALLET, emoji=EmojiID.BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_yoomoney_pay(pay_url: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Перейти к оплате", pay_url, emoji=EmojiID.MONEY, url=True)],
            [_btn("Я оплатил", f"{CB.PAY_CONFIRM}{payment_id}", emoji=EmojiID.CHECK)],
            [_btn("Отмена", CB.WALLET, emoji=EmojiID.CROSS)],
        ]
    )


def kb_cryptobot_pay(pay_url: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Оплатить в CryptoBot", pay_url, emoji=EmojiID.CRYPTOBOT, url=True)],
            [_btn("Проверить оплату", f"{CB.PAY_RECHECK}{payment_id}", emoji=EmojiID.LOADING)],
            [_btn("Отмена", CB.WALLET, emoji=EmojiID.CROSS)],
        ]
    )


def kb_admin_payment_review(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("Подтвердить", f"{CB.PAY_ADMIN_OK}{payment_id}", emoji=EmojiID.CHECK),
                _btn("Отклонить", f"{CB.PAY_ADMIN_NO}{payment_id}", emoji=EmojiID.CROSS),
            ],
        ]
    )


def kb_after_render() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("В меню", CB.MAIN, emoji=EmojiID.HOME),
            ]
        ]
    )


def kb_convert_ready() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("Собрать", CB.CONVERT_NOW, emoji=EmojiID.PARTY)],
            [_btn("◁  Назад", CB.GIF_HOME, emoji=EmojiID.BACK)],
        ]
    )


def kb_admin_home() -> InlineKeyboardMarkup:
    from services.content import btn

    buttons = [
        _btn(btn("admin.btn.stats"), CB.ADM_STATS, emoji=EmojiID.STATS),
        _btn(btn("admin.btn.users"), CB.ADM_USERS, emoji=EmojiID.PEOPLE),
        _btn(btn("admin.btn.prices"), CB.ADM_PRICES, emoji=EmojiID.MONEY),
        _btn(btn("admin.btn.gifs"), CB.ADM_GIFS, emoji=EmojiID.MEDIA_PHOTO),
        _btn(btn("admin.btn.grant"), CB.ADM_GRANT, emoji=EmojiID.GIFT),
        _btn(btn("admin.btn.broadcast"), CB.ADM_BROADCAST, emoji=EmojiID.MEGAPHONE),
        _btn(btn("admin.btn.content"), "a:content", emoji=EmojiID.WRITE),
        _btn("Примеры паков", CB.ADM_EXAMPLES, emoji=EmojiID.LINK),
        _btn("Медиа уведомлений", CB.ADM_NOTIFY, emoji=EmojiID.MEDIA_PHOTO),
        _btn("Ссылка Рублями", CB.ADM_YM_URL, emoji="5879814368572478751"),
        _btn("Добавить канал", "adm:channel:set", emoji=EmojiID.BELL),
        _btn("Канал отчётов", "adm:report:set", emoji=EmojiID.STATS),
        _btn("Промокоды", CB.ADM_PROMO, emoji="5346044461614924803"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=_two_col(buttons))


def kb_admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK),
            ]
        ]
    )


def kb_admin_users() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=_two_col(
            [
                _btn(
                    "Найти пользователя",
                    CB.ADM_USER_FIND,
                    emoji=EmojiID.PROFILE if hasattr(EmojiID, "PROFILE") else EmojiID.SETTINGS,
                ),
                _btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK),
            ]
        )
    )


def kb_admin_prices() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=_two_col(
            [
                _btn("Цена рендера", CB.ADM_PRICE_SET, emoji=EmojiID.MONEY),
                _btn("Пак ≤20 тайлов", CB.ADM_PACK_PRICE_SET, emoji=EmojiID.MONEY),
                _btn("Пак >20 тайлов", CB.ADM_PACK_LOGO_PRICE_SET, emoji=EmojiID.MONEY),
                _btn("Доплата за паспорт", CB.ADM_PACK_PASSPORT_PRICE_SET, emoji=EmojiID.MONEY),
                _btn("Бесплатных при старте", CB.ADM_FREE_SET, emoji=EmojiID.GIFT),
                _btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK),
            ]
        )
    )


def kb_admin_grant() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=_two_col(
            [
                _btn("+рендеров всем", CB.ADM_GRANT_FREE, emoji=EmojiID.GIFT),
                _btn("+рублей всем", CB.ADM_GRANT_RUB, emoji=EmojiID.MONEY),
                _btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK),
            ]
        )
    )


def kb_admin_gifs(media_map: dict[str, str]) -> InlineKeyboardMarkup:
    """media_map: screen_key -> 'set' | 'unset' status display."""
    from services.screens import SCREEN_LABELS

    buttons: list[InlineKeyboardButton] = []
    for key, label in SCREEN_LABELS.items():
        marker = "✓" if media_map.get(key) == "set" else "—"
        buttons.append(
            _btn(f"{marker}  {label}", f"{CB.ADM_GIF_SET}{key}", emoji=EmojiID.MEDIA_PHOTO)
        )
    buttons.append(_btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK))
    return InlineKeyboardMarkup(inline_keyboard=_two_col(buttons))


def kb_admin_user_card(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=_two_col(
            [
                _btn("Изменить баланс", f"adm:bal:{user_id}", emoji=EmojiID.MONEY),
                _btn("◁  Назад", CB.ADM_USERS, emoji=EmojiID.BACK),
            ]
        )
    )


def kb_admin_gif_actions(screen_key: str, has_media: bool) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = [
        _btn("Прикрепить новую", f"{CB.ADM_GIF_SET}{screen_key}", emoji=EmojiID.UPLOAD),
    ]
    if has_media:
        buttons.append(_btn("Удалить", f"{CB.ADM_GIF_DEL}{screen_key}", emoji=EmojiID.TRASH))
    buttons.append(_btn("◁  Назад", CB.ADM_GIFS, emoji=EmojiID.BACK))
    return InlineKeyboardMarkup(inline_keyboard=_two_col(buttons))


def kb_admin_notify(events: dict[str, str], media_map: dict[str, str]) -> InlineKeyboardMarkup:
    """events: {event_key: human_label}; media_map: {event_key: 'set'|'unset'}."""
    buttons: list[InlineKeyboardButton] = []
    for key, label in events.items():
        marker = "✓" if media_map.get(key) == "set" else "—"
        buttons.append(
            _btn(f"{marker}  {label}", f"{CB.ADM_NOTIFY_SET}{key}", emoji=EmojiID.MEDIA_PHOTO)
        )
    buttons.append(_btn("◁  Назад", CB.ADM_HOME, emoji=EmojiID.BACK))
    return InlineKeyboardMarkup(inline_keyboard=_two_col(buttons))


def kb_admin_notify_actions(event_key: str, has_media: bool) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = [
        _btn("Прикрепить медиа", f"{CB.ADM_NOTIFY_SET}{event_key}", emoji=EmojiID.UPLOAD),
    ]
    if has_media:
        buttons.append(_btn("Удалить", f"{CB.ADM_NOTIFY_DEL}{event_key}", emoji=EmojiID.TRASH))
    buttons.append(_btn("◁  Назад", CB.ADM_NOTIFY, emoji=EmojiID.BACK))
    return InlineKeyboardMarkup(inline_keyboard=_two_col(buttons))


def kb_cancel(to: str = CB.MAIN) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("Отмена", to, emoji=EmojiID.CROSS),
            ]
        ]
    )
