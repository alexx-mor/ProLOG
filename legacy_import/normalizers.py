"""Normalizers for old secretary-maintained Excel reports."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any

from hours import normalize_hours
from services import NON_WORK_LOCATIONS

WORK_TYPE_DEFAULT = "Прочее"
LOCAL_OFFICE_MARKERS = ("газетная", "ермак", "офис")
PRODUCTION_MARKERS = ("цех", "производ")

POSITION_ALIASES = {
    "эл.монтажник": "Электромонтажник",
    "эл монтажник": "Электромонтажник",
    "электомонтажник": "Электромонтажник",
    "электромонтаж": "Электромонтажник",
    "электромонтажник": "Электромонтажник",
    "слесарь": "Слесарь",
    "слесарь - электромонтажник": "Слесарь-электромонтажник",
    "слесарь-электромонтажник": "Слесарь-электромонтажник",
    "инженер асу тп": "Инженер АСУТП",
    "инженер асутп": "Инженер АСУТП",
    "мастер": "Мастер",
    "помощник руководителя": "Помощник руководителя",
}

ABSENCE_ALIASES = (
    (("безсодерж",), "Без содержания"),
    (("без содерж",), "Без содержания"),
    (("учеб",), "Учебный отпуск"),
    (("ученичес",), "Учебный отпуск"),
    (("ежегод", "отпуск"), "Отпуск"),
    (("отпуск",), "Отпуск"),
    (("больнич"), "Больничный"),
    (("прогул"), "Прогул"),
)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()


def normalize_key(value: str) -> str:
    return clean_text(value).replace("ё", "е").replace("Ё", "Е").casefold()


def normalize_position(value: str) -> str:
    normalized = normalize_key(value)
    return POSITION_ALIASES.get(normalized, clean_text(value).capitalize())


def parse_hours(value: Any) -> float:
    if value is None or value == "":
        return 0
    if isinstance(value, datetime):
        return normalize_hours(value.hour + value.minute / 60 + value.second / 3600)
    if isinstance(value, time):
        return normalize_hours(value.hour + value.minute / 60 + value.second / 3600)
    if isinstance(value, timedelta):
        return normalize_hours(value.total_seconds() / 3600)
    if isinstance(value, (int, float)):
        hours = float(value * 24 if 0 < value < 1 else value)
        return normalize_hours(hours)
    text = clean_text(value).replace(",", ".").casefold().replace("ч", "")
    try:
        return normalize_hours(text)
    except ValueError:
        return 0


def classify_non_work(description: str) -> str:
    normalized = normalize_key(description)
    if is_weekend(description):
        return ""
    for markers, location in ABSENCE_ALIASES:
        if isinstance(markers, str):
            if markers in normalized:
                return location
            continue
        if all(marker in normalized for marker in markers):
            return location
    return ""


def is_weekend(description: str) -> bool:
    normalized = normalize_key(description)
    return "выход" in normalized or "выыход" in normalized


def infer_location(legacy_location: str, object_name: str) -> str:
    combined = normalize_key(f"{legacy_location} {object_name}")
    if any(marker in combined for marker in PRODUCTION_MARKERS):
        return "Производство"
    if any(marker in combined for marker in LOCAL_OFFICE_MARKERS):
        return "Офис"
    if legacy_location.strip():
        return "Командировка дальняя (КД)"
    return ""


def person_match_keys(value: str) -> set[str]:
    text = clean_text(value)
    if not text:
        return set()
    normalized = normalize_person_text(text)
    keys = {normalized}
    parts = [part for part in re.split(r"\s+", normalized) if part]
    if parts:
        surname = parts[0]
        initials = "".join(part[0] for part in parts[1:] if part)
        if initials:
            keys.add(f"{surname} {initials}")
            keys.add(f"{surname}{initials}")
    compact_initials = re.match(r"^([а-яa-z-]+)\s+([а-яa-z])\.?\s*([а-яa-z])?\.?$", normalized)
    if compact_initials:
        surname = compact_initials.group(1)
        first = compact_initials.group(2) or ""
        second = compact_initials.group(3) or ""
        keys.add(f"{surname} {first}{second}")
        keys.add(f"{surname}{first}{second}")
    return {key for key in keys if key}


def normalize_person_text(value: str) -> str:
    text = normalize_key(value)
    text = re.sub(r"[^а-яa-z\s.-]", " ", text)
    text = text.replace(".", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_import_comment(sheet_name: str, row_number: int, position_text: str, legacy_location: str) -> str:
    parts = [f"Импорт из старого Excel: лист '{sheet_name}', строка {row_number}."]
    if position_text:
        parts.append(f"Старая должность: {position_text}.")
    if legacy_location:
        parts.append(f"Старое нахождение: {legacy_location}.")
    return " ".join(parts)


def is_non_work_location(location_name: str) -> bool:
    return location_name in NON_WORK_LOCATIONS
