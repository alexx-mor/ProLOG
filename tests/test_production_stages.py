"""Persistence and service tests for the ProductionStage directory."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PySide6.QtWidgets import QApplication, QHeaderView

from database import Database
from production.errors import ProductionStageCodeExistsError
from production.migrations import (
    PRODUCTION_STAGE_SEED,
    apply_production_stages_migration,
    seed_production_stages,
)
from production.repository import ProductionStageRepository
from production.service import ProductionStageService
from schema_migrations import Migration, MigrationComponent, MigrationRunner
from services import DirectoryService
from database import DirectoryRepository
from ui.dialogs import DirectoryDialog


def _service(database: Database) -> ProductionStageService:
    return ProductionStageService(ProductionStageRepository(database))


def _downgrade_core_to_v1(database: Database) -> None:
    with database.connect() as connection:
        connection.execute("DROP TABLE ProductionStages")
        connection.execute(
            "DELETE FROM main.SchemaMigrations WHERE component = 'prolog' AND version = 2"
        )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migration_v1_to_v2_preserves_existing_core_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO Settings(key, value) VALUES ('p2-test', 'preserve-me')"
        )
    _downgrade_core_to_v1(database)

    database.initialize()

    versions = {item.component: item.current_version for item in database.schema_versions()}
    assert versions == {
        "prolog": 2,
        "employees": 1,
        "objects": 1,
        "products": 1,
        "aliases": 1,
    }
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM Settings WHERE key = 'p2-test'"
        ).fetchone()[0] == "preserve-me"


def test_production_stages_schema_has_required_columns_and_unique_keys(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()

    with database.connect() as connection:
        columns = [
            (row["name"], row["type"], bool(row["notnull"]), bool(row["pk"]))
            for row in connection.execute("PRAGMA table_info(ProductionStages)")
        ]
        unique_indexes = {
            tuple(
                column["name"]
                for column in connection.execute(f"PRAGMA index_info('{index['name']}')")
            )
            for index in connection.execute("PRAGMA index_list(ProductionStages)")
            if index["unique"]
        }

    assert columns == [
        ("id", "INTEGER", False, True),
        ("uid", "TEXT", True, False),
        ("code", "TEXT", True, False),
        ("name", "TEXT", True, False),
        ("sort_order", "INTEGER", True, False),
        ("is_active", "INTEGER", True, False),
        ("created_at_utc", "TEXT", True, False),
        ("updated_at_utc", "TEXT", True, False),
    ]
    assert {("uid",), ("code",)} <= unique_indexes


def test_repeated_v2_migration_and_seed_are_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()

    database.initialize()
    with database.connect() as connection:
        seed_production_stages(connection)
        seed_production_stages(connection)
        rows = connection.execute(
            "SELECT code, name FROM ProductionStages ORDER BY sort_order"
        ).fetchall()
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM SchemaMigrations WHERE component = 'prolog' AND version = 2"
        ).fetchone()[0]

    assert [tuple(row) for row in rows] == list(PRODUCTION_STAGE_SEED)
    assert migration_count == 1


def test_v2_migration_rolls_back_on_artificial_failure(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "rollback-v2.sqlite3")
    connection.row_factory = sqlite3.Row
    component = MigrationComponent("prolog")
    baseline = Migration(1, "Baseline", "rollback-p2-baseline", lambda _connection: None)
    MigrationRunner((component,), (baseline,), app_version="test").migrate(connection)

    def fail_after_schema(active: sqlite3.Connection) -> None:
        apply_production_stages_migration(active)
        raise RuntimeError("artificial P2 failure")

    runner = MigrationRunner(
        (component,),
        (
            baseline,
            Migration(2, "Production stages", "rollback-p2-v2", fail_after_schema),
        ),
        app_version="test",
    )

    with pytest.raises(RuntimeError, match="artificial P2 failure"):
        runner.migrate(connection)

    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    history = connection.execute(
        "SELECT version FROM SchemaMigrations ORDER BY version"
    ).fetchall()
    connection.close()
    assert "ProductionStages" not in tables
    assert [row[0] for row in history] == [1]


def test_other_component_files_do_not_change_during_v2_migration(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_core_to_v1(database)
    component_paths = {
        key: path
        for key, path in database.database_paths().items()
        if key != "prolog"
    }
    before = {key: _file_hash(path) for key, path in component_paths.items()}

    database.initialize()

    assert {key: _file_hash(path) for key, path in component_paths.items()} == before


def test_seed_rename_is_not_overwritten_by_restart(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database)
    stage = service.repository.get_by_code("PREPARATION")
    assert stage is not None and stage.id is not None

    renamed = service.rename(stage.id, "Подготовительные операции")
    database.initialize()

    current = service.repository.get_by_code("PREPARATION")
    assert current is not None
    assert current.name == "Подготовительные операции"
    assert current.uid == renamed.uid


def test_create_custom_stage_generates_immutable_uuid_v4(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()

    stage = _service(database).create("custom stage", "Пользовательский этап")

    assert stage.id is not None
    assert isinstance(stage.uid, UUID) and stage.uid.version == 4
    assert stage.code == "CUSTOM_STAGE"
    with pytest.raises(FrozenInstanceError):
        stage.uid = UUID("00000000-0000-4000-8000-000000000001")  # type: ignore[misc]


def test_stage_code_is_unique_after_normalization(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database)
    service.create("laser-cutting", "Лазерная резка")

    with pytest.raises(ProductionStageCodeExistsError):
        service.create("LASER CUTTING", "Другое название")


def test_rename_does_not_change_code_or_uid(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database)
    stage = service.create("commissioning", "Пуск")
    assert stage.id is not None

    renamed = service.rename(stage.id, "Пусконаладка")

    assert renamed.name == "Пусконаладка"
    assert renamed.code == stage.code
    assert renamed.uid == stage.uid


def test_active_filter_deactivation_and_restore(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database)
    stage = service.create("temporary", "Временный этап")
    assert stage.id is not None

    service.deactivate(stage.id)
    assert stage.id not in {item.id for item in service.list_active()}
    assert stage.id in {item.id for item in service.list_all()}

    restored = service.restore(stage.id)
    assert restored.is_active
    assert stage.id in {item.id for item in service.list_active()}


def test_reorder_is_persisted_with_hidden_inactive_stage(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database)
    stages = service.list_all()
    first, hidden, third = stages[:3]
    assert first.id is not None and hidden.id is not None and third.id is not None
    service.deactivate(hidden.id)

    service.move(third.id, -1, active_only=True)

    reordered = service.list_all()
    assert [item.id for item in reordered[:3]] == [third.id, hidden.id, first.id]


def test_repository_reports_stage_unused_before_production_events_exist(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    stage = _service(database).list_all()[0]
    assert stage.id is not None

    assert not ProductionStageRepository(database).is_in_use(stage.id)


def test_stage_service_has_no_work_type_dependency() -> None:
    source_path = Path(__file__).parents[1] / "production" / "service.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "models" not in imports
    assert "services" not in imports


def test_production_stage_ui_uses_service_without_sqlite(tmp_path: Path) -> None:
    source_path = Path(__file__).parents[1] / "ui" / "production_stage_widget.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert "sqlite3" not in imported_roots
    assert "database" not in imported_roots

    app = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    stage_service = _service(database)
    dialog = DirectoryDialog(
        DirectoryService(DirectoryRepository(database)),
        initial_key="production_stages",
        can_edit_databases=False,
        production_stage_service=stage_service,
    )

    assert dialog.current_key == "production_stages"
    assert dialog.title_label.text() == "Справочник этапов производства"
    assert dialog.production_stage_widget.table.rowCount() == len(PRODUCTION_STAGE_SEED)
    assert dialog.table.isHidden()
    assert all(
        dialog.production_stage_widget.table.horizontalHeader().sectionResizeMode(column)
        == QHeaderView.ResizeMode.Interactive
        for column in range(4)
    )
    dialog.close()
    app.processEvents()
