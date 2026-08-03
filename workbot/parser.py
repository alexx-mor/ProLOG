"""Разбор структурированного отчёта из обычного текстового сообщения."""

from __future__ import annotations

import re
from datetime import date

from workbot.models import ParsedReport


class ReportParseError(ValueError):
    pass


_ALIASES = {
    "work_date": {"дата", "день"},
    "work_types": {"виды работ", "вид работ", "работы", "выполненные работы"},
    "hours": {"затраченное время", "время", "часы", "часов"},
    "object_name": {"объект", "объекты"},
    "location": {"местонахождение", "местоположение", "локация", "место"},
}
_LABEL_TO_FIELD = {alias: field for field, aliases in _ALIASES.items() for alias in aliases}
_LABEL_PATTERN = re.compile(
    r"^\s*(" + "|".join(sorted((re.escape(item) for item in _LABEL_TO_FIELD), key=len, reverse=True)) + r")\s*[:—–-]\s*(.*)\s*$",
    re.IGNORECASE,
)


def parse_report(text: str, message_date: date | None = None) -> ParsedReport | None:
    """Вернуть отчёт, None для обычного сообщения или ошибку для неполного отчёта."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in normalized and normalized.count(";") >= 2:
        normalized = normalized.replace(";", "\n")

    values: dict[str, str] = {}
    current_field: str | None = None
    recognized = 0
    for line in normalized.splitlines():
        match = _LABEL_PATTERN.match(line)
        if match:
            current_field = _LABEL_TO_FIELD[match.group(1).strip().casefold()]
            value = match.group(2).strip()
            values[current_field] = value
            recognized += 1
        elif current_field and line.strip():
            values[current_field] = f"{values[current_field]}\n{line.strip()}".strip()

    if recognized == 0:
        return None

    missing = [
        title
        for key, title in (
            ("work_date", "дата"),
            ("work_types", "виды работ"),
            ("hours", "затраченное время"),
            ("object_name", "объект"),
            ("location", "местонахождение"),
        )
        if not values.get(key, "").strip()
    ]
    if missing:
        raise ReportParseError("Не заполнено: " + ", ".join(missing))

    work_date = _parse_date(values["work_date"], message_date)
    hours = _parse_hours(values["hours"])
    return ParsedReport(
        work_date=work_date,
        work_types=values["work_types"].strip(),
        hours=hours,
        object_name=values["object_name"].strip(),
        location=values["location"].strip(),
    )


def _parse_date(value: str, message_date: date | None) -> date:
    cleaned = value.strip().casefold()
    if cleaned == "сегодня":
        if message_date is None:
            raise ReportParseError("Для значения «сегодня» не удалось определить дату сообщения")
        return message_date
    for pattern in (r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", r"(\d{1,2})[./-](\d{1,2})[./-](\d{2})"):
        match = re.fullmatch(pattern, cleaned)
        if not match:
            continue
        day, month, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise ReportParseError(f"Некорректная дата: {value}") from exc
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ReportParseError("Дата должна быть в формате ДД.ММ.ГГГГ") from exc


def _parse_hours(value: str) -> float:
    match = re.search(r"-?\d+(?:[.,]\d+)?", value)
    if not match:
        raise ReportParseError("Затраченное время должно быть числом")
    hours = float(match.group(0).replace(",", "."))
    if hours <= 0 or hours > 24:
        raise ReportParseError("Затраченное время должно быть больше 0 и не больше 24 часов")
    return hours
