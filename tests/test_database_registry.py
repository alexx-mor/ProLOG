"""Checks for selecting local and network-ready SQLite sources."""

from pathlib import Path

from database import Database
from database_registry import (
    ALIASES_DATABASE,
    EMPLOYEES_DATABASE,
    OBJECTS_DATABASE,
    PRODUCTS_DATABASE,
    PROLOG_DATABASE,
    DatabaseRegistry,
)


def test_registry_accepts_existing_prolog_database(tmp_path: Path) -> None:
    database_path = tmp_path / "shared-prolog.sqlite3"
    Database(database_path).initialize()
    registry = DatabaseRegistry(tmp_path / "database_sources.json")

    source = registry.add("Серверная база", PROLOG_DATABASE, str(database_path))
    sources = registry.list(str(database_path), "")

    assert source.path == str(database_path)
    by_kind = {item.kind: item for item in sources}
    assert set(by_kind) == {
        PROLOG_DATABASE,
        EMPLOYEES_DATABASE,
        OBJECTS_DATABASE,
        PRODUCTS_DATABASE,
        ALIASES_DATABASE,
    }
    assert by_kind[PROLOG_DATABASE].name == "Серверная база"
    assert by_kind[EMPLOYEES_DATABASE].path == str(tmp_path / "employees.sqlite3")
    assert by_kind[OBJECTS_DATABASE].path == str(tmp_path / "objects.sqlite3")
    assert by_kind[PRODUCTS_DATABASE].path == str(tmp_path / "products.sqlite3")
    assert by_kind[ALIASES_DATABASE].path == str(tmp_path / "aliases.sqlite3")
    assert registry.check(str(database_path), PROLOG_DATABASE) == (True, "Доступна")


def test_registry_rejects_wrong_database_type(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite3"
    path.touch()
    registry = DatabaseRegistry(tmp_path / "database_sources.json")

    ok, message = registry.check(str(path), PROLOG_DATABASE)

    assert not ok
    assert "не является базой Ядро ProLOG" in message
