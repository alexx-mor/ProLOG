"""SQLite migration helpers for the production-stage directory."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


PRODUCTION_STAGE_SEED: tuple[tuple[str, str], ...] = (
    ("PREPARATION", "Подготовка"),
    ("METALWORK", "Слесарные работы"),
    ("EQUIPMENT_INSTALLATION", "Установка оборудования"),
    ("ELECTRICAL_INSTALLATION", "Электромонтаж"),
    ("MARKING", "Маркировка"),
    ("PROGRAMMING", "Программирование"),
    ("TESTING", "Проверка"),
    ("QUALITY_CONTROL", "ОТК"),
    ("PACKAGING", "Упаковка"),
    ("COMPLETED", "Готово"),
)


PRODUCTION_STAGES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionStages (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
    is_active INTEGER NOT NULL CHECK(is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""


def apply_production_stages_migration(connection: sqlite3.Connection) -> None:
    """Create and seed the standalone production-stage directory."""

    connection.execute(PRODUCTION_STAGES_SCHEMA_SQL)
    seed_production_stages(connection)


def seed_production_stages(connection: sqlite3.Connection) -> None:
    """Add missing defaults without overwriting user-managed fields."""

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sort_order, (code, name) in enumerate(PRODUCTION_STAGE_SEED, start=1):
        connection.execute(
            """
            INSERT INTO ProductionStages (
                uid, code, name, sort_order, is_active, created_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(code) DO NOTHING
            """,
            (str(uuid4()), code, name, sort_order, now, now),
        )
