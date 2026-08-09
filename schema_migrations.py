"""Versioned, transactional SQLite schema migrations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable


MigrationCallback = Callable[[sqlite3.Connection], None]
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SchemaMigrationError(RuntimeError):
    """Base error for invalid or failed schema migration state."""


class UnsupportedSchemaVersionError(SchemaMigrationError):
    """Raised when a database was created by a newer application version."""


@dataclass(frozen=True, slots=True)
class MigrationComponent:
    name: str
    schema: str = "main"

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.name):
            raise ValueError(f"Некорректное имя компонента: {self.name}")
        if not _IDENTIFIER_RE.fullmatch(self.schema):
            raise ValueError(f"Некорректное имя схемы SQLite: {self.schema}")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    fingerprint: str
    apply: MigrationCallback

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise ValueError("Версия миграции должна быть положительной")
        if not self.name.strip() or not self.fingerprint.strip():
            raise ValueError("Миграция должна иметь название и fingerprint")

    @property
    def checksum(self) -> str:
        payload = f"{self.version}\n{self.name.strip()}\n{self.fingerprint.strip()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SchemaVersionInfo:
    component: str
    schema: str
    current_version: int
    supported_version: int

    @property
    def is_supported(self) -> bool:
        return self.current_version <= self.supported_version

    @property
    def status(self) -> str:
        if self.current_version > self.supported_version:
            return "Требуется более новая версия ProLOG"
        if self.current_version < self.supported_version:
            return "Требуется миграция"
        return "Актуальна"


class MigrationRunner:
    """Applies one ordered migration stream to one or more attached databases."""

    def __init__(
        self,
        components: Iterable[MigrationComponent],
        migrations: Iterable[Migration],
        *,
        app_version: str,
    ) -> None:
        self.components = tuple(components)
        self.migrations = tuple(sorted(migrations, key=lambda item: item.version))
        self.app_version = app_version
        if not self.components:
            raise ValueError("Не указан ни один компонент базы данных")
        versions = [migration.version for migration in self.migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("Миграции должны иметь последовательные версии, начиная с 1")

    @property
    def supported_version(self) -> int:
        return self.migrations[-1].version if self.migrations else 0

    def inspect(self, connection: sqlite3.Connection) -> list[SchemaVersionInfo]:
        return [
            SchemaVersionInfo(
                component=component.name,
                schema=component.schema,
                current_version=_current_version(connection, component),
                supported_version=self.supported_version,
            )
            for component in self.components
        ]

    def migrate(self, connection: sqlite3.Connection) -> list[SchemaVersionInfo]:
        initial = self.inspect(connection)
        self._reject_newer_versions(initial)
        transaction = _MigrationTransaction(connection)
        with transaction:
            for component in self.components:
                _create_history_table(connection, component)
            self._validate_history(connection)
            current = {
                info.component: info.current_version
                for info in self.inspect(connection)
            }
            for migration in self.migrations:
                pending = [
                    component
                    for component in self.components
                    if current[component.name] < migration.version
                ]
                if not pending:
                    continue
                migration.apply(connection)
                applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for component in pending:
                    connection.execute(
                        f"""
                        INSERT INTO {component.schema}.SchemaMigrations (
                            component, version, name, checksum, app_version, applied_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            component.name,
                            migration.version,
                            migration.name,
                            migration.checksum,
                            self.app_version,
                            applied_at,
                        ),
                    )
                    current[component.name] = migration.version
        result = self.inspect(connection)
        self._reject_newer_versions(result)
        return result

    def _reject_newer_versions(self, versions: Iterable[SchemaVersionInfo]) -> None:
        newer = [info for info in versions if not info.is_supported]
        if not newer:
            return
        details = ", ".join(
            f"{info.component}: {info.current_version} (поддерживается {info.supported_version})"
            for info in newer
        )
        raise UnsupportedSchemaVersionError(
            "База данных создана более новой версией ProLOG. "
            f"Обновите приложение. Компоненты: {details}"
        )

    def _validate_history(self, connection: sqlite3.Connection) -> None:
        expected = {migration.version: migration for migration in self.migrations}
        for component in self.components:
            rows = connection.execute(
                f"""
                SELECT component, version, name, checksum
                FROM {component.schema}.SchemaMigrations
                ORDER BY version
                """
            ).fetchall()
            versions = [int(row["version"]) for row in rows]
            if versions and versions != list(range(1, max(versions) + 1)):
                raise SchemaMigrationError(
                    f"Нарушена последовательность миграций компонента {component.name}"
                )
            for row in rows:
                version = int(row["version"])
                migration = expected.get(version)
                if migration is None:
                    continue
                if str(row["component"]) != component.name:
                    raise SchemaMigrationError(
                        f"Некорректный компонент в истории миграций {component.name}"
                    )
                if str(row["name"]) != migration.name or str(row["checksum"]) != migration.checksum:
                    raise SchemaMigrationError(
                        f"Контрольная сумма миграции {component.name} v{version} не совпадает"
                    )


class _MigrationTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.savepoint = "prolog_schema_migration"
        self.owns_transaction = not connection.in_transaction

    def __enter__(self) -> None:
        if self.owns_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        else:
            self.connection.execute(f"SAVEPOINT {self.savepoint}")

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        if exc_type is None:
            if self.owns_transaction:
                self.connection.commit()
            else:
                self.connection.execute(f"RELEASE SAVEPOINT {self.savepoint}")
            return False
        if self.owns_transaction:
            self.connection.rollback()
        else:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {self.savepoint}")
            self.connection.execute(f"RELEASE SAVEPOINT {self.savepoint}")
        return False


def execute_sql_script(connection: sqlite3.Connection, script: str) -> None:
    """Execute a SQL script statement-by-statement without implicit commits."""
    buffer: list[str] = []
    for line in script.splitlines():
        buffer.append(line)
        statement = "\n".join(buffer).strip()
        if not statement or not sqlite3.complete_statement(statement):
            continue
        connection.execute(statement)
        buffer.clear()
    remainder = "\n".join(buffer).strip()
    if remainder:
        raise SchemaMigrationError("SQL миграции содержит незавершенное выражение")


def _history_table_exists(
    connection: sqlite3.Connection,
    component: MigrationComponent,
) -> bool:
    row = connection.execute(
        f"""
        SELECT 1
        FROM {component.schema}.sqlite_master
        WHERE type = 'table' AND name = 'SchemaMigrations'
        """
    ).fetchone()
    return row is not None


def _current_version(
    connection: sqlite3.Connection,
    component: MigrationComponent,
) -> int:
    if not _history_table_exists(connection, component):
        return 0
    row = connection.execute(
        f"SELECT MAX(version) AS version FROM {component.schema}.SchemaMigrations"
    ).fetchone()
    return int(row["version"] or 0)


def _create_history_table(
    connection: sqlite3.Connection,
    component: MigrationComponent,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {component.schema}.SchemaMigrations (
            component TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version > 0),
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            app_version TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY (component, version)
        )
        """
    )
