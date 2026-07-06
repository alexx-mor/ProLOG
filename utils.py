"""General utilities used across layers."""

from __future__ import annotations

import logging
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
