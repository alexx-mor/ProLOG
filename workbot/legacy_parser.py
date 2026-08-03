"""Эвристический разбор исторических отчётов свободной формы."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from workbot.models import ParsedEmployeeReport, ParsedReport


_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?!\d)")
_DATE_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[-–—]\s*(\d{1,2})[./](\d{1,2})[./](\d{2,4})(?!\d)"
)
_NAME_RE = re.compile(
    r"(?<![А-Яа-яЁё-])([А-ЯЁ][а-яё-]{2,})(?:\s+|\.\s*)"
    r"([А-ЯЁ])\s*\.\s*([А-ЯЁ])\s*\.?",
)
_EXPLICIT_HOURS_RE = re.compile(r"(?<!\d)(\d{1,2}(?:[.,]\d+)?)\s*(?:ч\b|час)", re.IGNORECASE)
_TIME_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?:[:.]?(\d{2}))?\s*(?:-|–|—|до)\s*"
    r"(\d{1,2})(?:[:.]?(\d{2}))?(?!\d)",
    re.IGNORECASE,
)
_LOCATION_PATTERNS = (
    (re.compile(r"газетн(?:ая|ый)(?:\s*23)?", re.IGNORECASE), "Газетная"),
    (re.compile(r"комитетская(?:\s*\d+\s*[а-я]?)?", re.IGNORECASE), "Комитетская"),
    (re.compile(r"ермака(?:\s*\d+\s*[а-я]?)?", re.IGNORECASE), "Ермака"),
    (re.compile(r"харьковск(?:ое|ий|ая)?", re.IGNORECASE), "Харьковское"),
    (re.compile(r"камчатка", re.IGNORECASE), "Камчатка"),
    (re.compile(r"петропавловск(?:-камчатский)?", re.IGNORECASE), "Петропавловск-Камчатский"),
    (re.compile(r"новочеркасск", re.IGNORECASE), "Новочеркасск"),
    (re.compile(r"южно-сахалинск", re.IGNORECASE), "Южно-Сахалинск"),
    (re.compile(r"элиста", re.IGNORECASE), "Элиста"),
    (re.compile(r"\bофис\b", re.IGNORECASE), "Офис"),
    (re.compile(r"\bпроизводство\b", re.IGNORECASE), "Производство"),
)
_OBJECT_PATTERNS = (
    ("жигалово", "Жигалово"),
    ("залари", "Залари"),
    ("зверево", "Зверево"),
    ("сипавск", "Сипавское"),
    ("сиповск", "Сипавское"),
    ("сухой лог", "Сухой Лог"),
    ("ижевск", "Ижевск"),
    ("элиста", "Элиста"),
    ("фку сизо", "ФКУ СИЗО"),
    ("тундров", "Тундровый"),
    ("никольская сопка", "Никольская сопка"),
    ("военные", "Военные"),
    ("унр", "УНР"),
    ("рэб", "РЭБ"),
    ("кос", "КОС"),
    ("внс", "ВНС"),
)
_EMPLOYEE_ALIASES = {
    "шерстик а.д.": "Шерстюк А.Д.",
}


def parse_legacy_reports(
    text: str,
    message_date: date,
    fallback_employee: str,
) -> list[ParsedEmployeeReport]:
    """Разобрать одно сообщение, которое может содержать несколько дней и сотрудников."""
    lines = _normalize_lines(text)
    groups = _date_groups(lines, message_date)
    if not groups:
        return []

    prefix = lines[: groups[0][0]]
    chunks: list[list[str]] = []
    for index, (line_index, _dates) in enumerate(groups):
        next_index = groups[index + 1][0] if index + 1 < len(groups) else len(lines)
        chunks.append(lines[line_index + 1 : next_index])

    # В сообщениях вида «26.07 / 27.07 / 28.07 / время и работы» последние
    # строки относятся ко всем перечисленным подряд датам.
    next_useful: list[str] | None = None
    for index in range(len(chunks) - 1, -1, -1):
        if _description_lines(chunks[index]):
            next_useful = chunks[index]
        elif next_useful is not None:
            chunks[index] = next_useful

    results: list[ParsedEmployeeReport] = []
    for (_line_index, dates), chunk in zip(groups, chunks):
        context = [*prefix, *chunk]
        names = extract_employee_names("\n".join(context))
        if not names:
            names = [fallback_employee.strip() or "Неизвестный сотрудник"]
        hours = _extract_hours(context)
        location = _extract_location(context)
        object_name = _extract_object(context)
        descriptions = _description_lines(context)
        description = "\n".join(descriptions).strip()
        if not description:
            description = "Описание не распознано автоматически"
        fragment = "\n".join(context).strip()
        confidence = _confidence(names, hours, description, location)
        for work_date in dates:
            for employee_name in names:
                results.append(
                    ParsedEmployeeReport(
                        employee_name=employee_name,
                        report=ParsedReport(
                            work_date=work_date,
                            work_types=description,
                            hours=hours,
                            object_name=object_name,
                            location=location,
                        ),
                        confidence=confidence,
                        source_fragment=fragment,
                    )
                )
    return _deduplicate(results)


def extract_employee_names(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _NAME_RE.finditer(text):
        name = f"{match.group(1)} {match.group(2)}.{match.group(3)}."
        name = _EMPLOYEE_ALIASES.get(name.casefold(), name)
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return names


def infer_sender_names(messages: list[tuple[int, str]]) -> dict[int, str]:
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    for sender_id, text in messages:
        for name in extract_employee_names(text):
            counts[sender_id][name] += 1
    inferred: dict[int, str] = {}
    for sender_id, counter in counts.items():
        top = counter.most_common(2)
        if not top or top[0][1] < 2:
            continue
        second_count = top[1][1] if len(top) > 1 else 0
        if top[0][1] >= max(2, second_count * 2):
            inferred[sender_id] = top[0][0]
    return inferred


def _normalize_lines(text: str) -> list[str]:
    normalized = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("⁰⁰", ":00")
        .replace("⁰", "0")
    )
    return [re.sub(r"\s+", " ", line).strip(" \t") for line in normalized.splitlines() if line.strip()]


def _date_groups(lines: list[str], reference_date: date) -> list[tuple[int, list[date]]]:
    groups: list[tuple[int, list[date]]] = []
    for line_index, line in enumerate(lines):
        dates: list[date] = []
        occupied_spans: list[tuple[int, int]] = []
        for match in _DATE_RANGE_RE.finditer(line):
            first_day, last_day, month, year = (int(value) for value in match.groups())
            if year < 100:
                year += 2000
            if last_day < first_day or last_day - first_day > 31:
                continue
            for day in range(first_day, last_day + 1):
                parsed = _correct_date(day, month, year, reference_date)
                if parsed is not None and parsed not in dates:
                    dates.append(parsed)
            occupied_spans.append(match.span())
        for match in _DATE_RE.finditer(line):
            if any(start <= match.start() < end for start, end in occupied_spans):
                continue
            day, month, year = (int(value) for value in match.groups())
            if year < 100:
                year += 2000
            parsed = _correct_date(day, month, year, reference_date)
            if parsed is None:
                continue
            if parsed not in dates:
                dates.append(parsed)
        if dates:
            groups.append((line_index, dates))
    return groups


def _extract_hours(lines: list[str]) -> float:
    joined = " ".join(lines)
    normalized = joined.replace("⁰⁰", ":00").replace("⁰", "0")
    match = _TIME_RANGE_RE.search(normalized)
    if match:
        start_hour = int(match.group(1))
        start_minute = int(match.group(2) or 0)
        end_hour = int(match.group(3))
        end_minute = int(match.group(4) or 0)
        duration = (end_hour * 60 + end_minute - start_hour * 60 - start_minute) / 60
        if 0 < duration <= 24:
            if duration >= 6 and "без обед" not in normalized.casefold():
                duration -= 1
            return round(duration, 2)
    explicit = _EXPLICIT_HOURS_RE.search(joined)
    if explicit:
        value = float(explicit.group(1).replace(",", "."))
        return value if 0 < value <= 24 else 0.0
    return 0.0


def _extract_location(lines: list[str]) -> str:
    candidates: list[str] = []
    for line in lines:
        for pattern, default_title in _LOCATION_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(0).strip(" .,:;-")
                if default_title == "Газетная":
                    value = "Газетная 23" if "23" in value else "Газетная"
                elif default_title == "Комитетская":
                    value = value[0].upper() + value[1:]
                else:
                    value = default_title
                if value.casefold() not in {candidate.casefold() for candidate in candidates}:
                    candidates.append(value)
    return "; ".join(candidates[:3])


def _extract_object(lines: list[str]) -> str:
    joined = " ".join(lines).casefold()
    objects: list[str] = []
    for line in lines:
        match = re.search(r"\bобъект\s*:\s*(.+)", line, re.IGNORECASE)
        if match:
            labeled = match.group(1).strip(" .,:;-")
            if labeled and labeled not in objects:
                objects.append(labeled)
    objects.extend(
        title
        for pattern, title in _OBJECT_PATTERNS
        if pattern in joined and title not in objects
    )
    return "; ".join(objects)


def _description_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        without_dates = _DATE_RANGE_RE.sub("", line)
        without_dates = _DATE_RE.sub("", without_dates).strip(" .,:;-")
        if not without_dates:
            continue
        if _NAME_RE.fullmatch(without_dates):
            continue
        if _is_time_only(without_dates):
            continue
        if _is_location_only(without_dates):
            continue
        result.append(without_dates)
    return result


def _is_time_only(value: str) -> bool:
    cleaned = value.casefold().strip()
    if _EXPLICIT_HOURS_RE.fullmatch(cleaned):
        return True
    return _TIME_RANGE_RE.fullmatch(cleaned.removeprefix("с ").removeprefix("с")) is not None


def _is_location_only(value: str) -> bool:
    remainder = value
    matched = False
    for pattern, _title in _LOCATION_PATTERNS:
        if pattern.search(remainder):
            matched = True
            remainder = pattern.sub("", remainder)
    if not matched:
        return False
    remainder = remainder.strip(" .,:;-")
    return not remainder or _is_time_only(remainder)


def _confidence(names: list[str], hours: float, description: str, location: str) -> float:
    score = 0.35
    if names and names[0] != "Неизвестный сотрудник":
        score += 0.2
    if hours > 0:
        score += 0.2
    if description and description != "Описание не распознано автоматически":
        score += 0.15
    if location:
        score += 0.1
    return min(score, 1.0)


def _deduplicate(items: list[ParsedEmployeeReport]) -> list[ParsedEmployeeReport]:
    result: list[ParsedEmployeeReport] = []
    seen: set[tuple[object, ...]] = set()
    for item in items:
        key = (
            item.employee_name.casefold(),
            item.report.work_date,
            item.report.work_types.casefold(),
            item.report.hours,
            item.report.object_name.casefold(),
            item.report.location.casefold(),
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _correct_date(day: int, month: int, year: int, reference_date: date) -> date | None:
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    lower = reference_date - timedelta(days=400)
    upper = reference_date + timedelta(days=7)
    if year == reference_date.year and lower <= parsed <= upper:
        return parsed

    candidates = [parsed]
    for candidate_month in (month, reference_date.month):
        try:
            candidate = date(reference_date.year, candidate_month, day)
        except ValueError:
            continue
        if candidate not in candidates:
            candidates.append(candidate)
    valid = [candidate for candidate in candidates if lower <= candidate <= upper]
    if not valid:
        return None
    return min(valid, key=lambda candidate: abs((candidate - reference_date).days))
