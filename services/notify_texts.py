"""User notification texts. Each helper picks a random variant so repeated
notifications don't read as copy-paste.
"""

from __future__ import annotations

import random

_E_WALLET = '<tg-emoji emoji-id="5354996371755645180">👛</tg-emoji>'
_E_COIN = '<tg-emoji emoji-id="5904462880941545555">🪙</tg-emoji>'
_E_PARTY = '<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji>'
_E_GIFT = '<tg-emoji emoji-id="6032644646587338669">🎁</tg-emoji>'
_E_CHECK = '<tg-emoji emoji-id="5870633910337015697">✅</tg-emoji>'
_E_FIRE = '<tg-emoji emoji-id="5420315771991497307">🔥</tg-emoji>'
_E_HANDS = '<tg-emoji emoji-id="5258513401784573443">🤝</tg-emoji>'


# Top-up / credit (rubles arrived)
_CREDIT_HEADERS: tuple[str, ...] = (
    f"{_E_WALLET} <b>Балик залетел)</b>",
    f"{_E_PARTY} <b>Бабки на месте)</b>",
    f"{_E_FIRE} <b>Кошелёк потяжелел)</b>",
    f"{_E_COIN} <b>Звонкая монета упала)</b>",
    f"{_E_WALLET} <b>Баланс пополнен)</b>",
    f"{_E_PARTY} <b>Капнуло на счёт)</b>",
)


def credit_text(amount_rub: float, new_balance_rub: float) -> str:
    """User notification when balance is credited (top-up, admin add, etc.)."""
    header = random.choice(_CREDIT_HEADERS)
    return (
        f"{header}\n"
        f"<blockquote>"
        f"{_E_COIN} <b>+{amount_rub:.0f}₽</b>\n"
        f"{_E_WALLET} <b>Текущий баланс: {new_balance_rub:.0f}₽</b>"
        f"</blockquote>"
    )


# Mass grant — rubles
_GRANT_RUB_HEADERS: tuple[str, ...] = (
    f"{_E_GIFT} <b>Подарочек залетел)</b>",
    f"{_E_PARTY} <b>Халява на счёт)</b>",
    f"{_E_WALLET} <b>Бесплатный балик)</b>",
    f"{_E_FIRE} <b>Бот раздаёт нал)</b>",
    f"{_E_GIFT} <b>Тебе кинули подгон)</b>",
)


def grant_rub_text(amount_rub: int) -> str:
    header = random.choice(_GRANT_RUB_HEADERS)
    return f"{header}\n<blockquote>{_E_COIN} <b>+{amount_rub}₽ на баланс</b></blockquote>"


# Mass grant — free renders
_GRANT_FREE_HEADERS: tuple[str, ...] = (
    f"{_E_GIFT} <b>Подарочек залетел)</b>",
    f"{_E_PARTY} <b>Халявные рендеры)</b>",
    f"{_E_FIRE} <b>Бесплатные пуши)</b>",
    f"{_E_GIFT} <b>Тебе кинули подгон)</b>",
)


def grant_free_text(amount: int) -> str:
    header = random.choice(_GRANT_FREE_HEADERS)
    return f"{header}\n<blockquote>{_E_CHECK} <b>+{amount} бесплатных рендеров</b></blockquote>"


# Referral bonus (incoming friend → +5₽)
_REFERRAL_HEADERS: tuple[str, ...] = (
    f"{_E_HANDS} <b>Новый кореш в команде)</b>",
    f"{_E_PARTY} <b>Реферал залетел)</b>",
    f"{_E_FIRE} <b>+1 в твою банду)</b>",
    f"{_E_HANDS} <b>Свежак по рефке)</b>",
)


def referral_text(amount_rub: int = 5) -> str:
    header = random.choice(_REFERRAL_HEADERS)
    return f"{header}\n<blockquote>{_E_COIN} <b>+{amount_rub}₽ залетели на баланс</b></blockquote>"


# Payment approved (admin confirmed YooMoney)
_E_ROCKET = '<tg-emoji emoji-id="5879813504499742873">🚀</tg-emoji>'

_PAYMENT_APPROVED_HEADERS: tuple[str, ...] = (
    f"{_E_PARTY} <b>Оплата прошла)</b>",
    f"{_E_CHECK} <b>Бабки зачислены)</b>",
    f"{_E_FIRE} <b>Платёж подтверждён)</b>",
    f"{_E_WALLET} <b>Оплата принята)</b>",
)


def payment_approved_text(amount_rub: float, new_balance_rub: float) -> str:
    header = random.choice(_PAYMENT_APPROVED_HEADERS)
    return (
        f"{header}\n"
        f"<blockquote>"
        f"{_E_COIN} <b>+{amount_rub:.0f}₽ зачислено</b>\n"
        f"{_E_WALLET} <b>Баланс: {new_balance_rub:.0f}₽</b>"
        f"</blockquote>"
    )


# Payment rejected (admin declined)
_E_SAD = '<tg-emoji emoji-id="5870783375312946839">😔</tg-emoji>'

_PAYMENT_REJECTED_HEADERS: tuple[str, ...] = (
    f"{_E_SAD} <b>Оплата не прошла</b>",
    f"{_E_SAD} <b>Платёж отклонён</b>",
    f"{_E_SAD} <b>Не удалось подтвердить оплату</b>",
)


def payment_rejected_text() -> str:
    header = random.choice(_PAYMENT_REJECTED_HEADERS)
    return (
        f"{header}\n"
        f"<blockquote>"
        f"<b>Обратитесь в поддержку, если считаете это ошибкой.</b>"
        f"</blockquote>"
    )


# Render done
_RENDER_DONE_HEADERS: tuple[str, ...] = (
    f"{_E_PARTY} <b>Рендер готов)</b>",
    f"{_E_FIRE} <b>Готово, держи)</b>",
    f"{_E_ROCKET} <b>Рендер улетел)</b>",
    f"{_E_CHECK} <b>Всё сделано)</b>",
)


def render_done_text() -> str:
    header = random.choice(_RENDER_DONE_HEADERS)
    return f"{header}\n<blockquote><b>Твой рендер готов 👇</b></blockquote>"
