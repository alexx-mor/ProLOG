"""Shared parsing and display rules for worked hours."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

HOURS_QUANT = Decimal("0.01")
MAX_DAILY_HOURS = Decimal("24")


def normalize_hours(value: int | float | Decimal | str | None) -> float:
    if value in (None, ""):
        return 0.0
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Часы должны быть числом") from exc
    if not amount.is_finite():
        raise ValueError("Часы должны быть числом")
    return float(amount.quantize(HOURS_QUANT, rounding=ROUND_HALF_UP))


def parse_hours(value: str) -> float:
    return normalize_hours(value)


def format_hours(value: int | float | Decimal | str | None) -> str:
    amount = Decimal(str(normalize_hours(value))).quantize(HOURS_QUANT)
    text = format(amount, "f").rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")
