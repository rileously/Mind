from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


class MathInputError(ValueError):
    pass


_NUMBER = re.compile(
    r"(?<![\w.])(?P<open>\()?\s*(?P<sign>[+-])?\s*"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<close>\))?(?![\w.])"
)
_NUMBERED_LIST_PREFIX = re.compile(r"^\s*\d+\s*[.)]\s+")


def normalize_math_text(text: str) -> str:
    return (
        text.replace("×", "*")
        .replace("✕", "*")
        .replace("·", "*")
        .replace("÷", "/")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("＝", "=")
    )


def extract_numbers(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    for raw_line in normalize_math_text(text).splitlines():
        # OCR often keeps numbered-list prefixes ("1. 250"). They are labels, not values.
        line = _NUMBERED_LIST_PREFIX.sub("", raw_line)
        for match in _NUMBER.finditer(line):
            token = match.group("number").replace(",", "")
            try:
                value = Decimal(token)
            except InvalidOperation:
                continue
            if match.group("sign") == "-" or (match.group("open") and match.group("close")):
                value = -value
            values.append(value)
    return values


def sum_number_list(text: str) -> str:
    values = extract_numbers(text)
    if not values:
        raise MathInputError("No numbers were found in the image.")
    total = sum(values, Decimal(0))
    formatted_total = format_decimal(total)
    if len(values) <= 12:
        expression = " + ".join(format_decimal(value) for value in values)
        expression = expression.replace("+ -", "− ")
        return f"{expression} = {formatted_total}"
    return f"Total ({len(values)} numbers): {formatted_total}"


def format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return f"{int(value):,}"
    plain = format(value.normalize(), "f")
    integer, decimal = plain.split(".", 1)
    sign = ""
    if integer.startswith("-"):
        sign, integer = "-", integer[1:]
    return f"{sign}{int(integer):,}.{decimal.rstrip('0')}"
