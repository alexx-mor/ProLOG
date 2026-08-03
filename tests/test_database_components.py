"""Migration and isolation checks for component SQLite databases."""

from pathlib import Path
import sqlite3

from database import Database, DirectoryRepository, WorkLogRepository
from models import AliasItem


def test_legacy_component_rows_move_without_changing_ids(tmp_path: Path) -> None:
    core_path = tmp_path / "prolog.sqlite3"
    _create_legacy_database(core_path)

    database = Database(core_path)
    database.initialize()

    assert database.employees_path == tmp_path / "employees.sqlite3"
    assert database.objects_path == tmp_path / "objects.sqlite3"
    assert database.products_path == tmp_path / "products.sqlite3"
    assert database.aliases_path == tmp_path / "aliases.sqlite3"
    assert all(path.is_file() for path in database.database_paths().values())

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        legacy_tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM main.sqlite_master WHERE type = 'table'"
            )
        }
        assert not {
            "Employees",
            "Objects",
            "Products",
            "EmployeeAliases",
            "ObjectAliases",
            "LocationAliases",
            "ProductAliases",
        } & legacy_tables
        assert connection.execute(
            "SELECT full_name FROM employees_db.Employees WHERE id = 17"
        ).fetchone()[0] == "Иванов Иван Иванович"
        assert connection.execute(
            "SELECT name FROM objects_db.Objects WHERE id = 23"
        ).fetchone()[0] == "Жигалово"
        assert connection.execute(
            "SELECT name FROM products_db.Products WHERE id = 31"
        ).fetchone()[0] == "Шкаф управления"
        assert {
            row["table"]
            for row in connection.execute("PRAGMA foreign_key_list(WorkLogEntries)")
        } == {"Locations", "WorkTypes"}

    entry = WorkLogRepository(database).get(41)
    assert entry is not None
    assert entry.employee_name == "Иванов Иван Иванович"
    assert entry.object_name == "Жигалово"
    assert entry.product_name == "Шкаф управления"

    aliases = DirectoryRepository(database).list_aliases()
    assert [(item.alias_type, item.original_alias, item.target_id) for item in aliases] == [
        ("object", "Жимолово", 23)
    ]

    repository = DirectoryRepository(database)
    repository.save_alias(AliasItem("product", "ШУ 31", 31))
    assert any(item.original_alias == "ШУ 31" for item in repository.list_aliases())
    repository.delete_alias("product", "шу 31")
    assert all(item.original_alias != "ШУ 31" for item in repository.list_aliases())


def _create_legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE Employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL UNIQUE,
            position TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Активен',
            mobile_phone TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE Objects (
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
        CREATE TABLE Products (
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
        CREATE TABLE Locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE WorkTypes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE WorkLogEntries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES Employees(id),
            work_date TEXT NOT NULL,
            location_id INTEGER,
            object_id INTEGER REFERENCES Objects(id),
            product_id INTEGER REFERENCES Products(id),
            work_type_id INTEGER,
            description TEXT NOT NULL DEFAULT '',
            hours REAL NOT NULL DEFAULT 0,
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE EmployeeAliases (
            alias_normalized TEXT PRIMARY KEY,
            original_alias TEXT NOT NULL,
            employee_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE ObjectAliases (
            alias_normalized TEXT PRIMARY KEY,
            original_alias TEXT NOT NULL,
            object_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE LocationAliases (
            alias_normalized TEXT PRIMARY KEY,
            original_alias TEXT NOT NULL,
            location_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE ProductAliases (
            alias_normalized TEXT PRIMARY KEY,
            original_alias TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        INSERT INTO Employees (id, full_name, position, category)
        VALUES (17, 'Иванов Иван Иванович', 'Слесарь', '2');
        INSERT INTO Objects (id, name, sort_order) VALUES (23, 'Жигалово', 1);
        INSERT INTO Products (id, object_id, name, sort_order)
        VALUES (31, 23, 'Шкаф управления', 1);
        INSERT INTO Locations (id, name) VALUES (3, 'Производство');
        INSERT INTO WorkTypes (id, name) VALUES (5, 'Сборка шкафа');
        INSERT INTO WorkLogEntries (
            id, employee_id, work_date, location_id, object_id, product_id,
            work_type_id, description, hours, created_at, updated_at
        ) VALUES (
            41, 17, '2026-08-01', 3, 23, 31, 5, 'Сборка', 7.5,
            '2026-08-01T18:00:00', '2026-08-01T18:00:00'
        );
        INSERT INTO ObjectAliases (
            alias_normalized, original_alias, object_id, created_at, updated_at
        ) VALUES (
            'жимолово', 'Жимолово', 23, '2026-08-01T18:00:00', '2026-08-01T18:00:00'
        );
        """
    )
    connection.commit()
    connection.close()
