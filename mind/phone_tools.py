from __future__ import annotations

import re


_MV_PHONE_PATTERN = re.compile(
    r"(?:(?:\+|00)?960|\(960\))?[\s.-]*([36789]\d{2})[\s.-]*(\d{4})\b"
)


def parse_maldivian_phone(text: str) -> dict[str, str] | None:
    """Parse a Maldivian phone number from the given text."""
    if not text:
        return None
    cleaned = text.strip().strip("\"'`“”‘’`()[]").strip()
    if len(cleaned) > 50:
        return None

    match = _MV_PHONE_PATTERN.search(cleaned)
    if not match:
        return None

    prefix = match.group(1)
    suffix = match.group(2)
    local_7 = prefix + suffix
    intl_digits = "960" + local_7
    intl_plus = "+960" + local_7
    formatted = f"+960 {prefix}-{suffix}"

    return {
        "raw": cleaned,
        "local": local_7,
        "international": intl_plus,
        "formatted": formatted,
        "digits": intl_digits,
        "viber_url": f"viber://chat?number=%2B{intl_digits}",
        "telegram_url": f"https://t.me/+{intl_digits}",
        "whatsapp_url": f"https://wa.me/{intl_digits}",
        "tel_url": f"tel:+{intl_digits}",
    }
