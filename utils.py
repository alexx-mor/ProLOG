"""General utilities used across layers."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from constants import BACKUPS_DIR, DATA_DIR, DICTIONARIES_DIR, EXPORTS_DIR


def ensure_app_directories() -> None:
    for directory in (DATA_DIR, EXPORTS_DIR, BACKUPS_DIR, DICTIONARIES_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    ensure_app_directories()
    log_file = DATA_DIR / "prolog.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def safe_filename(value: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if char in forbidden else char for char in value).strip()
    return cleaned or "export"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Не удалось подобрать имя файла для {path}")


def employment_duration_text(hire_date: str, as_of: date | None = None) -> str:
    if not hire_date.strip():
        return "Дата не указана"
    try:
        started = datetime.strptime(hire_date, "%Y-%m-%d").date()
    except ValueError:
        return "Некорректная дата"
    current = as_of or date.today()
    if started > current:
        return "Дата позднее текущей"

    months_total = (current.year - started.year) * 12 + current.month - started.month
    if current.day < started.day:
        months_total -= 1
    years, months = divmod(max(0, months_total), 12)
    parts = []
    if years:
        parts.append(f"{years} {_plural(years, 'год', 'года', 'лет')}")
    if months:
        parts.append(f"{months} {_plural(months, 'месяц', 'месяца', 'месяцев')}")
    return " ".join(parts) or "Менее месяца"


def _plural(value: int, one: str, few: str, many: str) -> str:
    if value % 100 in range(11, 15):
        return many
    if value % 10 == 1:
        return one
    if value % 10 in range(2, 5):
        return few
    return many
