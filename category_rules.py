"""Helpers for employee category rules."""

from __future__ import annotations

STUDENT_CATEGORY = "0 (ученик/стажер)"
LEGACY_STUDENT_CATEGORY = "0 (студент)"
NO_CATEGORY = "—"


def category_values_from_rule(rule: str) -> list[str]:
    value = rule.strip()
    if not value or value == NO_CATEGORY:
        return []
    if "-" not in value:
        return [value] if value.isdigit() else []
    left, right = (part.strip() for part in value.split("-", 1))
    if not left.isdigit() or not right.isdigit():
        return []
    start, end = int(left), int(right)
    if start > end:
        start, end = end, start
    return [str(number) for number in range(start, end + 1)]


def pay_categories_for_position(rule: str, student_allowed: bool) -> list[str]:
    categories = category_values_from_rule(rule)
    if student_allowed:
        categories = [STUDENT_CATEGORY, *categories]
    return categories or [NO_CATEGORY]


def normalize_employee_category(value: str) -> str:
    stripped = value.strip()
    if stripped.casefold() in {
        "0",
        "студент",
        "ученик",
        "стажер",
        "ученик/стажер",
        LEGACY_STUDENT_CATEGORY,
        STUDENT_CATEGORY,
    }:
        return STUDENT_CATEGORY
    return "" if stripped in {"", "-", NO_CATEGORY} else stripped


def normalize_pay_category(value: str) -> str:
    normalized = normalize_employee_category(value)
    return normalized or NO_CATEGORY
