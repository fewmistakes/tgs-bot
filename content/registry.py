"""Default texts and button labels for every user-facing string.

All of them can be overridden from /admin → «Тексты и кнопки».
"""

from __future__ import annotations

from dataclasses import dataclass


class Cat:
    MAIN = "main"
    BG = "bg"
    RES = "res"
    FMT = "fmt"
    MEDIA = "media"
    EMOJI = "emoji"
    CONFIGS = "configs"
    WM = "wm"
    PREVIEW = "preview"
    WALLET = "wallet"
    PAY = "pay"
    CONVERT = "convert"
    ERRORS = "errors"
    ADMIN = "admin"
    PACK = "pack"


CATEGORY_LABELS: dict[str, str] = {
    Cat.MAIN: "Главное меню",
    Cat.BG: "Цвет фона",
    Cat.RES: "Разрешение",
    Cat.FMT: "Формат",
    Cat.MEDIA: "Своя медиа",
    Cat.EMOJI: "Цвет эмодзи",
    Cat.CONFIGS: "Конфиги",
    Cat.WM: "Вотермарка",
    Cat.PREVIEW: "Предосмотр",
    Cat.WALLET: "Кошелёк",
    Cat.PAY: "Платежи",
    Cat.CONVERT: "Рендер",
    Cat.ERRORS: "Ошибки",
    Cat.ADMIN: "Админка",
    Cat.PACK: "Создать пак",
}


@dataclass(slots=True, frozen=True)
class Item:
    key: str
    category: str
    default: str
    description: str = ""
    placeholders: tuple[str, ...] = ()


