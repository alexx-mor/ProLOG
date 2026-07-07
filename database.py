"""SQLite storage and repositories."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from category_rules import NO_CATEGORY, normalize_pay_category, pay_categories_for_position
from constants import DATABASE_FILE
from directory_files import (
    department_names_match,
    load_directory_seeds,
    load_names,
    load_position_category_map,
    load_position_seed_map,
    load_positions,
)
from models import DirectoryItem, Employee, PayRate, WorkLogEntry

logger = logging.getLogger(__name__)

DIRECTORY_TABLES = {
    "objects": "Objects",
    "positions": "Positions",
    "work_types": "WorkTypes",
    "locations": "Locations",
}

class Database:
    def __init__(self, path: Path = DATABASE_FILE) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._migrate(connection)
            self._seed(connection)

    def _migrate(self, connection: sqlite3.Connection) -> None:
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
        connection.execute("UPDATE Positions SET category = '—', student_allowed = 0 WHERE name = 'Мастер чистоты'")
        self._sync_pay_rates(connection)

    def _seed(self, connection: sqlite3.Connection) -> None:
        seed_values = {
            "Locations": load_names("locations"),
            "Objects": load_names("objects"),
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
                    INSERT INTO PayRates (position_id, category, salary, salary_type)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(position_id, category) DO NOTHING
                    """,
                    (
                        row["id"],
                        category,
                        row["salary"] or "",
                        _normalize_salary_type(row["salary_type"] or "hourly"),
                    ),
                )


class DirectoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_items(self, table_key: str, active_only: bool = True) -> list[DirectoryItem]:
        table = self._table(table_key)
        fields = (
            "id, name, is_active, category, student_allowed, salary, salary_type, employee_group"
            if table_key == "positions"
            else "id, name, is_active, '' AS category, 0 AS student_allowed, '' AS salary, 'hourly' AS salary_type, '' AS employee_group"
        )
        sql = f"SELECT {fields} FROM {table}"
        if active_only:
            sql += " WHERE is_active = 1"
        with self.database.connect() as connection:
            items = [
                DirectoryItem(
                    row["name"],
                    row["id"],
                    bool(row["is_active"]),
                    row["category"] or "",
                    bool(row["student_allowed"]),
                    row["salary"] or "",
                    row["salary_type"] or "hourly",
                    row["employee_group"] or "",
                )
                for row in connection.execute(sql)
            ]
        return sorted(items, key=lambda item: item.name.casefold())

    def rename(self, table_key: str, item_id: int, name: str) -> None:
        table = self._table(table_key)
        normalized = name.strip()
        if not normalized:
            raise ValueError("Название справочника не может быть пустым")
        with self.database.connect() as connection:
            connection.execute(f"UPDATE {table} SET name = ? WHERE id = ?", (normalized, item_id))

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
                group = seed.group if seed else _default_position_group(normalized)
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
                    group.strip() or "Рабочие",
                    item_id,
                ),
            )
            self.database._sync_pay_rates(connection)

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
                    pr.salary_type
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
                salary_type=_normalize_salary_type(row["salary_type"] or "hourly"),
            )
            for row in rows
            if row["category"] in expected.get(row["position_id"], set())
        ]

    def update_pay_rate(self, item_id: int, salary: str, salary_type: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE PayRates
                SET salary = ?, salary_type = ?
                WHERE id = ?
                """,
                (salary.strip(), _normalize_salary_type(salary_type), item_id),
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
                connection.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
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
        query = """
            SELECT e.*
            FROM Employees e
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
        ]

    def get(self, employee_id: int) -> Employee | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT e.*
                FROM Employees e
                WHERE e.id = ?
                """,
                (employee_id,),
            ).fetchone()
            return self._map(row) if row else None

    def save(self, employee: Employee) -> int:
        with self.database.connect() as connection:
            if employee.id:
                connection.execute(
                    """
                    UPDATE Employees
                    SET full_name = ?, position = ?, category = ?, status = ?
                    WHERE id = ?
                    """,
                    (
                        employee.full_name.strip(),
                        employee.position.strip(),
                        employee.category.strip(),
                        employee.status,
                        employee.id,
                    ),
                )
                return employee.id
            cursor = connection.execute(
                """
                INSERT INTO Employees (full_name, position, category, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(full_name) DO UPDATE SET
                    position = excluded.position,
                    category = excluded.category,
                    status = excluded.status
                """,
                (
                    employee.full_name.strip(),
                    employee.position.strip(),
                    employee.category.strip(),
                    employee.status,
                ),
            )
            row = connection.execute("SELECT id FROM Employees WHERE full_name = ?", (employee.full_name.strip(),)).fetchone()
            return int(row["id"] if row else cursor.lastrowid)

    def delete(self, employee_id: int) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM WorkLogEntries WHERE employee_id = ?",
                (employee_id,),
            ).fetchone()
            if row and row["count"]:
                raise ValueError("Нельзя удалить сотрудника: у него есть записи работ")
            connection.execute("DELETE FROM Employees WHERE id = ?", (employee_id,))

    def _map(self, row: sqlite3.Row) -> Employee:
        return Employee(
            id=row["id"],
            full_name=row["full_name"],
            position=row["position"] or "",
            category=row["category"] or "",
            status=row["status"],
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
                        work_type_id = ?, description = ?, hours = ?, comment = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    self._params(entry, now) + (entry.id,),
                )
                return entry.id
            cursor = connection.execute(
                """
                INSERT INTO WorkLogEntries (
                    employee_id, work_date, location_id, object_id, work_type_id,
                    description, hours, comment, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            work_type_id=row["work_type_id"],
            description=row["description"] or "",
            hours=int(row["hours"] or 0),
            comment=row["comment"] or "",
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            employee_name=row["employee_name"] or "",
            location_name=row["location_name"] or "",
            object_name=row["object_name"] or "",
            work_type_name=row["work_type_name"] or "",
        )


WORKLOG_SELECT = """
    SELECT
        w.*,
        e.full_name AS employee_name,
        l.name AS location_name,
        o.name AS object_name,
        wt.name AS work_type_name
    FROM WorkLogEntries w
    JOIN Employees e ON e.id = w.employee_id
    LEFT JOIN Locations l ON l.id = w.location_id
    LEFT JOIN Objects o ON o.id = w.object_id
    LEFT JOIN WorkTypes wt ON wt.id = w.work_type_id
"""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS Objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

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

CREATE TABLE IF NOT EXISTS PayRates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL REFERENCES Positions(id) ON DELETE CASCADE,
    category TEXT NOT NULL DEFAULT '—',
    salary TEXT NOT NULL DEFAULT '',
    salary_type TEXT NOT NULL DEFAULT 'hourly',
    UNIQUE(position_id, category)
);

CREATE TABLE IF NOT EXISTS Locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE,
    position TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Активен'
);

CREATE TABLE IF NOT EXISTS WorkLogEntries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES Employees(id),
    work_date TEXT NOT NULL,
    location_id INTEGER REFERENCES Locations(id),
    object_id INTEGER REFERENCES Objects(id),
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

CREATE TABLE IF NOT EXISTS Settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worklog_employee_date ON WorkLogEntries(employee_id, work_date);
CREATE INDEX IF NOT EXISTS idx_worklog_date ON WorkLogEntries(work_date);
CREATE INDEX IF NOT EXISTS idx_payrates_position ON PayRates(position_id);
CREATE INDEX IF NOT EXISTS idx_import_rows_batch ON ImportRows(batch_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_import_batches_completed_hash
    ON ImportBatches(source_file_hash)
    WHERE status = 'completed';
"""


def _default_position_category(name: str) -> str:
    return "—" if name.strip().casefold() == "мастер чистоты" else "1-3"


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
