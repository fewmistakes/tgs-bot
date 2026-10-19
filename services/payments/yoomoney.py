"""YooMoney quickpay link helper.

The user gave us a quickpay URL with a `requestId`. To pre-fill amount we
just append `&sum={amount}` to the URL — quickpay supports this query param.

Auto-verification of payment status is **not possible** with quickpay-only
URLs (no OAuth token, no operation history). We use a manual flow:

    1. User taps "Я оплатил"
    2. Bot creates a pending payment
    3. Admin gets a notification with ✅/❌ buttons
    4. Admin confirms → balance is credited
"""

from __future__ import annotations

from urllib.parse import quote


def quickpay_url(base_url: str, amount_rub: int, label: str = "") -> str:
    """Append `sum` (and optional label) to a YooMoney quickpay URL.

    Args:
        base_url: e.g. "https://yoomoney.ru/transfer/quickpay?requestId=..."
        amount_rub: integer roubles
        label: optional payment comment / label
    """
    if not base_url:
        return ""
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}sum={int(amount_rub)}"
    if label:
        url += f"&label={quote(label)}"
    return url
