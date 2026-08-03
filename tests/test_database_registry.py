"""Checks for selecting local and network-ready SQLite sources."""

from pathlib import Path

from database import Database
from database_registry import PROLOG_DATABASE, DatabaseRegistry


def test_registry_accepts_existing_prolog_database(tmp_path: Path) -> None:
    database_path = tmp_path / "shared-prolog.sqlite3"
    Database(database_path).initialize()
    registry = DatabaseRegistry(tmp_path / "database_sources.json")

    source = registry.add("Серверная база", PROLOG_DATABASE, str(database_path))
    sources = registry.list(str(database_path), "")

    assert source.path == str(database_path)
    assert [(item.name, item.kind) for item in sources] == [("Серверная база", PROLOG_DATABASE)]
    assert registry.check(str(database_path), PROLOG_DATABASE) == (True, "Доступна")


def test_registry_rejects_wrong_database_type(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    path.touch()
    registry = DatabaseRegistry(tmp_path / "database_sources.json")

    ok, message = registry.check(str(path), PROLOG_DATABASE)

    assert not ok
    assert "не является базой ProLOG" in message
