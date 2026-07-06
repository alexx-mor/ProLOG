"""Private requisites options loaded from a local JSON file."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from constants import BASE_DIR
from directory_files import normalize_department_name

logger = logging.getLogger(__name__)

REQUISITES_FILE = BASE_DIR / "private" / "requisites.json"


@dataclass(slots=True)
class RequisitesOptions:
    organizations: list[str]
    departments: list[str]
    leaders: list[str]


def load_requisites_options(path: Path | None = None) -> RequisitesOptions:
    path = path or _resolve_requisites_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Failed to load requisites options from %s", path)
        raise ValueError(f"Не удалось загрузить файл реквизитов: {path}") from exc
    return RequisitesOptions(
        organizations=_values(data, "organizations"),
        departments=_values(data, "departments"),
        leaders=_values(data, "leaders"),
    )


def _resolve_requisites_file() -> Path:
    candidates = [REQUISITES_FILE]
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if bundle_dir:
        candidates.append(Path(bundle_dir) / "private" / "requisites.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return REQUISITES_FILE


def _values(data: dict, key: str) -> list[str]:
    values = data.get(key, [])
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if key == "departments" and normalize_department_name(text) == "участок композитных материалов":
            text = "Участок композитных материалов"
        if text not in result:
            result.append(text)
    return result
