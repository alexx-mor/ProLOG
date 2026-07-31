"""Integration tests for fractional hours and the WorkBot inbox."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from hours import format_hours, normalize_hours, parse_hours
from integrations.workbot.models import STATUS_CHANGED, STATUS_READY
from integrations.workbot.repository import WorkBotRepository
from integrations.workbot.service import WorkBotIntegrationService
from models import Employee
from services import DirectoryService, EmployeeService, WorkLogService


def test_fractional_hours_are_not_rounded() -> None:
    assert normalize_hours("7,5") == 7.5
    assert parse_hours("10.25") == 10.25
    assert format_hours(7.5) == "7,5"


def test_workbot_sync_is_idempotent_and_preserves_revisions(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    service, worklogs, employee_id, location_id, object_id, work_type_id = _create_prolog(tmp_path)

    first = service.sync(source_path)
    second = service.sync(source_path)
    assert first.added_rows == 1
    assert second.added_rows == 0
    assert second.unchanged_messages == 1

    row = service.list_rows()[0]
    assert row.status == STATUS_READY
    assert row.hours == 7.5
    worklog_id = service.import_row(
        row.id,
        employee_id=employee_id,
        work_date=date(2026, 7, 30),
        location_id=location_id,
        object_id=object_id,
        work_type_id=work_type_id,
        description="Монтаж",
        hours=7.5,
        remember=True,
    )
    assert worklogs.get(worklog_id).hours == 7.5
    with pytest.raises(ValueError, match="уже импортирован"):
        service.import_row(
            row.id,
            employee_id=employee_id,
            work_date=date(2026, 7, 30),
            location_id=location_id,
            object_id=object_id,
            work_type_id=work_type_id,
            description="Монтаж",
            hours=7.5,
            remember=True,
        )

    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE reports SET hours = 8.25 WHERE source_message_id = 'message-1'")
        connection.execute("UPDATE messages SET raw_text = raw_text || ' исправлено' WHERE max_message_id = 'message-1'")
    revised = service.sync(source_path)
    latest = service.list_rows()[0]
    assert revised.revised_messages == 1
    assert latest.revision == 2
    assert latest.status == STATUS_CHANGED
    assert latest.hours == 8.25


def _create_prolog(tmp_path: Path):
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories = DirectoryService(DirectoryRepository(database))
    employees = EmployeeService(EmployeeRepository(database), directories)
    worklogs = WorkLogService(WorkLogRepository(database), directories)
    employee_id = employees.save(Employee("Иванов Иван Иванович", "Слесарь", "1"))
    location_id = directories.ensure("locations", "Производство")
    object_id = directories.ensure("objects", "Жигалово")
    work_type_id = directories.ensure("work_types", "Монтаж")
    service = WorkBotIntegrationService(
        WorkBotRepository(database),
        employees,
        directories,
        worklogs,
    )
    return service, worklogs, employee_id, location_id, object_id, work_type_id


def _create_workbot_source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                max_user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                employee_name TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                max_message_id TEXT PRIMARY KEY,
                chat_id INTEGER,
                sender_id INTEGER NOT NULL,
                received_at TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                parse_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE reports (
                id INTEGER PRIMARY KEY,
                source_message_id TEXT NOT NULL UNIQUE,
                sender_id INTEGER NOT NULL,
                employee_name TEXT NOT NULL,
                work_date TEXT NOT NULL,
                work_types TEXT NOT NULL,
                hours REAL NOT NULL,
                object_name TEXT NOT NULL,
                location TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE historical_reports (
                id INTEGER PRIMARY KEY,
                source_message_id TEXT NOT NULL,
                source_index INTEGER NOT NULL,
                employee_name TEXT NOT NULL,
                work_date TEXT NOT NULL,
                work_types TEXT NOT NULL,
                hours REAL NOT NULL,
                object_name TEXT NOT NULL,
                location TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_fragment TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(source_message_id, source_index)
            );
            INSERT INTO users VALUES (100, 'Иван', 'Иванов', 'ivanov', 'Иванов Иван Иванович', '2026-07-30');
            INSERT INTO messages VALUES (
                'message-1', 10, 100, '2026-07-30T18:00:00', 'Отчет за день',
                'parsed', ''
            );
            INSERT INTO reports VALUES (
                1, 'message-1', 100, 'Иванов Иван Иванович', '2026-07-30',
                'Монтаж', 7.5, 'Жигалово', 'Производство', '2026-07-30T18:00:00'
            );
            """
        )
