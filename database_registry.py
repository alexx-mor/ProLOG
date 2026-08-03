"""JSON registry of ProLOG and WorkBot database files."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from constants import DATABASE_FILE, DATA_DIR

logger = logging.getLogger(__name__)

REGISTRY_FILE = DATA_DIR / "database_sources.json"
PROLOG_DATABASE = "prolog"
WORKBOT_DATABASE = "workbot"

_REQUIRED_TABLES = {
    PROLOG_DATABASE: {"Employees", "Objects", "WorkLogEntries"},
    WORKBOT_DATABASE: {"users", "messages", "reports"},
}


@dataclass(slots=True)
class DatabaseSource:
    id: str
    name: str
    kind: str
    path: str


class DatabaseRegistry:
    """Stores known database paths outside the database being selected."""

    def __init__(self, path: Path = REGISTRY_FILE) -> None:
        self.path = path

    def list(self, prolog_path: str = "", workbot_path: str = "") -> list[DatabaseSource]:
        sources = self._load()
        known = {(item.kind, _path_key(item.path)) for item in sources}
        candidates = [(PROLOG_DATABASE, prolog_path or str(DATABASE_FILE), "Основная база ProLOG")]
        if workbot_path:
            candidates.append((WORKBOT_DATABASE, workbot_path, "База WorkBot"))
        for kind, value, name in candidates:
            key = (kind, _path_key(value))
            if value and key not in known:
                sources.append(DatabaseSource(uuid4().hex, name, kind, value))
                known.add(key)
        for file_path in self.path.parent.glob("*.sqlite3"):
            kind = self.detect_kind(file_path)
            key = (kind, _path_key(str(file_path)))
            if kind and key not in known:
                sources.append(DatabaseSource(uuid4().hex, file_path.stem, kind, str(file_path)))
                known.add(key)
        self._save(sources)
        return sorted(sources, key=lambda item: (item.kind, item.name.casefold()))

    def add(self, name: str, kind: str, path: str) -> DatabaseSource:
        clean_name = name.strip()
        clean_path = path.strip()
        if kind not in _REQUIRED_TABLES:
            raise ValueError("Неизвестный тип базы данных")
        if not clean_name:
            raise ValueError("Укажите название базы данных")
        ok, message = self.check(clean_path, kind)
        if not ok:
            raise ValueError(message)
        sources = self._load()
        key = (kind, _path_key(clean_path))
        existing = next((item for item in sources if (item.kind, _path_key(item.path)) == key), None)
        if existing:
            existing.name = clean_name
            self._save(sources)
            return existing
        source = DatabaseSource(uuid4().hex, clean_name, kind, clean_path)
        sources.append(source)
        self._save(sources)
        return source

    def update(self, source_id: str, name: str, kind: str, path: str) -> DatabaseSource:
        sources = self._load()
        source = next((item for item in sources if item.id == source_id), None)
        if source is None:
            raise ValueError("База данных не найдена в реестре")
        ok, message = self.check(path, kind)
        if not ok:
            raise ValueError(message)
        key = (kind, _path_key(path))
        if any(
            item.id != source_id and (item.kind, _path_key(item.path)) == key
            for item in sources
        ):
            raise ValueError("Эта база данных уже добавлена в реестр")
        source.name = name.strip() or source.name
        source.kind = kind
        source.path = path.strip()
        self._save(sources)
        return source

    def remove(self, source_id: str) -> None:
        self._save([item for item in self._load() if item.id != source_id])

    def check(self, path: str, kind: str) -> tuple[bool, str]:
        if not path.strip():
            return False, "Путь к базе данных не указан"
        file_path = Path(path)
        if not file_path.is_file():
            return False, "Файл базы данных не найден"
        try:
            connection = sqlite3.connect(
                f"{file_path.absolute().as_uri()}?mode=ro",
                uri=True,
                timeout=5,
            )
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            return False, f"Не удалось открыть базу данных: {exc}"
        missing = _REQUIRED_TABLES.get(kind, set()) - tables
        if missing:
            label = "ProLOG" if kind == PROLOG_DATABASE else "WorkBot"
            return False, f"Файл не является базой {label}: отсутствуют необходимые таблицы"
        return True, "Доступна"

    def detect_kind(self, path: Path) -> str:
        for kind in (PROLOG_DATABASE, WORKBOT_DATABASE):
            if self.check(str(path), kind)[0]:
                return kind
        return ""

    def _load(self) -> list[DatabaseSource]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            result = []
            for row in payload.get("sources", []):
                if row.get("kind") in _REQUIRED_TABLES and row.get("path"):
                    result.append(DatabaseSource(**row))
            return result
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.exception("Failed to load database registry: %s", exc)
            return []

    def _save(self, sources: list[DatabaseSource]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "sources": [asdict(item) for item in sources]}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def _path_key(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(value.strip())))
