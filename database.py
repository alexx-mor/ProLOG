"""SQLite storage and repositories."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from category_rules import (
    LEGACY_STUDENT_CATEGORY,
    NO_CATEGORY,
    STUDENT_CATEGORY,
    normalize_pay_category,
    pay_categories_for_position,
)
from constants import (
    ALIASES_DATABASE_FILE,
    DATABASE_FILE,
    EMPLOYEES_DATABASE_FILE,
    OBJECTS_DATABASE_FILE,
    PRODUCTS_DATABASE_FILE,
)
from directory_files import (
    department_names_match,
    load_directory_seeds,
    load_names,
    load_position_category_map,
    load_position_seed_map,
    load_positions,
)
from models import (
    AliasItem,
    DirectoryItem,
    Employee,
    ObjectStatus,
    PayRate,
    ProductItem,
    ProductStatus,
    WorkCalendarDay,
    WorkDayType,
    WorkLogEntry,
)

logger = logging.getLogger(__name__)

DIRECTORY_TABLES = {
    "objects": "objects_db.Objects",
    "employee_groups": "EmployeeGroups",
    "positions": "Positions",
    "work_types": "WorkTypes",
    "locations": "Locations",
}

EMPLOYEES_TABLE = "employees_db.Employees"
OBJECTS_TABLE = "objects_db.Objects"
PRODUCTS_TABLE = "products_db.Products"
ALIASES_SCHEMA = "aliases_db"

ALIAS_DEFINITIONS = {
    "employee": ("EmployeeAliases", "employee_id", EMPLOYEES_TABLE, "full_name"),
    "object": ("ObjectAliases", "object_id", OBJECTS_TABLE, "name"),
    "location": ("LocationAliases", "location_id", "Locations", "name"),
    "work_type": ("WorkTypeAliases", "work_type_id", "WorkTypes", "name"),
    "product": ("ProductAliases", "product_id", PRODUCTS_TABLE, "name"),
}

class Database:
    def __init__(
        self,
        path: Path | None = None,
        *,
        employees_path: Path | None = None,
        objects_path: Path | None = None,
        products_path: Path | None = None,
        aliases_path: Path | None = None,
    ) -> None:
        self.path = path or DATABASE_FILE
        component_dir = self.path.parent
        self.employees_path = employees_path or component_dir / EMPLOYEES_DATABASE_FILE.name
        self.objects_path = objects_path or component_dir / OBJECTS_DATABASE_FILE.name
        self.products_path = products_path or component_dir / PRODUCTS_DATABASE_FILE.name
        self.aliases_path = aliases_path or component_dir / ALIASES_DATABASE_FILE.name
        paths = [
            self.path,
            self.employees_path,
            self.objects_path,
            self.products_path,
            self.aliases_path,
        ]
        if len({_database_path_key(item) for item in paths}) != len(paths):
            raise ValueError("Каждый компонент ProLOG должен использовать отдельный файл базы данных")

    @contextmanager
    def connect(self, *, foreign_keys: bool = True) -> Iterator[sqlite3.Connection]:
        for path in self.database_paths().values():
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("ATTACH DATABASE ? AS employees_db", (str(self.employees_path),))
        connection.execute("ATTACH DATABASE ? AS objects_db", (str(self.objects_path),))
        connection.execute("ATTACH DATABASE ? AS products_db", (str(self.products_path),))
        connection.execute("ATTACH DATABASE ? AS aliases_db", (str(self.aliases_path),))
        try:
            yield connection
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if not isinstance(exc, ValueError):
                logger.exception("Database transaction failed")
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect(foreign_keys=False) as connection:
            connection.executescript(COMPONENT_SCHEMA_SQL)
            self._migrate_legacy_components(connection)
            connection.executescript(MAIN_SCHEMA_SQL)
            self._migrate(connection)
            self._remove_external_foreign_keys(connection)
            connection.executescript(MAIN_SCHEMA_SQL)
            self._seed(connection)
            self._drop_legacy_component_tables(connection)

    def database_paths(self) -> dict[str, Path]:
        return {
            "prolog": self.path,
            "employees": self.employees_path,
            "objects": self.objects_path,
            "products": self.products_path,
            "aliases": self.aliases_path,
        }

    def _migrate_legacy_components(self, connection: sqlite3.Connection) -> None:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM main.sqlite_master WHERE type = 'table'"
            )
        }
        migrations = {
            "Employees": (
                "employees_db.Employees",
                "id, full_name, position, category, status, mobile_phone",
            ),
            "Objects": (
                "objects_db.Objects",
                "id, name, project_number, contract_number, customer, contract_type, "
                "object_type, object_subtype, signed_date, due_date, object_status, is_active, sort_order",
            ),
            "Products": (
                "products_db.Products",
                "id, object_id, serial_number, name, code, product_status, readiness_percent, "
                "start_date, release_date, is_active, sort_order",
            ),
            "EmployeeAliases": (
                "aliases_db.EmployeeAliases",
                "alias_normalized, original_alias, employee_id, created_at, updated_at",
            ),
            "ObjectAliases": (
                "aliases_db.ObjectAliases",
                "alias_normalized, original_alias, object_id, created_at, updated_at",
            ),
            "LocationAliases": (
                "aliases_db.LocationAliases",
                "alias_normalized, original_alias, location_id, created_at, updated_at",
            ),
            "WorkTypeAliases": (
                "aliases_db.WorkTypeAliases",
                "alias_normalized, original_alias, work_type_id, created_at, updated_at",
            ),
            "ProductAliases": (
                "aliases_db.ProductAliases",
                "alias_normalized, original_alias, product_id, created_at, updated_at",
            ),
        }
        for source_table, (target_table, columns) in migrations.items():
            if source_table not in tables:
                continue
            connection.execute(
                f"INSERT OR IGNORE INTO {target_table} ({columns}) "
                f"SELECT {columns} FROM main.{source_table}"
            )

    def _drop_legacy_component_tables(self, connection: sqlite3.Connection) -> None:
        for table in (
            "ProductAliases",
            "ObjectAliases",
            "EmployeeAliases",
            "LocationAliases",
            "WorkTypeAliases",
            "Products",
            "Objects",
            "Employees",
        ):
            connection.execute(f"DROP TABLE IF EXISTS main.{table}")

    def _remove_external_foreign_keys(self, connection: sqlite3.Connection) -> None:
        definitions = {
            "WorkLogEntries": """
                CREATE TABLE WorkLogEntries_component_migration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    work_date TEXT NOT NULL,
                    location_id INTEGER REFERENCES Locations(id),
                    object_id INTEGER,
                    product_id INTEGER,
                    work_type_id INTEGER REFERENCES WorkTypes(id),
                    description TEXT NOT NULL DEFAULT '',
                    hours REAL NOT NULL DEFAULT 0,
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """,
            "MaxUserBindings": """
                CREATE TABLE MaxUserBindings_component_migration (
                    max_user_id INTEGER PRIMARY KEY,
                    employee_id INTEGER NOT NULL,
                    username_snapshot TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """,
            "WorkBotImportRows": """
                CREATE TABLE WorkBotImportRows_component_migration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    max_message_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    source_index INTEGER NOT NULL,
                    source_kind TEXT NOT NULL,
                    sender_id INTEGER NOT NULL,
                    chat_id INTEGER,
                    received_at TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    raw_text TEXT NOT NULL DEFAULT '',
                    source_fragment TEXT NOT NULL DEFAULT '',
                    employee_text TEXT NOT NULL DEFAULT '',
                    work_date TEXT NOT NULL,
                    work_types TEXT NOT NULL DEFAULT '',
                    hours REAL NOT NULL DEFAULT 0,
                    object_text TEXT NOT NULL DEFAULT '',
                    location_text TEXT NOT NULL DEFAULT '',
                    product_text TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    employee_id INTEGER,
                    object_id INTEGER,
                    location_id INTEGER REFERENCES Locations(id),
                    work_type_id INTEGER REFERENCES WorkTypes(id),
                    product_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'new',
                    error_message TEXT NOT NULL DEFAULT '',
                    worklog_entry_id INTEGER UNIQUE REFERENCES WorkLogEntries(id),
                    imported_at TEXT,
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(max_message_id, revision, source_index)
                )
            """,
        }
        external_tables = {"Employees", "Objects", "Products"}
        for table, definition in definitions.items():
            parents = {
                str(row["table"])
                for row in connection.execute(f"PRAGMA main.foreign_key_list({table})")
            }
            if not parents & external_tables:
                continue
            columns = [
                str(row["name"])
                for row in connection.execute(f"PRAGMA main.table_info({table})")
            ]
            temporary = f"{table}_component_migration"
            connection.execute(f"DROP TABLE IF EXISTS {temporary}")
            connection.execute(definition)
            column_list = ", ".join(columns)
            connection.execute(
                f"INSERT INTO {temporary} ({column_list}) SELECT {column_list} FROM {table}"
            )
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {temporary} RENAME TO {table}")

    def _migrate(self, connection: sqlite3.Connection) -> None:
        employee_columns = {
            row["name"] for row in connection.execute("PRAGMA employees_db.table_info(Employees)")
        }
        if "mobile_phone" not in employee_columns:
            connection.execute(
                "ALTER TABLE employees_db.Employees ADD COLUMN mobile_phone TEXT NOT NULL DEFAULT ''"
            )
        position_columns = {row["name"] for row in connection.execute("PRAGMA table_info(Positions)")}
        if "category" not in position_columns:
            connection.execute("ALTER TABLE Positions ADD COLUMN category TEXT NOT NULL DEFAULT '1-3'")
        if "student_allowed" not in position_columns:
            connection.execute("ALTER TABLE Positions ADD COLUMN student_allowed INTEGER NOT NULL DEFAULT 0")
        if "salary" not in position_columns:
            connection.execute("ALTER TABLE Positions ADD COLUMN salary TEXT NOT NULL DEFAULT ''")
        if "salary_type" not in position_columns:
            connection.execute("ALTER TABLE Positions ADD COLUMN salary_type TEXT NOT NULL DEFAULT 'hourly'")
        if "employee_group" not in position_columns:
            connection.execute("ALTER TABLE Positions ADD COLUMN employee_group TEXT NOT NULL DEFAULT 'Рабочие'")
        pay_rate_columns = {row["name"] for row in connection.execute("PRAGMA table_info(PayRates)")}
        if "far_trip_coeff" not in pay_rate_columns:
            connection.execute("ALTER TABLE PayRates ADD COLUMN far_trip_coeff TEXT NOT NULL DEFAULT '1'")
        if "far_trip_salary" not in pay_rate_columns:
            connection.execute("ALTER TABLE PayRates ADD COLUMN far_trip_salary TEXT NOT NULL DEFAULT ''")
        if "near_trip_coeff" not in pay_rate_columns:
            connection.execute("ALTER TABLE PayRates ADD COLUMN near_trip_coeff TEXT NOT NULL DEFAULT '1'")
        if "holiday_coeff" not in pay_rate_columns:
            connection.execute("ALTER TABLE PayRates ADD COLUMN holiday_coeff TEXT NOT NULL DEFAULT '1'")
        if "saturday_coeff" not in pay_rate_columns:
            connection.execute("ALTER TABLE PayRates ADD COLUMN saturday_coeff TEXT NOT NULL DEFAULT '1'")
        object_columns = {
            row["name"] for row in connection.execute("PRAGMA objects_db.table_info(Objects)")
        }
        object_defaults = {
            "project_number": "",
            "contract_number": "",
            "customer": "",
            "contract_type": "",
            "object_type": "",
            "object_subtype": "",
            "signed_date": "",
            "due_date": "",
            "object_status": ObjectStatus.IN_PROGRESS.value,
        }
        for column, default in object_defaults.items():
            if column not in object_columns:
                connection.execute(
                    f"ALTER TABLE objects_db.Objects ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'"
                )
        if "sort_order" not in object_columns:
            connection.execute(
                "ALTER TABLE objects_db.Objects ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
        product_columns = {
            row["name"] for row in connection.execute("PRAGMA products_db.table_info(Products)")
        }
        if "sort_order" not in product_columns:
            connection.execute(
                "ALTER TABLE products_db.Products ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
        worklog_columns = {row["name"] for row in connection.execute("PRAGMA table_info(WorkLogEntries)")}
        if "product_id" not in worklog_columns:
            connection.execute("ALTER TABLE WorkLogEntries ADD COLUMN product_id INTEGER")
        workbot_columns = {row["name"] for row in connection.execute("PRAGMA table_info(WorkBotImportRows)")}
        if "product_text" not in workbot_columns:
            connection.execute("ALTER TABLE WorkBotImportRows ADD COLUMN product_text TEXT NOT NULL DEFAULT ''")
        if "product_id" not in workbot_columns:
            connection.execute("ALTER TABLE WorkBotImportRows ADD COLUMN product_id INTEGER")
        connection.execute("UPDATE Positions SET category = '—', student_allowed = 0 WHERE name = 'Мастер чистоты'")
        connection.execute(
            """
            DELETE FROM PayRates
            WHERE category = ?
              AND EXISTS (
                  SELECT 1
                  FROM PayRates current
                  WHERE current.position_id = PayRates.position_id
                    AND current.category = ?
              )
            """,
            (LEGACY_STUDENT_CATEGORY, STUDENT_CATEGORY),
        )
        connection.execute(
            "UPDATE PayRates SET category = ? WHERE category = ?",
            (STUDENT_CATEGORY, LEGACY_STUDENT_CATEGORY),
        )
        connection.execute(
            "UPDATE employees_db.Employees SET category = ? WHERE category = ?",
            (STUDENT_CATEGORY, LEGACY_STUDENT_CATEGORY),
        )
        self._normalize_sort_order(connection, OBJECTS_TABLE, "name COLLATE NOCASE, id")
        self._normalize_product_sort_order(connection)
        self._sync_pay_rates(connection)

    def _seed(self, connection: sqlite3.Connection) -> None:
        seed_values = {
            "Locations": load_names("locations"),
            "EmployeeGroups": load_names("employee_groups"),
            OBJECTS_TABLE: load_names("objects"),
            "WorkTypes": load_names("work_types"),
        }
        for table, values in seed_values.items():
            existing = {
                row["name"].casefold(): row["id"]
                for row in connection.execute(f"SELECT id, name FROM {table}")
            }
            for value in values:
                existing_id = existing.get(value.casefold())
                if existing_id:
                    connection.execute(
                        f"UPDATE {table} SET name = ? WHERE id = ?",
                        (value, existing_id),
                    )
                else:
                    if table == OBJECTS_TABLE:
                        cursor = connection.execute(
                            f"""
                            INSERT INTO {OBJECTS_TABLE} (name, sort_order)
                            VALUES (?, COALESCE((SELECT MAX(sort_order) + 1 FROM {OBJECTS_TABLE}), 1))
                            """,
                            (value,),
                        )
                    else:
                        cursor = connection.execute(
                            f"INSERT INTO {table} (name) VALUES (?)",
                            (value,),
                        )
                    existing[value.casefold()] = cursor.lastrowid
        self._seed_positions(connection)

    def _seed_positions(self, connection: sqlite3.Connection) -> None:
        existing_positions = {
            row["name"].casefold(): row["id"]
            for row in connection.execute("SELECT id, name FROM Positions")
        }
        for position in load_positions():
            existing_id = existing_positions.get(position.name.casefold())
            if existing_id:
                connection.execute(
                    """
                    UPDATE Positions
                    SET name = ?
                    WHERE id = ?
                    """,
                    (position.name, existing_id),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO Positions (name, category, student_allowed, salary, salary_type, employee_group, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (position.name, position.category, int(position.student_allowed), position.salary, position.salary_type, position.group),
                )
                existing_positions[position.name.casefold()] = cursor.lastrowid
        self._sync_pay_rates(connection)

    def _normalize_sort_order(
        self,
        connection: sqlite3.Connection,
        table: str,
        fallback_order: str,
        where_sql: str = "",
        params: tuple[object, ...] = (),
    ) -> None:
        sql = f"SELECT id FROM {table}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += f" ORDER BY CASE WHEN sort_order > 0 THEN 0 ELSE 1 END, sort_order, {fallback_order}"
        rows = connection.execute(sql, params).fetchall()
        for index, row in enumerate(rows, start=1):
            connection.execute(
                f"UPDATE {table} SET sort_order = ? WHERE id = ?",
                (index, row["id"]),
            )

    def _normalize_product_sort_order(self, connection: sqlite3.Connection) -> None:
        object_rows = connection.execute(
            f"SELECT DISTINCT object_id FROM {PRODUCTS_TABLE}"
        ).fetchall()
        for row in object_rows:
            self._normalize_sort_order(
                connection,
                PRODUCTS_TABLE,
                "name COLLATE NOCASE, serial_number COLLATE NOCASE, id",
                "object_id = ?",
                (row["object_id"],),
            )

    def _sync_pay_rates(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT id, name, category, student_allowed, salary, salary_type
            FROM Positions
            WHERE is_active = 1
            """
        ).fetchall()
        for row in rows:
            for category in pay_categories_for_position(row["category"] or NO_CATEGORY, bool(row["student_allowed"])):
                connection.execute(
                    """
                    INSERT INTO PayRates (position_id, category, salary, far_trip_salary, salary_type)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(position_id, category) DO NOTHING
                    """,
                    (
                        row["id"],
                        category,
                        row["salary"] or "",
                        "",
                        _normalize_salary_type(row["salary_type"] or "hourly"),
                    ),
                )


class DirectoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_items(self, table_key: str, active_only: bool = True) -> list[DirectoryItem]:
        table = self._table(table_key)
        if table_key == "positions":
            fields = (
                "id, name, is_active, category, student_allowed, salary, salary_type, employee_group, "
                "'' AS project_number, '' AS contract_number, '' AS customer, '' AS contract_type, '' AS object_type, "
                "'' AS object_subtype, '' AS signed_date, '' AS due_date, '' AS object_status"
            )
        elif table_key == "objects":
            fields = (
                "id, name, is_active, '' AS category, 0 AS student_allowed, '' AS salary, "
                "'hourly' AS salary_type, '' AS employee_group, project_number, contract_number, customer, "
                "contract_type, object_type, object_subtype, signed_date, due_date, object_status"
            )
        else:
            fields = (
                "id, name, is_active, '' AS category, 0 AS student_allowed, '' AS salary, "
                "'hourly' AS salary_type, '' AS employee_group, '' AS project_number, '' AS contract_number, '' AS customer, "
                "'' AS contract_type, '' AS object_type, '' AS object_subtype, '' AS signed_date, "
                "'' AS due_date, '' AS object_status"
            )
        sql = f"SELECT {fields} FROM {table}"
        if active_only:
            sql += " WHERE is_active = 1"
        if table_key == "objects":
            sql += " ORDER BY sort_order, name COLLATE NOCASE, id"
        with self.database.connect() as connection:
            items = [
                DirectoryItem(
                    name=row["name"],
                    id=row["id"],
                    is_active=bool(row["is_active"]),
                    category=row["category"] or "",
                    student_allowed=bool(row["student_allowed"]),
                    salary=row["salary"] or "",
                    salary_type=row["salary_type"] or "hourly",
                    group=row["employee_group"] or "",
                    project_number=row["project_number"] or "",
                    contract_number=row["contract_number"] or "",
                    customer=row["customer"] or "",
                    contract_type=row["contract_type"] or "",
                    object_type=row["object_type"] or "",
                    object_subtype=row["object_subtype"] or "",
                    signed_date=row["signed_date"] or "",
                    due_date=row["due_date"] or "",
                    object_status=row["object_status"] or ObjectStatus.IN_PROGRESS.value,
                )
                for row in connection.execute(sql)
            ]
        if table_key == "objects":
            return items
        return sorted(items, key=lambda item: item.name.casefold())

    def rename(self, table_key: str, item_id: int, name: str) -> None:
        table = self._table(table_key)
        normalized = name.strip()
        if not normalized:
            raise ValueError("Название справочника не может быть пустым")
        with self.database.connect() as connection:
            old_name = ""
            if table_key == "employee_groups":
                row = connection.execute(f"SELECT name FROM {table} WHERE id = ?", (item_id,)).fetchone()
                old_name = str(row["name"] or "") if row else ""
            connection.execute(f"UPDATE {table} SET name = ? WHERE id = ?", (normalized, item_id))
            if table_key == "employee_groups" and old_name:
                connection.execute(
                    "UPDATE Positions SET employee_group = ? WHERE employee_group = ?",
                    (normalized, old_name),
                )

    def move(self, table_key: str, item_id: int, direction: int) -> None:
        if table_key != "objects":
            raise ValueError("Ручной порядок поддерживается только для объектов и изделий")
        with self.database.connect() as connection:
            self._move_ordered_row(connection, OBJECTS_TABLE, item_id, direction)

    def ui_setting(self, key: str, default: str = "") -> str:
        with self.database.connect() as connection:
            row = connection.execute("SELECT value FROM Settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_ui_setting(self, key: str, value: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO Settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def list_aliases(self) -> list[AliasItem]:
        items: list[AliasItem] = []
        with self.database.connect() as connection:
            for alias_type, (table, target_column, target_table, target_name_column) in ALIAS_DEFINITIONS.items():
                rows = connection.execute(
                    f"""
                    SELECT a.alias_normalized, a.original_alias, a.{target_column} AS target_id,
                           COALESCE(t.{target_name_column}, '') AS target_name
                    FROM {ALIASES_SCHEMA}.{table} a
                    LEFT JOIN {target_table} t ON t.id = a.{target_column}
                    ORDER BY a.original_alias COLLATE NOCASE
                    """
                ).fetchall()
                items.extend(
                    AliasItem(
                        alias_type=alias_type,
                        original_alias=str(row["original_alias"] or ""),
                        target_id=int(row["target_id"]),
                        target_name=str(row["target_name"] or ""),
                        alias_normalized=str(row["alias_normalized"] or ""),
                    )
                    for row in rows
                )
        return sorted(items, key=lambda item: (item.alias_type, item.original_alias.casefold()))

    def save_alias(
        self,
        alias: AliasItem,
        previous_type: str = "",
        previous_normalized: str = "",
    ) -> None:
        original = alias.original_alias.strip()
        normalized = _normalize_alias(original)
        if not original:
            raise ValueError("Укажите алиас")
        if alias.alias_type not in ALIAS_DEFINITIONS:
            raise ValueError("Выберите тип алиаса")
        table, target_column, target_table, _target_name = ALIAS_DEFINITIONS[alias.alias_type]
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            target = connection.execute(
                f"SELECT id FROM {target_table} WHERE id = ?", (alias.target_id,)
            ).fetchone()
            if target is None:
                raise ValueError("Выбранная запись справочника не найдена")
            if previous_type in ALIAS_DEFINITIONS and previous_normalized:
                previous_table = ALIAS_DEFINITIONS[previous_type][0]
                connection.execute(
                    f"DELETE FROM {ALIASES_SCHEMA}.{previous_table} WHERE alias_normalized = ?",
                    (previous_normalized,),
                )
            connection.execute(
                f"""
                INSERT INTO {ALIASES_SCHEMA}.{table}(
                    alias_normalized, original_alias, {target_column}, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(alias_normalized) DO UPDATE SET
                    original_alias = excluded.original_alias,
                    {target_column} = excluded.{target_column},
                    updated_at = excluded.updated_at
                """,
                (normalized, original, alias.target_id, now, now),
            )

    def delete_alias(self, alias_type: str, alias_normalized: str) -> None:
        if alias_type not in ALIAS_DEFINITIONS:
            raise ValueError("Неизвестный тип алиаса")
        table = ALIAS_DEFINITIONS[alias_type][0]
        with self.database.connect() as connection:
            connection.execute(
                f"DELETE FROM {ALIASES_SCHEMA}.{table} WHERE alias_normalized = ?",
                (alias_normalized,),
            )

    def upsert(self, table_key: str, name: str) -> int:
        table = self._table(table_key)
        normalized = name.strip()
        if not normalized:
            raise ValueError("Название справочника не может быть пустым")
        with self.database.connect() as connection:
            if table_key == "positions":
                seed_map = load_position_seed_map()
                seed = seed_map.get(normalized)
                category = seed.category if seed else _default_position_category(normalized)
                student_allowed = int(seed.student_allowed) if seed else int(_default_student_allowed(normalized))
                salary = seed.salary if seed else ""
                salary_type = seed.salary_type if seed else "hourly"
                group = self._resolve_employee_group(
                    connection,
                    seed.group if seed else _default_position_group(normalized),
                )
                if seed:
                    connection.execute(
                        """
                        INSERT INTO Positions (name, category, student_allowed, salary, salary_type, employee_group, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(name) DO UPDATE SET
                            is_active = 1,
                            category = excluded.category,
                            student_allowed = excluded.student_allowed,
                            salary = excluded.salary,
                            salary_type = excluded.salary_type,
                            employee_group = excluded.employee_group
                        """,
                        (normalized, category, student_allowed, salary, salary_type, group),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO Positions (name, category, student_allowed, salary, salary_type, employee_group, is_active)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(name) DO UPDATE SET is_active = 1
                        """,
                        (normalized, category, student_allowed, salary, salary_type, group),
                    )
            elif table_key == "objects":
                connection.execute(
                    f"""
                    INSERT INTO {OBJECTS_TABLE} (name, is_active, sort_order)
                    VALUES (?, 1, COALESCE((SELECT MAX(sort_order) + 1 FROM {OBJECTS_TABLE}), 1))
                    ON CONFLICT(name) DO UPDATE SET is_active = 1
                    """,
                    (normalized,),
                )
            else:
                connection.execute(
                    f"INSERT INTO {table} (name, is_active) VALUES (?, 1) "
                    "ON CONFLICT(name) DO UPDATE SET is_active = 1",
                    (normalized,),
                )
            row = connection.execute(f"SELECT id FROM {table} WHERE name = ?", (normalized,)).fetchone()
            return int(row["id"])

    def set_position_category(self, item_id: int, category: str) -> None:
        with self.database.connect() as connection:
            connection.execute("UPDATE Positions SET category = ? WHERE id = ?", (category.strip(), item_id))

    def update_position_details(
        self,
        item_id: int,
        name: str,
        category: str,
        student_allowed: bool,
        salary: str,
        salary_type: str,
        group: str,
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Название должности не может быть пустым")
        if normalized.casefold() == "мастер чистоты":
            category = "—"
            student_allowed = False
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE Positions
                SET name = ?, category = ?, student_allowed = ?, salary = ?, salary_type = ?, employee_group = ?
                WHERE id = ?
                """,
                (
                    normalized,
                    category.strip() or "—",
                    int(student_allowed),
                    salary.strip(),
                    _normalize_salary_type(salary_type),
                    self._resolve_employee_group(connection, group.strip() or _default_position_group(normalized)),
                    item_id,
                ),
            )
            self.database._sync_pay_rates(connection)

    def _resolve_employee_group(self, connection: sqlite3.Connection, preferred: str) -> str:
        preferred = preferred.strip()
        rows = connection.execute("SELECT name, is_active FROM EmployeeGroups ORDER BY name").fetchall()
        if not rows:
            return preferred or _default_position_group("")
        for row in rows:
            name = str(row["name"] or "")
            if preferred and name.casefold() == preferred.casefold():
                return name
        for row in rows:
            if bool(row["is_active"]):
                return str(row["name"] or "")
        return str(rows[0]["name"] or preferred)

    def update_object_details(
        self,
        item_id: int,
        name: str,
        project_number: str,
        contract_number: str,
        customer: str,
        contract_type: str,
        object_type: str,
        object_subtype: str,
        signed_date: str,
        due_date: str,
        object_status: str,
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Укажите общее наименование объекта")
        with self.database.connect() as connection:
            connection.execute(
                f"""
                UPDATE {OBJECTS_TABLE}
                SET name = ?,
                    project_number = ?,
                    contract_number = ?,
                    customer = ?,
                    contract_type = ?,
                    object_type = ?,
                    object_subtype = ?,
                    signed_date = ?,
                    due_date = ?,
                    object_status = ?
                WHERE id = ?
                """,
                (
                    normalized,
                    project_number.strip(),
                    contract_number.strip(),
                    customer.strip(),
                    contract_type.strip(),
                    object_type.strip(),
                    object_subtype.strip(),
                    signed_date.strip(),
                    due_date.strip(),
                    _normalize_object_status(object_status),
                    item_id,
                ),
            )

    def list_calendar_days(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[WorkCalendarDay]:
        sql = "SELECT id, work_date, day_type, note FROM WorkCalendarDays"
        conditions: list[str] = []
        params: list[object] = []
        if date_from:
            conditions.append("work_date >= ?")
            params.append(date_from.isoformat())
        if date_to:
            conditions.append("work_date <= ?")
            params.append(date_to.isoformat())
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY work_date"
        with self.database.connect() as connection:
            return [_map_calendar_day(row) for row in connection.execute(sql, params)]

    def save_calendar_day(self, calendar_day: WorkCalendarDay) -> int:
        with self.database.connect() as connection:
            if calendar_day.id:
                duplicate = connection.execute(
                    """
                    SELECT id
                    FROM WorkCalendarDays
                    WHERE work_date = ? AND id <> ?
                    """,
                    (calendar_day.work_date.isoformat(), calendar_day.id),
                ).fetchone()
                if duplicate:
                    raise ValueError("Для выбранной даты уже есть настройка календаря")
                connection.execute(
                    """
                    UPDATE WorkCalendarDays
                    SET work_date = ?, day_type = ?, note = ?
                    WHERE id = ?
                    """,
                    (
                        calendar_day.work_date.isoformat(),
                        _normalize_day_type(calendar_day.day_type),
                        calendar_day.note.strip(),
                        calendar_day.id,
                    ),
                )
                return calendar_day.id
            cursor = connection.execute(
                """
                INSERT INTO WorkCalendarDays (work_date, day_type, note)
                VALUES (?, ?, ?)
                ON CONFLICT(work_date) DO UPDATE SET
                    day_type = excluded.day_type,
                    note = excluded.note
                """,
                (
                    calendar_day.work_date.isoformat(),
                    _normalize_day_type(calendar_day.day_type),
                    calendar_day.note.strip(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM WorkCalendarDays WHERE work_date = ?",
                (calendar_day.work_date.isoformat(),),
            ).fetchone()
            return int(row["id"] if row else cursor.lastrowid)

    def delete_calendar_day(self, item_id: int) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM WorkCalendarDays WHERE id = ?", (item_id,))

    def list_products(self, active_only: bool = False) -> list[ProductItem]:
        sql = f"""
            SELECT
                p.*,
                o.name AS object_name
            FROM {PRODUCTS_TABLE} p
            JOIN {OBJECTS_TABLE} o ON o.id = p.object_id
        """
        if active_only:
            sql += " WHERE p.is_active = 1"
        sql += " ORDER BY o.sort_order, o.name COLLATE NOCASE, p.sort_order, p.name COLLATE NOCASE, p.id"
        with self.database.connect() as connection:
            return [_map_product(row) for row in connection.execute(sql)]

    def save_product(self, product: ProductItem) -> int:
        if product.object_id is None:
            raise ValueError("Выберите объект для изделия")
        if not product.name.strip():
            raise ValueError("Укажите наименование изделия")
        readiness = max(0, min(100, int(product.readiness_percent or 0)))
        with self.database.connect() as connection:
            if product.id:
                current = connection.execute(
                    f"SELECT object_id FROM {PRODUCTS_TABLE} WHERE id = ?",
                    (product.id,),
                ).fetchone()
                object_changed = current is not None and current["object_id"] != product.object_id
                connection.execute(
                    f"""
                    UPDATE {PRODUCTS_TABLE}
                    SET object_id = ?,
                        serial_number = ?,
                        name = ?,
                        code = ?,
                        product_status = ?,
                        readiness_percent = ?,
                        start_date = ?,
                        release_date = ?,
                        is_active = ?
                    WHERE id = ?
                    """,
                    (
                        product.object_id,
                        product.serial_number.strip(),
                        product.name.strip(),
                        product.code.strip(),
                        _normalize_product_status(product.product_status),
                        readiness,
                        product.start_date.strip(),
                        product.release_date.strip(),
                        int(product.is_active),
                        product.id,
                    ),
                )
                if object_changed:
                    connection.execute(
                        f"""
                        UPDATE {PRODUCTS_TABLE}
                        SET sort_order = COALESCE(
                            (
                                SELECT MAX(other.sort_order) + 1
                                FROM {PRODUCTS_TABLE} other
                                WHERE other.object_id = ? AND other.id <> ?
                            ),
                            1
                        )
                        WHERE id = ?
                        """,
                        (product.object_id, product.id, product.id),
                    )
                return product.id
            cursor = connection.execute(
                f"""
                INSERT INTO {PRODUCTS_TABLE} (
                    object_id, serial_number, name, code, product_status,
                    readiness_percent, start_date, release_date, is_active, sort_order
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT MAX(sort_order) + 1 FROM {PRODUCTS_TABLE} WHERE object_id = ?), 1)
                )
                """,
                (
                    product.object_id,
                    product.serial_number.strip(),
                    product.name.strip(),
                    product.code.strip(),
                    _normalize_product_status(product.product_status),
                    readiness,
                    product.start_date.strip(),
                    product.release_date.strip(),
                    int(product.is_active),
                    product.object_id,
                ),
            )
            return int(cursor.lastrowid)

    def set_product_active(self, product_id: int, is_active: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE {PRODUCTS_TABLE} SET is_active = ? WHERE id = ?",
                (int(is_active), product_id),
            )

    def delete_product(self, product_id: int) -> None:
        with self.database.connect() as connection:
            used = connection.execute(
                "SELECT COUNT(*) AS count FROM WorkLogEntries WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if used and used["count"]:
                raise ValueError("Нельзя удалить изделие: оно используется в журнале работ")
            connection.execute(
                "DELETE FROM aliases_db.ProductAliases WHERE product_id = ?",
                (product_id,),
            )
            connection.execute(
                "UPDATE WorkBotImportRows SET product_id = NULL "
                "WHERE product_id = ? AND worklog_entry_id IS NULL",
                (product_id,),
            )
            connection.execute(f"DELETE FROM {PRODUCTS_TABLE} WHERE id = ?", (product_id,))

    def move_product(self, product_id: int, direction: int) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT object_id FROM {PRODUCTS_TABLE} WHERE id = ?",
                (product_id,),
            ).fetchone()
            if row is None:
                return
            self._move_ordered_row(
                connection,
                PRODUCTS_TABLE,
                product_id,
                direction,
                scope_column="object_id",
                scope_value=row["object_id"],
            )

    def _move_ordered_row(
        self,
        connection: sqlite3.Connection,
        table: str,
        item_id: int,
        direction: int,
        scope_column: str = "",
        scope_value: object | None = None,
    ) -> None:
        if direction not in (-1, 1):
            raise ValueError("Направление перемещения должно быть -1 или 1")
        where_sql = f"{scope_column} = ?" if scope_column else ""
        params = (scope_value,) if scope_column else ()
        fallback = "name COLLATE NOCASE, id"
        self.database._normalize_sort_order(connection, table, fallback, where_sql, params)
        row = connection.execute(
            f"SELECT id, sort_order FROM {table} WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return
        comparison = "<" if direction < 0 else ">"
        ordering = "DESC" if direction < 0 else "ASC"
        scope_clause = f" AND {scope_column} = ?" if scope_column else ""
        neighbor_params: tuple[object, ...] = (row["sort_order"],)
        if scope_column:
            neighbor_params += (scope_value,)
        neighbor = connection.execute(
            f"""
            SELECT id, sort_order
            FROM {table}
            WHERE sort_order {comparison} ?{scope_clause}
            ORDER BY sort_order {ordering}, id {ordering}
            LIMIT 1
            """,
            neighbor_params,
        ).fetchone()
        if neighbor is None:
            return
        connection.execute(
            f"UPDATE {table} SET sort_order = ? WHERE id = ?",
            (neighbor["sort_order"], row["id"]),
        )
        connection.execute(
            f"UPDATE {table} SET sort_order = ? WHERE id = ?",
            (row["sort_order"], neighbor["id"]),
        )

    def list_pay_rates(self) -> list[PayRate]:
        with self.database.connect() as connection:
            self.database._sync_pay_rates(connection)
            position_rows = connection.execute(
                """
                SELECT id, name, category, student_allowed
                FROM Positions
                WHERE is_active = 1
                """
            ).fetchall()
            expected = {
                row["id"]: set(pay_categories_for_position(row["category"] or NO_CATEGORY, bool(row["student_allowed"])))
                for row in position_rows
            }
            rows = connection.execute(
                """
                SELECT
                    pr.id,
                    pr.position_id,
                    p.name AS position_name,
                    pr.category,
                    pr.salary,
                    pr.far_trip_salary,
                    pr.salary_type,
                    pr.far_trip_coeff,
                    pr.near_trip_coeff,
                    pr.holiday_coeff,
                    pr.saturday_coeff
                FROM PayRates pr
                JOIN Positions p ON p.id = pr.position_id
                WHERE p.is_active = 1
                ORDER BY p.name COLLATE NOCASE, pr.category
                """
            ).fetchall()
        return [
            PayRate(
                id=row["id"],
                position_id=row["position_id"],
                position_name=row["position_name"] or "",
                category=row["category"] or NO_CATEGORY,
                salary=row["salary"] or "",
                far_trip_salary=row["far_trip_salary"] or "",
                salary_type=_normalize_salary_type(row["salary_type"] or "hourly"),
                far_trip_coeff=row["far_trip_coeff"] or "1",
                near_trip_coeff=row["near_trip_coeff"] or "1",
                holiday_coeff=row["holiday_coeff"] or "1",
                saturday_coeff=row["saturday_coeff"] or "1",
            )
            for row in rows
            if row["category"] in expected.get(row["position_id"], set())
        ]

    def update_pay_rate(
        self,
        item_id: int,
        salary: str,
        far_trip_salary: str,
        salary_type: str,
        far_trip_coeff: str,
        near_trip_coeff: str,
        holiday_coeff: str,
        saturday_coeff: str,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE PayRates
                SET salary = ?,
                    far_trip_salary = ?,
                    salary_type = ?,
                    far_trip_coeff = ?,
                    near_trip_coeff = ?,
                    holiday_coeff = ?,
                    saturday_coeff = ?
                WHERE id = ?
                """,
                (
                    salary.strip(),
                    far_trip_salary.strip(),
                    _normalize_salary_type(salary_type),
                    _normalize_coefficient(far_trip_coeff),
                    _normalize_coefficient(near_trip_coeff),
                    _normalize_coefficient(holiday_coeff),
                    _normalize_coefficient(saturday_coeff),
                    item_id,
                ),
            )

    def set_active(self, table_key: str, item_id: int, is_active: bool) -> None:
        table = self._table(table_key)
        with self.database.connect() as connection:
            connection.execute(f"UPDATE {table} SET is_active = ? WHERE id = ?", (int(is_active), item_id))
            if table_key == "positions":
                self.database._sync_pay_rates(connection)

    def _table(self, table_key: str) -> str:
        try:
            return DIRECTORY_TABLES[table_key]
        except KeyError as exc:
            raise ValueError(f"Неизвестный справочник: {table_key}") from exc

    def delete(self, table_key: str, item_id: int) -> None:
        table = self._table(table_key)
        try:
            with self.database.connect() as connection:
                if table_key == "employee_groups":
                    row = connection.execute(f"SELECT name FROM {table} WHERE id = ?", (item_id,)).fetchone()
                    group_name = str(row["name"] or "") if row else ""
                    used = connection.execute(
                        "SELECT COUNT(*) AS count FROM Positions WHERE employee_group = ?",
                        (group_name,),
                    ).fetchone()
                    if int(used["count"] or 0):
                        raise ValueError("Нельзя удалить группу: она используется в справочнике должностей")
                if table_key == "objects":
                    products = connection.execute(
                        f"SELECT COUNT(*) AS count FROM {PRODUCTS_TABLE} WHERE object_id = ?",
                        (item_id,),
                    ).fetchone()
                    if products and products["count"]:
                        raise ValueError("Нельзя удалить объект: к нему привязаны изделия")
                    worklogs = connection.execute(
                        "SELECT COUNT(*) AS count FROM WorkLogEntries WHERE object_id = ?",
                        (item_id,),
                    ).fetchone()
                    if worklogs and worklogs["count"]:
                        raise ValueError("Нельзя удалить объект: он используется в журнале работ")
                    connection.execute(
                        "DELETE FROM aliases_db.ObjectAliases WHERE object_id = ?",
                        (item_id,),
                    )
                    connection.execute(
                        "UPDATE WorkBotImportRows SET object_id = NULL "
                        "WHERE object_id = ? AND worklog_entry_id IS NULL",
                        (item_id,),
                    )
                if table_key == "locations":
                    connection.execute(
                        "DELETE FROM aliases_db.LocationAliases WHERE location_id = ?",
                        (item_id,),
                    )
                if table_key == "work_types":
                    connection.execute(
                        "DELETE FROM aliases_db.WorkTypeAliases WHERE work_type_id = ?",
                        (item_id,),
                    )
                connection.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        except ValueError:
            raise
        except sqlite3.IntegrityError as exc:
            raise ValueError("Нельзя удалить элемент: он уже используется в журнале работ") from exc

    def category_for_position(self, position: str) -> str:
        normalized = position.strip()
        if not normalized:
            return ""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT category FROM Positions WHERE name = ?",
                (normalized,),
            ).fetchone()
        return str(row["category"] or "") if row else ""

    def student_allowed_for_position(self, position: str) -> bool:
        normalized = position.strip()
        if not normalized:
            return False
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT student_allowed FROM Positions WHERE name = ?",
                (normalized,),
            ).fetchone()
        return bool(row["student_allowed"]) if row else False

    def apply_department_defaults(self, department: str) -> None:
        if not department.strip():
            return
        with self.database.connect() as connection:
            self._apply_department_table(connection, "positions", "Positions", department)
            self._apply_department_table(connection, "work_types", "WorkTypes", department)
            self._apply_department_table(connection, "locations", "Locations", department)

    def _apply_department_table(
        self,
        connection: sqlite3.Connection,
        dictionary_name: str,
        table_name: str,
        department: str,
    ) -> None:
        seeds = load_positions() if dictionary_name == "positions" else load_directory_seeds(dictionary_name)
        if not seeds:
            return
        connection.execute(f"UPDATE {table_name} SET is_active = 0")
        for seed in seeds:
            is_active = department_names_match(seed.departments, department)
            connection.execute(
                f"UPDATE {table_name} SET is_active = ? WHERE name = ?",
                (int(is_active), seed.name),
            )


class EmployeeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, search: str = "", position: str = "", group: str = "") -> list[Employee]:
        query = f"""
            SELECT e.*
            FROM {EMPLOYEES_TABLE} e
            LEFT JOIN Positions p ON p.name = e.position
            WHERE e.status = 'Активен'
        """
        params: list[object] = []
        if position.strip():
            query += " AND e.position = ?"
            params.append(position.strip())
        if group.strip():
            query += " AND COALESCE(p.employee_group, '') = ?"
            params.append(group.strip())
        query += " ORDER BY e.full_name"
        with self.database.connect() as connection:
            employees = [self._map(row) for row in connection.execute(query, params)]
        needle = search.strip().casefold()
        if not needle:
            return employees
        return [
            employee
            for employee in employees
            if needle in employee.full_name.casefold()
            or needle in employee.position.casefold()
            or needle in employee.category.casefold()
            or needle in employee.mobile_phone.casefold()
        ]

    def get(self, employee_id: int) -> Employee | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"""
                SELECT e.*
                FROM {EMPLOYEES_TABLE} e
                WHERE e.id = ?
                """,
                (employee_id,),
            ).fetchone()
            return self._map(row) if row else None

    def save(self, employee: Employee) -> int:
        with self.database.connect() as connection:
            if employee.id:
                connection.execute(
                    f"""
                    UPDATE {EMPLOYEES_TABLE}
                    SET full_name = ?, position = ?, category = ?, status = ?, mobile_phone = ?
                    WHERE id = ?
                    """,
                    (
                        employee.full_name.strip(),
                        employee.position.strip(),
                        employee.category.strip(),
                        employee.status,
                        employee.mobile_phone.strip(),
                        employee.id,
                    ),
                )
                return employee.id
            cursor = connection.execute(
                f"""
                INSERT INTO {EMPLOYEES_TABLE} (full_name, position, category, status, mobile_phone)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(full_name) DO UPDATE SET
                    position = excluded.position,
                    category = excluded.category,
                    status = excluded.status,
                    mobile_phone = CASE
                        WHEN excluded.mobile_phone <> '' THEN excluded.mobile_phone
                        ELSE mobile_phone
                    END
                """,
                (
                    employee.full_name.strip(),
                    employee.position.strip(),
                    employee.category.strip(),
                    employee.status,
                    employee.mobile_phone.strip(),
                ),
            )
            row = connection.execute(
                f"SELECT id FROM {EMPLOYEES_TABLE} WHERE full_name = ?",
                (employee.full_name.strip(),),
            ).fetchone()
            return int(row["id"] if row else cursor.lastrowid)

    def delete(self, employee_id: int) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM WorkLogEntries WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if row and row["count"]:
                raise ValueError("Нельзя удалить сотрудника: у него есть записи работ")
            connection.execute(
                "DELETE FROM aliases_db.EmployeeAliases WHERE employee_id = ?",
                (employee_id,),
            )
            connection.execute(
                "DELETE FROM MaxUserBindings WHERE employee_id = ?",
                (employee_id,),
            )
            connection.execute(
                "UPDATE WorkBotImportRows SET employee_id = NULL "
                "WHERE employee_id = ? AND worklog_entry_id IS NULL",
                (employee_id,),
            )
            connection.execute(f"DELETE FROM {EMPLOYEES_TABLE} WHERE id = ?", (employee_id,))

    def _map(self, row: sqlite3.Row) -> Employee:
        return Employee(
            id=row["id"],
            full_name=row["full_name"],
            position=row["position"] or "",
            category=row["category"] or "",
            status=row["status"],
            mobile_phone=row["mobile_phone"] or "",
        )


class WorkLogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save(self, entry: WorkLogEntry) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            if entry.id:
                connection.execute(
                    """
                    UPDATE WorkLogEntries
                    SET employee_id = ?, work_date = ?, location_id = ?, object_id = ?,
                        product_id = ?, work_type_id = ?, description = ?, hours = ?, comment = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    self._params(entry, now) + (entry.id,),
                )
                return entry.id
            cursor = connection.execute(
                """
                INSERT INTO WorkLogEntries (
                    employee_id, work_date, location_id, object_id, product_id, work_type_id,
                    description, hours, comment, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._params(entry, now, include_created=True),
            )
            return int(cursor.lastrowid)

    def list_for_employee_date(self, employee_id: int, work_date: date) -> list[WorkLogEntry]:
        return self.list_entries(employee_id=employee_id, date_from=work_date, date_to=work_date)

    def list_entries(
        self,
        employee_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        object_id: int | None = None,
        product_id: int | None = None,
    ) -> list[WorkLogEntry]:
        sql = WORKLOG_SELECT
        conditions: list[str] = []
        params: list[object] = []
        if employee_id:
            conditions.append("w.employee_id = ?")
            params.append(employee_id)
        if date_from:
            conditions.append("w.work_date >= ?")
            params.append(date_from.isoformat())
        if date_to:
            conditions.append("w.work_date <= ?")
            params.append(date_to.isoformat())
        if object_id:
            conditions.append("w.object_id = ?")
            params.append(object_id)
        if product_id:
            conditions.append("w.product_id = ?")
            params.append(product_id)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY w.work_date, w.created_at"
        with self.database.connect() as connection:
            return [self._map(row) for row in connection.execute(sql, params)]

    def get(self, entry_id: int) -> WorkLogEntry | None:
        with self.database.connect() as connection:
            row = connection.execute(WORKLOG_SELECT + " WHERE w.id = ?", (entry_id,)).fetchone()
            return self._map(row) if row else None

    def last_for_employee(self, employee_id: int) -> WorkLogEntry | None:
        with self.database.connect() as connection:
            row = connection.execute(
                WORKLOG_SELECT + " WHERE w.employee_id = ? ORDER BY w.created_at DESC LIMIT 1",
                (employee_id,),
            ).fetchone()
            return self._map(row) if row else None

    def _params(self, entry: WorkLogEntry, now: str, include_created: bool = False) -> tuple[object, ...]:
        params = (
            entry.employee_id,
            entry.work_date.isoformat(),
            entry.location_id,
            entry.object_id,
            entry.product_id,
            entry.work_type_id,
            entry.description.strip(),
            entry.hours,
            entry.comment.strip(),
        )
        return params + (now, now) if include_created else params + (now,)

    def _map(self, row: sqlite3.Row) -> WorkLogEntry:
        return WorkLogEntry(
            id=row["id"],
            employee_id=row["employee_id"],
            work_date=date.fromisoformat(row["work_date"]),
            location_id=row["location_id"],
            object_id=row["object_id"],
            product_id=row["product_id"],
            work_type_id=row["work_type_id"],
            description=row["description"] or "",
            hours=float(row["hours"] or 0),
            comment=row["comment"] or "",
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            employee_name=row["employee_name"] or "",
            location_name=row["location_name"] or "",
            object_name=row["object_name"] or "",
            product_name=row["product_name"] or "",
            work_type_name=row["work_type_name"] or "",
        )


WORKLOG_SELECT = f"""
    SELECT
        w.*,
        e.full_name AS employee_name,
        l.name AS location_name,
        o.name AS object_name,
        p.name AS product_name,
        wt.name AS work_type_name
    FROM WorkLogEntries w
    JOIN {EMPLOYEES_TABLE} e ON e.id = w.employee_id
    LEFT JOIN Locations l ON l.id = w.location_id
    LEFT JOIN {OBJECTS_TABLE} o ON o.id = w.object_id
    LEFT JOIN {PRODUCTS_TABLE} p ON p.id = w.product_id
    LEFT JOIN WorkTypes wt ON wt.id = w.work_type_id
"""


MAIN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS WorkTypes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT '1-3',
    student_allowed INTEGER NOT NULL DEFAULT 0,
    salary TEXT NOT NULL DEFAULT '',
    salary_type TEXT NOT NULL DEFAULT 'hourly',
    employee_group TEXT NOT NULL DEFAULT 'Рабочие',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS EmployeeGroups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS PayRates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL REFERENCES Positions(id) ON DELETE CASCADE,
    category TEXT NOT NULL DEFAULT '—',
    salary TEXT NOT NULL DEFAULT '',
    far_trip_salary TEXT NOT NULL DEFAULT '',
    salary_type TEXT NOT NULL DEFAULT 'hourly',
    far_trip_coeff TEXT NOT NULL DEFAULT '1',
    near_trip_coeff TEXT NOT NULL DEFAULT '1',
    holiday_coeff TEXT NOT NULL DEFAULT '1',
    saturday_coeff TEXT NOT NULL DEFAULT '1',
    UNIQUE(position_id, category)
);

CREATE TABLE IF NOT EXISTS Locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS WorkLogEntries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    location_id INTEGER REFERENCES Locations(id),
    object_id INTEGER,
    product_id INTEGER,
    work_type_id INTEGER REFERENCES WorkTypes(id),
    description TEXT NOT NULL DEFAULT '',
    hours REAL NOT NULL DEFAULT 0,
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ImportBatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL DEFAULT 'legacy_excel',
    source_file_name TEXT NOT NULL,
    source_file_hash TEXT NOT NULL,
    period_from TEXT NOT NULL DEFAULT '',
    period_to TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL,
    total_rows INTEGER NOT NULL DEFAULT 0,
    imported_rows INTEGER NOT NULL DEFAULT 0,
    skipped_rows INTEGER NOT NULL DEFAULT 0,
    error_rows INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ImportRows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES ImportBatches(id) ON DELETE CASCADE,
    sheet_name TEXT NOT NULL,
    excel_row INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    employee_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    worklog_entry_id INTEGER REFERENCES WorkLogEntries(id)
);

CREATE TABLE IF NOT EXISTS WorkCalendarDays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_date TEXT NOT NULL UNIQUE,
    day_type TEXT NOT NULL DEFAULT 'Рабочий день',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS MaxUserBindings (
    max_user_id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    username_snapshot TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS WorkBotImportRows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    max_message_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_index INTEGER NOT NULL,
    source_kind TEXT NOT NULL,
    sender_id INTEGER NOT NULL,
    chat_id INTEGER,
    received_at TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    source_fragment TEXT NOT NULL DEFAULT '',
    employee_text TEXT NOT NULL DEFAULT '',
    work_date TEXT NOT NULL,
    work_types TEXT NOT NULL DEFAULT '',
    hours REAL NOT NULL DEFAULT 0,
    object_text TEXT NOT NULL DEFAULT '',
    location_text TEXT NOT NULL DEFAULT '',
    product_text TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    employee_id INTEGER,
    object_id INTEGER,
    location_id INTEGER REFERENCES Locations(id),
    work_type_id INTEGER REFERENCES WorkTypes(id),
    product_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new',
    error_message TEXT NOT NULL DEFAULT '',
    worklog_entry_id INTEGER UNIQUE REFERENCES WorkLogEntries(id),
    imported_at TEXT,
    reviewed_by TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(max_message_id, revision, source_index)
);

CREATE TABLE IF NOT EXISTS Settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worklog_employee_date ON WorkLogEntries(employee_id, work_date);
CREATE INDEX IF NOT EXISTS idx_worklog_date ON WorkLogEntries(work_date);
CREATE INDEX IF NOT EXISTS idx_worklog_product ON WorkLogEntries(product_id);
CREATE INDEX IF NOT EXISTS idx_payrates_position ON PayRates(position_id);
CREATE INDEX IF NOT EXISTS idx_import_rows_batch ON ImportRows(batch_id);
CREATE INDEX IF NOT EXISTS idx_work_calendar_date ON WorkCalendarDays(work_date);
CREATE INDEX IF NOT EXISTS idx_workbot_import_status ON WorkBotImportRows(status);
CREATE INDEX IF NOT EXISTS idx_workbot_import_date ON WorkBotImportRows(work_date);
CREATE INDEX IF NOT EXISTS idx_workbot_import_message ON WorkBotImportRows(max_message_id, revision);
CREATE UNIQUE INDEX IF NOT EXISTS idx_import_batches_completed_hash
    ON ImportBatches(source_file_hash)
    WHERE status = 'completed';
"""


COMPONENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS employees_db.Employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE,
    position TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Активен',
    mobile_phone TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS objects_db.Objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    project_number TEXT NOT NULL DEFAULT '',
    contract_number TEXT NOT NULL DEFAULT '',
    customer TEXT NOT NULL DEFAULT '',
    contract_type TEXT NOT NULL DEFAULT '',
    object_type TEXT NOT NULL DEFAULT '',
    object_subtype TEXT NOT NULL DEFAULT '',
    signed_date TEXT NOT NULL DEFAULT '',
    due_date TEXT NOT NULL DEFAULT '',
    object_status TEXT NOT NULL DEFAULT 'В работе',
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products_db.Products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id INTEGER NOT NULL,
    serial_number TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    product_status TEXT NOT NULL DEFAULT 'В изготовлении',
    readiness_percent INTEGER NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL DEFAULT '',
    release_date TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS products_db.idx_products_object ON Products(object_id);

CREATE TABLE IF NOT EXISTS aliases_db.EmployeeAliases (
    alias_normalized TEXT PRIMARY KEY,
    original_alias TEXT NOT NULL,
    employee_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases_db.ObjectAliases (
    alias_normalized TEXT PRIMARY KEY,
    original_alias TEXT NOT NULL,
    object_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases_db.LocationAliases (
    alias_normalized TEXT PRIMARY KEY,
    original_alias TEXT NOT NULL,
    location_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases_db.WorkTypeAliases (
    alias_normalized TEXT PRIMARY KEY,
    original_alias TEXT NOT NULL,
    work_type_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases_db.ProductAliases (
    alias_normalized TEXT PRIMARY KEY,
    original_alias TEXT NOT NULL,
    product_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _default_position_category(name: str) -> str:
    return "—" if name.strip().casefold() == "мастер чистоты" else "1-3"


def _database_path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _normalize_alias(value: str) -> str:
    return " ".join(value.replace("ё", "е").replace("Ё", "Е").casefold().split())


def _default_student_allowed(name: str) -> bool:
    normalized = name.strip().casefold()
    return normalized in {
        "электромонтажник",
        "слесарь",
        "инженер асутп",
        "слесарь-электромонтажник",
        "слесарь кипиа",
    }


def _default_position_group(name: str) -> str:
    normalized = name.strip().casefold()
    return "ИТР" if any(marker in normalized for marker in ("инженер", "мастер", "специалист", "руководител")) else "Рабочие"


def _normalize_salary_type(value: str) -> str:
    return "monthly" if value == "monthly" else "hourly"


def _normalize_coefficient(value: str) -> str:
    normalized = value.strip().replace(",", ".")
    return normalized or "1"


def _normalize_object_status(value: str) -> str:
    allowed = {status.value for status in ObjectStatus}
    return value.strip() if value.strip() in allowed else ObjectStatus.IN_PROGRESS.value


def _normalize_product_status(value: str) -> str:
    allowed = {status.value for status in ProductStatus}
    return value.strip() if value.strip() in allowed else ProductStatus.IN_PROGRESS.value


def _normalize_day_type(value: str) -> str:
    allowed = {day_type.value for day_type in WorkDayType}
    return value.strip() if value.strip() in allowed else WorkDayType.WORKDAY.value


def _map_calendar_day(row: sqlite3.Row) -> WorkCalendarDay:
    return WorkCalendarDay(
        id=row["id"],
        work_date=date.fromisoformat(row["work_date"]),
        day_type=_normalize_day_type(row["day_type"] or ""),
        note=row["note"] or "",
    )


def _map_product(row: sqlite3.Row) -> ProductItem:
    return ProductItem(
        id=row["id"],
        object_id=row["object_id"],
        object_name=row["object_name"] or "",
        serial_number=row["serial_number"] or "",
        name=row["name"] or "",
        code=row["code"] or "",
        product_status=_normalize_product_status(row["product_status"] or ""),
        readiness_percent=int(row["readiness_percent"] or 0),
        start_date=row["start_date"] or "",
        release_date=row["release_date"] or "",
        is_active=bool(row["is_active"]),
    )
