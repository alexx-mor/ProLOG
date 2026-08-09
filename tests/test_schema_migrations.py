"""Regression tests for versioned ProLOG and WorkBot schema migrations."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from database_integrity import CrossDatabaseIntegrityError
from models import Employee, ProductItem, WorkLogEntry
from schema_migrations import (
    Migration,
    MigrationComponent,
    MigrationRunner,
    UnsupportedSchemaVersionError,
)
from workbot.storage import WorkBotStorage


def test_all_component_databases_receive_baseline_version(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")

    database.initialize()

    versions = database.schema_versions()
    assert {info.component: info.current_version for info in versions} == {
        "prolog": 1,
        "employees": 1,
        "objects": 1,
        "products": 1,
        "aliases": 1,
    }
    with database.connect() as connection:
        for info in versions:
            row = connection.execute(
                f"""
                SELECT component, version, name, checksum, app_version, applied_at
                FROM {info.schema}.SchemaMigrations
                """
            ).fetchone()
            assert row["component"] == info.component
            assert row["version"] == 1
            assert row["name"]
            assert len(row["checksum"]) == 64
            assert row["app_version"] == "0.5.8"
            assert row["applied_at"].endswith("+00:00")


def test_repeated_initialization_does_not_repeat_baseline(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    before = _migration_history(database)

    database.initialize()

    assert _migration_history(database) == before
    assert all(len(rows) == 1 for rows in before.values())


def test_current_unversioned_working_database_starts_without_data_changes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories = DirectoryRepository(database)
    employee_id = EmployeeRepository(database).save(
        Employee(
            full_name="Иванов Иван Иванович",
            position="Слесарь",
            category="2",
            hire_date="2024-01-15",
        )
    )
    object_id = directories.upsert("objects", "Тестовый объект")
    product_id = directories.save_product(
        ProductItem(object_id=object_id, name="Шкаф управления", serial_number="1001")
    )
    worklog_id = WorkLogRepository(database).save(
        WorkLogEntry(
            employee_id=employee_id,
            work_date=date(2026, 8, 8),
            location_id=None,
            object_id=object_id,
            product_id=product_id,
            work_type_id=None,
            description="Сборка шкафа",
            hours=7.5,
        )
    )
    before = _user_data_snapshot(database)
    with database.connect(foreign_keys=False) as connection:
        for schema in ("main", "employees_db", "objects_db", "products_db", "aliases_db"):
            connection.execute(f"DROP TABLE {schema}.SchemaMigrations")

    database.initialize()

    assert _user_data_snapshot(database) == before
    entry = WorkLogRepository(database).get(worklog_id)
    assert entry is not None
    assert entry.hours == 7.5
    assert all(info.current_version == 1 for info in database.schema_versions())


def test_unknown_newer_schema_version_blocks_startup(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    before = _user_data_snapshot(database)
    with database.connect() as connection:
        connection.execute(
            "UPDATE main.SchemaMigrations SET version = 2 WHERE component = 'prolog'"
        )

    with pytest.raises(UnsupportedSchemaVersionError, match="более новой версией"):
        database.initialize()

    assert _user_data_snapshot(database) == before


def test_failed_migration_is_rolled_back_completely(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "rollback.sqlite3")
    connection.row_factory = sqlite3.Row

    def fail_after_writing(active: sqlite3.Connection) -> None:
        active.execute("CREATE TABLE ShouldRollback (id INTEGER PRIMARY KEY, value TEXT)")
        active.execute("INSERT INTO ShouldRollback(value) VALUES ('temporary')")
        raise RuntimeError("migration failed")

    runner = MigrationRunner(
        (MigrationComponent("test"),),
        (Migration(1, "Failing baseline", "failing-baseline-v1", fail_after_writing),),
        app_version="test",
    )

    with pytest.raises(RuntimeError, match="migration failed"):
        runner.migrate(connection)

    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    connection.close()
    assert "ShouldRollback" not in tables
    assert "SchemaMigrations" not in tables


def test_cross_database_reference_check_blocks_broken_component_set(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO products_db.Products (object_id, name, sort_order)
            VALUES (999999, 'Осиротевшее изделие', 1)
            """
        )

    report = database.check_references()
    assert not report.is_valid
    assert any(issue.code == "product_object" for issue in report.issues)
    with pytest.raises(CrossDatabaseIntegrityError, match="целостность"):
        database.initialize()


def test_workbot_schema_baseline_is_versioned_and_idempotent(tmp_path: Path) -> None:
    storage = WorkBotStorage(tmp_path / "workbot.sqlite3")

    storage.initialize()
    first = storage.schema_versions()
    storage.initialize()
    second = storage.schema_versions()

    assert [(item.component, item.current_version) for item in first] == [("workbot", 1)]
    assert second == first
    with storage.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM SchemaMigrations").fetchone()[0] == 1


def _migration_history(database: Database) -> dict[str, list[tuple[object, ...]]]:
    result: dict[str, list[tuple[object, ...]]] = {}
    with database.connect() as connection:
        for info in database.schema_versions():
            rows = connection.execute(
                f"""
                SELECT component, version, name, checksum, app_version, applied_at
                FROM {info.schema}.SchemaMigrations
                ORDER BY version
                """
            ).fetchall()
            result[info.component] = [tuple(row) for row in rows]
    return result


def _user_data_snapshot(database: Database) -> dict[str, list[tuple[object, ...]]]:
    tables = {
        "employees": ("employees_db", "Employees"),
        "objects": ("objects_db", "Objects"),
        "products": ("products_db", "Products"),
        "worklogs": ("main", "WorkLogEntries"),
        "employee_aliases": ("aliases_db", "EmployeeAliases"),
        "object_aliases": ("aliases_db", "ObjectAliases"),
        "product_aliases": ("aliases_db", "ProductAliases"),
    }
    snapshot: dict[str, list[tuple[object, ...]]] = {}
    with database.connect() as connection:
        for key, (schema, table) in tables.items():
            rows = connection.execute(f"SELECT * FROM {schema}.{table} ORDER BY rowid").fetchall()
            snapshot[key] = [tuple(row) for row in rows]
    return snapshot