# House style for every text below: bold, short, no filler.
ITEMS: list[Item] = [
    Item(
        "main.text",
        Cat.MAIN,
        "<b>{bot_emoji} Swex</b>\n"
        "<i>Твой инструмент для безупречного оформления</i>\n\n"
        "{profile_emoji} <b>Профиль:</b>\n"
        "<blockquote><b>"
        '<tg-emoji emoji-id="5355313692529424034">🎞</tg-emoji> '
        "GIF создано: <code>{renders}</code>\n"
        '<tg-emoji emoji-id="5346136511354004471">🙂</tg-emoji> '
        "Паков создано: <code>{packs}</code>\n"
        '<tg-emoji emoji-id="5870921681735781843">📊</tg-emoji> '
        "Место в топе: <code>{rank}</code> из <code>{total_users}</code>\n"
        '<tg-emoji emoji-id="5258513401784573443">🤝</tg-emoji> '
        "Рефералов: <code>{refs}</code>"
        "</b></blockquote>"
        "{media_line}",
        "Главное меню",
        (
            "renders",
            "packs",
            "rank",
            "total_users",
            "refs",
            "media_line",
            "bot_emoji",
            "upload_emoji",
            "settings_emoji",
            "profile_emoji",
        ),
    ),
    Item("main.btn.wallet", Cat.MAIN, "Кошелёк · {balance}₽", placeholders=("balance",)),
    Item("main.btn.bg", Cat.MAIN, "Цвет фона"),
    Item("main.btn.res", Cat.MAIN, "Разрешение"),
    Item("main.btn.fmt", Cat.MAIN, "Формат"),
    Item("main.btn.media", Cat.MAIN, "Своя медиа"),
    Item("main.btn.emoji", Cat.MAIN, "Цвет Emoji"),
    Item("main.btn.configs", Cat.MAIN, "Конфиги"),
    Item("main.btn.watermark", Cat.MAIN, "Вотермарка"),
    Item("main.btn.preview", Cat.MAIN, "Предосмотр"),
    Item("main.btn.back_home", Cat.MAIN, "В меню"),
    Item("main.btn.back", Cat.MAIN, "Назад"),
    Item(
        "bg.text",
        Cat.BG,
        '<tg-emoji emoji-id="5924961243721896074">🖌</tg-emoji> '
        "<b>Введи новый HEX-цвет для фона типо:</b>\n"
        "<blockquote><b>FFFFFF — белый\n"
        "000000 — чёрный</b></blockquote>",
        "Цвет фона. Доступно: {current}",
        ("current",),
    ),
    Item("bg.btn.custom", Cat.BG, "Подобрать цвет"),
    Item(
        "bg.applied",
        Cat.BG,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Фон: <code>{hex}</code></b>',
        placeholders=("hex",),
    ),
    Item(
        "bg.prompt",
        Cat.BG,
        '<tg-emoji emoji-id="5924961243721896074">🖌</tg-emoji> '
        "<b>Введи HEX-код цвета фона:</b>\n"
        "<blockquote><b>FFFFFF — белый\n"
        "000000 — чёрный</b></blockquote>",
        "Запрос HEX-кода",
    ),
    Item(
        "res.text",
        Cat.RES,
        '<tg-emoji emoji-id="5778479949572738874">↔️</tg-emoji> '
        "<b>Введи новое разрешение:</b>\n"
        "<blockquote><b>1920×600 или 1920×530\n"
        "2.35:1 или 16:9 или 1:1</b></blockquote>\n\n"
        '<tg-emoji emoji-id="5778479949572738874">↔️</tg-emoji> '
        "<b>Сейчас: {w}×{h}</b>",
        "Разрешение",
        ("w", "h"),
    ),
    Item(
        "fmt.text",
        Cat.FMT,
        '<tg-emoji emoji-id="5940433880585605708">🔨</tg-emoji> <b>Выбери формат вывода:</b>',
        "Формат вывода",
    ),
    Item("fmt.btn.gif", Cat.FMT, "GIF"),
    Item("fmt.btn.mp4", Cat.FMT, "Файл"),
    Item(
        "media.text",
        Cat.MEDIA,
        '<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> <b>Своя медиа — для рендера</b>',
        "Экран собственной медиа",
    ),
    Item("media.btn.upload", Cat.MEDIA, "Загрузить медиа"),
    Item("media.btn.delete", Cat.MEDIA, "Удалить медиа"),
    Item(
        "media.prompt",
        Cat.MEDIA,
        '<tg-emoji emoji-id="5963103826075456248">⬆️</tg-emoji> '
        "<b>Пришли мне фото, видео или GIF</b>\n"
        "<blockquote><b>Любая ориентация — подгоню под выбранное разрешение.</b></blockquote>",
    ),
    Item(
        "media.applied",
        Cat.MEDIA,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Медиа принята.</b>',
    ),
    Item(
        "emoji.text",
        Cat.EMOJI,
        '<tg-emoji emoji-id="5814690801665446789">🖌</tg-emoji> '
        "<b>Введи HEX-цвет шоб перекрасить emoji/sticker при рендере:</b>\n"
        "<blockquote><b>FFFFFF — белый\n"
        "F2E9E4 — серый</b></blockquote>",
        "Цвет эмодзи",
    ),
    Item("emoji.btn.custom", Cat.EMOJI, "Подобрать цвет"),
    Item("emoji.btn.off", Cat.EMOJI, "Убрать цвет"),
    Item(
        "emoji.applied",
        Cat.EMOJI,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Цвет: <code>{hex}</code></b>',
        placeholders=("hex",),
    ),
    Item(
        "configs.text",
        Cat.CONFIGS,
        '<tg-emoji emoji-id="5452165780579843515">📦</tg-emoji> <b>Конфиги</b>\n\n{state_line}',
        "Список пресетов",
        ("state_line",),
    ),
    Item(
        "configs.empty",
        Cat.CONFIGS,
        "<blockquote><b>Пусто. Сохрани текущие настройки и переключайся "
        "между ними одним тапом.</b></blockquote>",
    ),
    Item(
        "configs.has_some",
        Cat.CONFIGS,
        "<blockquote><b>Сохранено: {count}\n"
        "Жми на пресет чтобы загрузить, ✕ — удалить.</b></blockquote>",
        placeholders=("count",),
    ),
    Item("configs.btn.save", Cat.CONFIGS, "Сохранить текущие"),
    Item(
        "configs.prompt_name",
        Cat.CONFIGS,
        '<tg-emoji emoji-id="5870753782874246579">✍</tg-emoji> '
        "<b>Назови пресет</b>\n"
        "<blockquote><b>Например: Лого красное, Баннер 16:9, "
        "Тёмная вотермарка</b></blockquote>",
    ),
    Item(
        "configs.saved",
        Cat.CONFIGS,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        "<b>Сохранено как <code>{name}</code> (#{id})</b>",
        placeholders=("name", "id"),
    ),
    Item(
        "wm.text.empty",
        Cat.WM,
        '<tg-emoji emoji-id="5346198045350459046">©</tg-emoji> '
        "<b>Вотермарка не задана. Создайте название чтобы включить.</b>",
        "Когда текст не задан",
    ),
    Item(
        "wm.text.set",
        Cat.WM,
        '<tg-emoji emoji-id="5346198045350459046">©</tg-emoji> '
        "<b>Вотермарка: <code>{text}</code></b>\n\n"
        "<blockquote>"
        "<b>Шрифт: <code>{font}</code>\n"
        "Цвет: <code>{color}</code>\n"
        "Позиция: <code>{position}</code>\n"
        "Статус: <code>{status}</code></b>"
        "</blockquote>",
        "Когда текст задан",
        ("text", "font", "color", "position", "status"),
    ),
    Item("wm.btn.text", Cat.WM, "Название"),
    Item("wm.btn.font", Cat.WM, "Шрифт"),
    Item("wm.btn.color", Cat.WM, "Цвет"),
    Item("wm.btn.pos", Cat.WM, "Позиция"),
    Item("wm.btn.toggle_on", Cat.WM, "Включить"),
    Item("wm.btn.toggle_off", Cat.WM, "Выключить"),
    Item(
        "wm.prompt_text",
        Cat.WM,
        '<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> '
        "<b>Введи название вотермарки на любом языке</b>",
    ),
    Item(
        "wm.prompt_font",
        Cat.WM,
        '<tg-emoji emoji-id="5870801517140775623">I</tg-emoji> '
        "<b>Введи название шрифта английское, как на Google Fonts:</b>\n"
        "<blockquote><b>Например: Seymour One, Rubik Spray Paint, "
        "Montserrat, Press Start 2P.</b></blockquote>",
    ),
    Item(
        "wm.prompt_color",
        Cat.WM,
        '<tg-emoji emoji-id="5870676941614354370">🖋</tg-emoji> '
        "<b>Введи HEX-цвет вотермарки:</b>\n"
        "<blockquote><b>FFFFFF — белый\n"
        "F2E9E4 — серый\n"
        "000000 — чёрный</b></blockquote>",
    ),
    Item(
        "wm.prompt_pos",
        Cat.WM,
        '<tg-emoji emoji-id="6042011682497106307">📍</tg-emoji> <b>Выбери позицию вотермарки</b>',
    ),
    Item(
        "wm.text_set",
        Cat.WM,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        "<b>Название: <code>{text}</code></b>",
        placeholders=("text",),
    ),
    Item(
        "wm.font_set",
        Cat.WM,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Шрифт: <code>{font}</code></b>',
        placeholders=("font",),
    ),
    Item(
        "wm.color_set",
        Cat.WM,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Цвет: <code>{hex}</code></b>',
        placeholders=("hex",),
    ),
    Item("wm.btn.gfonts", Cat.WM, "Открыть Google Fonts"),
    Item("wm.btn.picker", Cat.WM, "Подобрать цвет"),
    Item(
        "preview.text",
        Cat.PREVIEW,
        '<tg-emoji emoji-id="5346297083001339175">👁</tg-emoji> '
        "<b>Режим предосмотра</b>\n\n"
        "<blockquote><b>Отправь одно эмодзи или стикер и покажу рендер "
        "с твоими настройками. По центру будет водяной знак @{bot_username} "
        '"вне предосмотра в рендере его не будет"</b></blockquote>',
        "Промпт превью",
        ("bot_username",),
    ),
    Item("preview.btn.cancel", Cat.PREVIEW, "Отмена"),
    Item(
        "preview.caption",
        Cat.PREVIEW,
        '<tg-emoji emoji-id="5346297083001339175">👁</tg-emoji> '
        "<b>Превью</b>\n"
        "<blockquote><b>Это бесплатно. Вотермарка @{bot_username} — "
        "только в режиме предосмотра.</b></blockquote>",
        "Подпись к превью",
        ("bot_username",),
    ),
    Item(
        "wallet.text",
        Cat.WALLET,
        '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji> '
        "<b>Ваш баланс: {balance}₽</b>\n"
        '<tg-emoji emoji-id="5870657884844462243">🏷</tg-emoji> '
        "<b>Цена рендера: {price}₽</b>\n\n"
        "<blockquote><b>Выбери способ пополнения:</b></blockquote>",
        "Кошелёк",
        ("balance", "price"),
    ),
    Item("wallet.btn.yoomoney", Cat.WALLET, "Прямой перевод"),
    Item("wallet.btn.crypto", Cat.WALLET, "CryptoBot"),
    Item("wallet.btn.stars", Cat.WALLET, "Telegram Stars"),
    Item(
        "wallet.balance_credited",
        Cat.WALLET,
        '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji> '
        "<b>Вам начислен баланс</b>\n\n"
        "<blockquote>"
        '<tg-emoji emoji-id="5870930636742595124">📊</tg-emoji> '
        "<b>Начислено: {amount}₽</b>\n"
        '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji> '
        "<b>Баланс: {new_balance}₽</b>"
        "</blockquote>",
        "Уведомление о начислении",
        ("amount", "new_balance"),
    ),
    Item("wallet.btn.back_to_menu", Cat.WALLET, "В меню"),
    Item(
        "pay.ym.intro",
        Cat.PAY,
        '<tg-emoji emoji-id="5318972874726339331">🪙</tg-emoji> '
        "<b>Прямой перевод</b>\n"
        "<blockquote><b>Минимальная сумма — {min}₽. После оплаты нажми "
        "«Я оплатил» — заявка уйдёт админу на подтверждение.</b></blockquote>",
        placeholders=("min",),
    ),
    Item(
        "pay.crypto.intro",
        Cat.PAY,
        '<tg-emoji emoji-id="5345833033259834487">👾</tg-emoji> '
        "<b>Пополнение через CryptoBot</b>\n"
        "<blockquote><b>Оплата в USDT по текущему курсу. "
        "Зачисление автоматическое.</b></blockquote>",
    ),
    Item(
        "pay.stars.intro",
        Cat.PAY,
        '<tg-emoji emoji-id="5994495149336434048">⭐</tg-emoji> '
        "<b>Пополнение через Telegram Stars</b>\n"
        "<blockquote><b>Курс: 1 ⭐ = {rate}₽. Зачисление автоматическое.</b></blockquote>",
        placeholders=("rate",),
    ),
    Item(
        "pay.ym.created",
        Cat.PAY,
        '<tg-emoji emoji-id="5318972874726339331">🪙</tg-emoji> '
        "<b>Оплата на {amount}₽</b>\n\n"
        "<blockquote><b>1. Жми «Перейти к оплате»\n"
        "2. Оплати в ЮMoney\n"
        "3. Вернись и нажми «Я оплатил» — заявка уйдёт админу.</b></blockquote>",
        placeholders=("amount",),
    ),
    Item(
        "pay.crypto.created",
        Cat.PAY,
        '<tg-emoji emoji-id="5345833033259834487">👾</tg-emoji> '
        "<b>CryptoBot · {amount}₽</b>\n\n"
        "<blockquote><b>К оплате: {usdt} USDT\n"
        "Курс: 1 USDT ≈ {rate}₽\n\n"
        "После оплаты жми «Проверить оплату».</b></blockquote>",
        placeholders=("amount", "usdt", "rate"),
    ),
    Item("pay.btn.go", Cat.PAY, "Перейти к оплате"),
    Item("pay.btn.crypto_go", Cat.PAY, "Оплатить в CryptoBot"),
    Item("pay.btn.confirmed", Cat.PAY, "Я оплатил"),
    Item("pay.btn.recheck", Cat.PAY, "Проверить оплату"),
    Item("pay.btn.cancel", Cat.PAY, "Отмена"),
    Item(
        "pay.user_confirmed",
        Cat.PAY,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        "<b>Заявка отправлена.</b>\n"
        "<blockquote><b>Админ проверит и зачислит {amount}₽ на баланс.</b></blockquote>",
        placeholders=("amount",),
    ),
    Item(
        "pay.payment_rejected",
        Cat.PAY,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Заявка на пополнение отклонена</b>\n"
        "<blockquote><b>Если ты оплатил — напиши админу с подтверждением.</b></blockquote>",
    ),
    Item(
        "convert.downloading",
        Cat.CONVERT,
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> <b>Скачиваю стикер…</b>',
    ),
    Item(
        "convert.rendering",
        Cat.CONVERT,
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        "<b>Рендерю…</b>\n"
        "<blockquote><b>Это займёт 5–30 секунд.</b></blockquote>",
    ),
    Item(
        "convert.preview_rendering",
        Cat.CONVERT,
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> <b>Рендерю превью…</b>',
    ),
    Item(
        "convert.success",
        Cat.CONVERT,
        '<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> '
        "<b>Готово!</b>\n"
        "<blockquote><b>{w}×{h} · {fps}fps · {size}KB · {fmt}\n"
        "Списано: {cost}</b></blockquote>",
        placeholders=("w", "h", "fps", "size", "fmt", "cost"),
    ),
    Item("convert.cost.free", Cat.CONVERT, "бесплатно"),
    Item("convert.cost.paid", Cat.CONVERT, "{price}₽", placeholders=("price",)),
    Item(
        "convert.no_funds",
        Cat.CONVERT,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Недостаточно средств</b>\n\n"
        "<blockquote><b>Баланс: {balance}₽\n"
        "Нужно: {price}₽</b></blockquote>\n\n"
        '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji> '
        "<b>Пополни кошелёк через меню.</b>",
        placeholders=("balance", "price"),
    ),
    Item(
        "convert.refunded",
        Cat.CONVERT,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Ошибка рендера. Деньги возвращены на баланс.</b>\n"
        "<blockquote><code>{error}</code></blockquote>",
        placeholders=("error",),
    ),
    Item(
        "errors.bad_hex",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Не похоже на HEX</b>\n"
        "<blockquote><b>Пример: <code>#FF3344</code></b></blockquote>",
    ),
    Item(
        "errors.bad_number",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Не число</b>',
    ),
    Item(
        "errors.too_big",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Слишком большой файл</b>\n"
        "<blockquote><b>Лимит: {limit} МБ.</b></blockquote>",
        placeholders=("limit",),
    ),
    Item(
        "errors.bad_format",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> <b>Не подходит формат.</b>',
    ),
    Item(
        "errors.no_sticker",
        Cat.ERRORS,
        '<tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> '
        "<b>Пришли анимированный стикер, premium-эмодзи или TGS-файл.</b>",
    ),
    Item(
        "errors.static_sticker",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Это статичный стикер. Нужен анимированный (TGS).</b>",
    ),
    Item(
        "errors.video_sticker",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Это видео-стикер (WebM), а нужен анимированный TGS.</b>\n"
        "<blockquote><b>Пришли стикер из другого пака или "
        "анимированное эмодзи.</b></blockquote>",
    ),
    Item(
        "errors.elsewhere",
        Cat.ERRORS,
        '<tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> '
        "<b>Чтобы отрендерить, открой раздел <code>Gif</code> в главном меню.</b>",
    ),
    Item(
        "errors.cancelled",
        Cat.ERRORS,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> <b>Сброшено.</b>',
    ),
    Item(
        "admin.home",
        Cat.ADMIN,
        '<tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> <b>Админ-панель</b>',
    ),
    Item("admin.btn.stats", Cat.ADMIN, "Статистика"),
    Item("admin.btn.users", Cat.ADMIN, "Пользователи"),
    Item("admin.btn.prices", Cat.ADMIN, "Цены"),
    Item("admin.btn.gifs", Cat.ADMIN, "GIF на экранах"),
    Item("admin.btn.grant", Cat.ADMIN, "Подарок всем"),
    Item("admin.btn.broadcast", Cat.ADMIN, "Рассылка"),
    Item("admin.btn.content", Cat.ADMIN, "Тексты и кнопки"),
    Item(
        "pack.home",
        Cat.PACK,
        '<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> '
        "<b>Создать пак</b>\n"
        "<blockquote><b>Открой редактор — там настроишь ник или лого, "
        "цвет, размер и положение каждого эмодзи, паспорт и формат пака "
        "(эмодзи или стикеры). Имя пака и сборка — тоже внутри.</b>"
        "</blockquote>",
        "Главный экран создания пака",
    ),
    # Example pack links shown on the home screen — admin-editable URLs.
    Item("pack.example.label", Cat.PACK, "пример"),
    Item(
        "pack.example.nick_url",
        Cat.PACK,
        "https://t.me/addemoji/swex_7174420_ddab03_by_swexmakerbot",
        "Ссылка на пример пака с ником",
    ),
    Item(
        "pack.example.logo_url",
        Cat.PACK,
        "https://t.me/addemoji/afluence_7174420_a1b3f2_by_swexmakerbot",
        "Ссылка на пример пака с лого",
    ),
    Item(
        "pack.example.sticker_url",
        Cat.PACK,
        "https://t.me/addstickers/swex_7174420_02be4c_by_swexmakerbot",
        "Ссылка на пример стикер-пака",
    ),
    Item("pack.btn.nick", Cat.PACK, "Ник / имя"),
    Item("pack.btn.font", Cat.PACK, "Шрифт"),
    Item("pack.btn.format", Cat.PACK, "Формат"),
    Item("pack.btn.logo", Cat.PACK, "Лого"),
    Item("pack.btn.preview", Cat.PACK, "Предосмотр"),
    Item("pack.btn.editor", Cat.PACK, "Открыть редактор"),
    # Preview
    Item(
        "pack.preview.progress",
        Cat.PACK,
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> <b>Готовлю предосмотр…</b>',
        "Прогресс предосмотра",
    ),
    Item(
        "pack.preview.caption",
        Cat.PACK,
        '<tg-emoji emoji-id="6037397706505195857">👁</tg-emoji> '
        "<b>Предосмотр пака</b>\n"
        "<blockquote><b>9 стикеров из 144 — как они будут выглядеть с твоим "
        "{kind}. Вотермарк <code>@{bot_username}</code> только в превью; "
        "в реальном паке его не будет.</b></blockquote>",
        "Подпись к превью",
        ("kind", "bot_username"),
    ),
    Item("pack.preview.kind.nick", Cat.PACK, "ником"),
    Item("pack.preview.kind.logo", Cat.PACK, "лого"),
    Item(
        "pack.preview.empty",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Сначала задай ник или загрузи лого</b>\n"
        "<blockquote><b>Без них предосмотр будет таким же, как сейчас "
        "стандартный пример.</b></blockquote>",
        "Когда ни ника, ни лого нет",
    ),
    Item(
        "pack.preview.error",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Не получилось собрать предосмотр</b>\n"
        "<blockquote><b>{detail}</b></blockquote>",
        "Ошибка предосмотра",
        ("detail",),
    ),
    # Nickname
    Item(
        "pack.nick.prompt",
        Cat.PACK,
        '<tg-emoji emoji-id="5812150667812280629">📝</tg-emoji> '
        "<b>Введи ник или имя пака</b>\n"
        "<blockquote><b>Этот текст заменит «swex» в текстовом паке.\n"
        "Если у тебя лого, он станет названием пака.\n"
        "Лимит: 1–30 символов.</b></blockquote>",
        "Запрос ника",
    ),
    Item(
        "pack.nick.set",
        Cat.PACK,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        "<b>Ник сохранён</b>\n"
        "<blockquote><b>Ник: <code>{nick}</code>\n"
        "Будет использован в паке и его названии.</b></blockquote>",
        "Ник применён",
        ("nick",),
    ),
    # Font
    Item(
        "pack.font.prompt",
        Cat.PACK,
        '<tg-emoji emoji-id="5400214560542060148">🔡</tg-emoji> '
        "<b>Выбери шрифт</b>\n"
        "<blockquote><b>Этим шрифтом будет нарисован твой ник на каждом "
        "эмодзи. Активный шрифт зелёный.</b></blockquote>",
        "Список шрифтов",
    ),
    # Format
    Item(
        "pack.format.prompt",
        Cat.PACK,
        '<tg-emoji emoji-id="5884366771913233289">📦</tg-emoji> '
        "<b>Выбери формат пака</b>\n"
        "<blockquote><b>Стикер — добавляется в обычный набор стикеров "
        "(120 штук, лимит Telegram).\n"
        "Эмодзи — кастомный premium-эмодзи в чате (144 штуки).\n"
        "Активный формат зелёный.</b></blockquote>",
        "Стикер vs эмодзи",
    ),
    Item("pack.format.btn.sticker", Cat.PACK, "Стикер"),
    Item("pack.format.btn.emoji", Cat.PACK, "Эмодзи"),
    # Logo
    Item(
        "pack.logo.prompt",
        Cat.PACK,
        '<tg-emoji emoji-id="6021792097454002931">🎨</tg-emoji> '
        "<b>Лого</b>\n"
        "<blockquote><b>Пришли файл логотипа в формате <code>.svg</code>. "
        "Я подставлю его на каждый эмодзи вместо текста.\n"
        "Для точной посадки лого лучше открыть редактор.\n"
        "Если SVG нет — нажми «Конвертировать в SVG», на сайте получится "
        "файл, и пришли его сюда.</b></blockquote>",
        "Запрос SVG-логотипа",
    ),
    Item("pack.logo.btn.convert", Cat.PACK, "Конвертировать в SVG"),
    Item("pack.logo.btn.delete", Cat.PACK, "Удалить лого"),
    Item(
        "pack.logo.downloading",
        Cat.PACK,
        "<blockquote>"
        '<tg-emoji emoji-id="5346149211572304778">🔄</tg-emoji> '
        "<b>Скачиваю лого</b>"
        "</blockquote>",
        "Промежуточный статус, пока качаем SVG",
    ),
    Item(
        "pack.logo.set",
        Cat.PACK,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        "<b>Лого применено</b>\n"
        "<blockquote><b>Файл: <code>{name}</code>\n"
        "Найдено путей: <code>{paths}</code>\n"
        "Будет использовано при создании пака. Название пака можно задать "
        "через «Ник / имя».</b></blockquote>",
        "Лого применено",
        ("name", "paths"),
    ),
    Item(
        "pack.logo.bad_svg",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Не получилось разобрать SVG</b>\n"
        "<blockquote><b>{detail}\n"
        "Попробуй сконвертировать через «Конвертировать в SVG» и "
        "пришли получившийся файл.</b></blockquote>",
        "Ошибка SVG",
        ("detail",),
    ),
    Item(
        "pack.logo.deleted",
        Cat.PACK,
        '<tg-emoji emoji-id="5870875489362513438">🗑</tg-emoji> <b>Лого удалено</b>',
        "Лого сброшено",
    ),
    Item(
        "pack.nick.blocked_by_logo",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Сначала удали лого</b>\n"
        "<blockquote><b>Когда лого активно, текст на эмодзи не "
        "отображается. Удали лого, чтобы вписать ник.</b></blockquote>",
        "Ник заблокирован лого",
    ),
    # Final action
    Item("pack.btn.apply", Cat.PACK, "Сгенерировать · {price}₽", placeholders=("price",)),
    Item(
        "pack.no_nick",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Сначала задай ник</b>\n"
        "<blockquote><b>Жми «Ник» и введи текст для замены.</b></blockquote>",
        "Когда ник не задан",
    ),
    Item(
        "pack.no_funds",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Недостаточно средств</b>\n"
        "<blockquote><b>Баланс: <code>{balance}₽</code>\n"
        "Стоимость пака: <code>{price}₽</code></b></blockquote>\n"
        '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji> '
        "<b>Пополни кошелёк через меню.</b>",
        "Не хватает баланса",
        ("balance", "price"),
    ),
    Item(
        "pack.confirm",
        Cat.PACK,
        '<tg-emoji emoji-id="5884479287171485878">📦</tg-emoji> '
        "<b>Создание пака</b>\n"
        "<blockquote><b>Ник: <code>{nick}</code>\n"
        "Стоимость: <code>{price}₽</code>\n"
        "После подтверждения: списываем баланс и создаём пак "
        "(~144 эмодзи).</b></blockquote>",
        "Подтверждение генерации",
        ("nick", "price"),
    ),
    Item("pack.btn.confirm", Cat.PACK, "Подтвердить · {price}₽", placeholders=("price",)),
    Item("pack.btn.cancel", Cat.PACK, "Отмена"),
    Item(
        "pack.progress",
        Cat.PACK,
        '<tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> '
        "<b>Создаю пак…</b>\n"
        "<blockquote><b>Готово: {i}/{total}</b></blockquote>",
        "Прогресс",
        ("i", "total"),
    ),
    Item(
        "pack.success",
        Cat.PACK,
        '<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> '
        "<b>Пак готов!</b>\n"
        "<blockquote><b>Ник: <code>{nick}</code>\n"
        "Эмодзи в паке: <code>{count}</code>\n"
        "Списано: <code>{price}₽</code></b></blockquote>\n\n"
        "{link}",
        "Успех",
        ("nick", "count", "price", "link"),
    ),
    Item(
        "pack.error",
        Cat.PACK,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Ошибка создания пака</b>\n"
        "<blockquote><b>{detail}\n"
        "Деньги возвращены на баланс.</b></blockquote>",
        "Ошибка с возвратом",
        ("detail",),
    ),
    # Common
    Item("pack.value.unset", Cat.PACK, "не задано"),
    Item("pack.value.set", Cat.PACK, "✓ добавлено"),
    Item("pack.format.value.sticker", Cat.PACK, "стикер"),
    Item("pack.format.value.emoji", Cat.PACK, "эмодзи"),
    # Main menu — sections
    Item("main.btn.create_pack", Cat.MAIN, "Emoji"),
    Item("main.btn.info", Cat.MAIN, "Информация"),
    Item("main.btn.gif_home", Cat.MAIN, "Gif"),
    Item("main.btn.other", Cat.MAIN, "Прочее"),
    Item(
        "other.text",
        Cat.MAIN,
        '<tg-emoji emoji-id="5345869179704606041">📂</tg-emoji> '
        "<b>Прочее</b>\n"
        "<blockquote><b>Рефералка и промокоды — собирай бонусы.</b></blockquote>",
        "Меню «Прочее»",
    ),
    Item("other.btn.referral", Cat.MAIN, "Рефералка"),
    Item("other.btn.promo", Cat.MAIN, "Промокод"),
    Item(
        "promo.prompt",
        Cat.MAIN,
        '<tg-emoji emoji-id="5346044461614924803">🎟</tg-emoji> '
        "<b>Промокод</b>\n"
        "<blockquote><b>Введи промокод одним сообщением.\n"
        "Каждый промик можно активировать только 1 раз.</b></blockquote>",
        "Запрос промокода",
    ),
    Item(
        "promo.ok",
        Cat.MAIN,
        '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji> '
        "<b>Промокод активирован</b>\n"
        "<blockquote><b>Начислено: <code>{amount}₽</code>\n"
        "Баланс: <code>{balance}₽</code></b></blockquote>",
        "Успех",
        ("amount", "balance"),
    ),
    Item(
        "promo.not_found",
        Cat.MAIN,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Такого промокода нет</b>\n"
        "<blockquote><b>Проверь раскладку и попробуй ещё раз.</b></blockquote>",
        "Промик не найден",
    ),
    Item(
        "promo.exhausted",
        Cat.MAIN,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Лимит активаций исчерпан</b>\n"
        "<blockquote><b>Этот промик уже разобрали раньше тебя.</b></blockquote>",
        "Лимит выбран",
    ),
    Item(
        "promo.used",
        Cat.MAIN,
        '<tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> '
        "<b>Ты уже активировал этот промокод</b>\n"
        "<blockquote><b>1 промик = 1 раз на юзера.</b></blockquote>",
        "Уже использовал",
    ),
    Item(
        "gif.home.text",
        Cat.MAIN,
        '<tg-emoji emoji-id="5355313692529424034">🎞</tg-emoji> '
        "<b>Создание GIF</b>\n\n"
        "{upload_emoji} <b>Отправь мне:</b>\n"
        "<blockquote><b>прем эмодзи — можно несколько [или] стикер, "
        "ссылку на пак emoji [или] sticker</b></blockquote>\n"
        "{settings_emoji} <b>Конфигурация:</b>\n"
        "<blockquote>{summary}</blockquote>",
        "Экран создания GIF",
        ("summary", "upload_emoji", "settings_emoji"),
    ),
]


ITEMS_BY_KEY: dict[str, Item] = {it.key: it for it in ITEMS}


def items_for_category(cat: str) -> list[Item]:
    return [it for it in ITEMS if it.category == cat]
