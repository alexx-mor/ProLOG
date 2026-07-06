"""Editable JSON-backed directory seed files."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from constants import BUNDLED_DICTIONARIES_DIR, DICTIONARIES_DIR

logger = logging.getLogger(__name__)

DIRECTORY_FILE_NAMES = ("locations", "objects", "positions", "work_types")
DIRECTORY_LABELS = {
    "locations": "Местонахождения",
    "objects": "Объекты",
    "positions": "Должности",
    "work_types": "Виды работ",
}
DEFAULT_POSITION_CATEGORY = "1-3"
DEPARTMENT_ALIASES = {
    "производственный участок композитных материалов (г.зверево)": "участок композитных материалов",
}


@dataclass(frozen=True, slots=True)
class PositionSeed:
    name: str
    category: str = DEFAULT_POSITION_CATEGORY
    departments: tuple[str, ...] = ()
    student_allowed: bool = False
    salary: str = ""
    salary_type: str = "hourly"
    group: str = "Рабочие"


@dataclass(frozen=True, slots=True)
class DirectorySeed:
    name: str
    departments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DictionaryStatus:
    key: str
    label: str
    current_version: str
    bundled_version: str
    missing_count: int
    custom_count: int

    @property
    def status_text(self) -> str:
        if self.missing_count:
            return f"Есть обновления: +{self.missing_count}"
        if self.custom_count:
            return f"Актуален, пользовательских записей: {self.custom_count}"
        return "Актуален"


def ensure_dictionary_files() -> None:
    """Create editable runtime dictionaries from bundled defaults when needed."""

    DICTIONARIES_DIR.mkdir(parents=True, exist_ok=True)
    for name in DIRECTORY_FILE_NAMES:
        target = DICTIONARIES_DIR / f"{name}.json"
        if target.exists():
            continue
        source = BUNDLED_DICTIONARIES_DIR / f"{name}.json"
        if source.exists() and source.resolve() != target.resolve():
            shutil.copy2(source, target)
        elif source.exists():
            continue
        else:
            _write_empty_dictionary(target)


def dictionary_statuses() -> list[DictionaryStatus]:
    ensure_dictionary_files()
    statuses: list[DictionaryStatus] = []
    for key in DIRECTORY_FILE_NAMES:
        bundled_path = BUNDLED_DICTIONARIES_DIR / f"{key}.json"
        editable_path = DICTIONARIES_DIR / f"{key}.json"
        bundled_raw = _read_json_file(bundled_path)
        editable_raw = _read_json_file(editable_path)
        bundled_items = _items_from_raw(bundled_raw)
        editable_items = _items_from_raw(editable_raw)
        bundled_names = {_item_name(item).casefold() for item in bundled_items if _item_name(item)}
        editable_names = {_item_name(item).casefold() for item in editable_items if _item_name(item)}
        statuses.append(
            DictionaryStatus(
                key=key,
                label=DIRECTORY_LABELS.get(key, key),
                current_version=_version_from_raw(editable_raw),
                bundled_version=_version_from_raw(bundled_raw),
                missing_count=len(bundled_names - editable_names),
                custom_count=len(editable_names - bundled_names),
            )
        )
    return statuses


def merge_dictionary_updates() -> int:
    ensure_dictionary_files()
    added = 0
    for key in DIRECTORY_FILE_NAMES:
        bundled_path = BUNDLED_DICTIONARIES_DIR / f"{key}.json"
        editable_path = DICTIONARIES_DIR / f"{key}.json"
        bundled_raw = _read_json_file(bundled_path)
        editable_raw = _read_json_file(editable_path)
        bundled_items = _items_from_raw(bundled_raw)
        editable_items = list(_items_from_raw(editable_raw))
        editable_names = {_item_name(item).casefold() for item in editable_items if _item_name(item)}
        for item in bundled_items:
            name = _item_name(item)
            if name and name.casefold() not in editable_names:
                editable_items.append(item)
                editable_names.add(name.casefold())
                added += 1
        _write_dictionary(editable_path, editable_items, _version_from_raw(bundled_raw))
    return added


def load_names(name: str) -> list[str]:
    return [seed.name for seed in load_directory_seeds(name)]


def load_directory_seeds(name: str) -> list[DirectorySeed]:
    ensure_dictionary_files()
    values: list[DirectorySeed] = []
    for item in _read_items(name):
        if isinstance(item, str):
            item_name = item.strip()
            departments: tuple[str, ...] = ()
        elif isinstance(item, dict):
            item_name = str(item.get("name", "")).strip()
            departments = _departments(item)
        else:
            continue
        if item_name:
            values.append(DirectorySeed(name=item_name, departments=departments))
    unique = {value.name.casefold(): value for value in values}
    return sorted(unique.values(), key=lambda value: value.name.casefold())


def load_positions() -> list[PositionSeed]:
    ensure_dictionary_files()
    positions: list[PositionSeed] = []
    for item in _read_items("positions"):
        if isinstance(item, str):
            name = item.strip()
            category = DEFAULT_POSITION_CATEGORY
            departments = ()
            student_allowed = False
            salary = ""
            salary_type = "hourly"
            group = "Рабочие"
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            category = str(item.get("category", DEFAULT_POSITION_CATEGORY)).strip()
            departments = _departments(item)
            student_allowed = bool(item.get("student_allowed", _default_student_allowed(name)))
            salary = str(item.get("salary", "")).strip()
            salary_type = _salary_type(str(item.get("salary_type", "")).strip())
            group = _position_group(str(item.get("group", "")).strip() or name)
        else:
            continue
        if name:
            positions.append(
                PositionSeed(
                    name=name,
                    category=category or DEFAULT_POSITION_CATEGORY,
                    departments=departments,
                    student_allowed=student_allowed,
                    salary=salary,
                    salary_type=salary_type,
                    group=group,
                )
            )
    unique = {position.name.casefold(): position for position in positions}
    return sorted(unique.values(), key=lambda position: position.name.casefold())


def load_position_category_map() -> dict[str, str]:
    return {position.name: position.category for position in load_positions()}


def load_position_seed_map() -> dict[str, PositionSeed]:
    return {position.name: position for position in load_positions()}


def department_names_match(item_departments: tuple[str, ...], department: str) -> bool:
    if not item_departments:
        return True
    normalized = normalize_department_name(department)
    return any(normalize_department_name(value) == normalized for value in item_departments)


def normalize_department_name(value: str) -> str:
    normalized = value.strip().casefold()
    return DEPARTMENT_ALIASES.get(normalized, normalized)


def _read_items(name: str) -> list[Any]:
    path = DICTIONARIES_DIR / f"{name}.json"
    return _items_from_raw(_read_json_file(path))


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Failed to read dictionary file %s: %s", path, exc)
        return []


def _items_from_raw(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        items = raw.get("items", [])
    else:
        items = raw
    if isinstance(items, dict) and isinstance(items.get("value"), list):
        items = items["value"]
    return items if isinstance(items, list) else []


def _version_from_raw(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("version") or "без версии")
    return "без версии"


def _item_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name", "")).strip()
    return ""


def _write_dictionary(path: Path, items: list[Any], version: str) -> None:
    payload = {"version": version, "items": items}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _departments(item: dict[str, Any]) -> tuple[str, ...]:
    values = item.get("departments", [])
    if isinstance(values, str):
        return (values.strip(),) if values.strip() else ()
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip() for value in values if str(value).strip())


def _position_group(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"итр", "инженерно-технические работники"}:
        return "ИТР"
    if normalized in {"рабочие", "рабочий"}:
        return "Рабочие"
    itr_markers = ("инженер", "мастер", "специалист", "руководител")
    return "ИТР" if any(marker in normalized for marker in itr_markers) else "Рабочие"


def _salary_type(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"monthly", "salary", "месяц", "зарплата", "оклад"}:
        return "monthly"
    return "hourly"


def _default_student_allowed(name: str) -> bool:
    normalized = name.strip().casefold()
    return normalized in {
        "электромонтажник",
        "слесарь",
        "инженер асутп",
        "слесарь-электромонтажник",
        "слесарь кипиа",
    }


def _write_empty_dictionary(path: Path) -> None:
    try:
        path.write_text("[]\n", encoding="utf-8")
    except OSError as exc:
        logger.exception("Failed to create dictionary file %s: %s", path, exc)
