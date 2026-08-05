"""Integration tests for fractional hours and the WorkBot inbox."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from hours import format_hours, normalize_hours, parse_hours
from integrations.workbot.models import (
    STATUS_CHANGED,
    STATUS_IMPORTED,
    STATUS_INVALID_HOURS,
    STATUS_NEEDS_EMPLOYEE,
    STATUS_NEEDS_WORK_TYPE,
    STATUS_READY,
)
from integrations.workbot.models import WorkBotUserLink
from integrations.workbot.matcher import detect_product
from integrations.workbot.repository import WorkBotRepository
from integrations.workbot.service import WorkBotIntegrationService
from models import AliasItem, Employee, ProductItem
from services import DirectoryService, EmployeeService, WorkLogService, normalize_mobile_phone


def test_fractional_hours_are_not_rounded() -> None:
    assert normalize_hours("7,5") == 7.5
    assert parse_hours("10.25") == 10.25
    assert format_hours(7.5) == "7,5"
    assert normalize_mobile_phone("8 (999) 123-45-67") == "+79991234567"


def test_product_is_detected_by_code() -> None:
    products = [
        ProductItem(id=10, object_id=4, name="Шкаф управления насосами", code="ШУ-12"),
        ProductItem(id=11, object_id=4, name="Шкаф управления вентиляцией", code="ШУ-13"),
    ]
    match = detect_product("Сегодня выполнил маркировку шкафа ШУ 12", products)
    assert match.product_id == 10
    assert match.reference == "ШУ-12"
    short_code = detect_product(
        "Работа со шкафом ШУ-12",
        [ProductItem(id=12, object_id=4, name="Шкаф 1", code="ШУ-1")],
    )
    assert short_code.product_id is None
    lower_case = detect_product(
        "сборка и маркировка шу3",
        [ProductItem(id=13, object_id=4, name="Шкаф 3", code="ШУ3")],
    )
    assert lower_case.product_id == 13
    serial_priority = detect_product(
        "Шкаф управления 2323",
        [
            ProductItem(id=14, object_id=4, name="Шкаф управления"),
            ProductItem(id=15, object_id=5, name="Шкаф управления", serial_number="2323"),
        ],
    )
    assert serial_priority.product_id == 15


def test_short_product_name_is_resolved_inside_matched_object(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    message = "31.07.26\nКеренцев Ф.В\nГазетная 23\nСборка Жигалово шув\n8-17"
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "UPDATE messages SET raw_text = ? WHERE max_message_id = 'message-1'",
            (message,),
        )
        connection.execute(
            "UPDATE reports SET work_types = ?, object_name = 'Жигалово', hours = 8 "
            "WHERE source_message_id = 'message-1'",
            (message,),
        )
    service, _worklogs, _employee_id, _location_id, object_id, *_rest = _create_prolog(tmp_path)
    other_object_id = service.directories.ensure("objects", "Другой объект")
    expected_product_id = service.directories.save_product(
        ProductItem(object_id=object_id, name="ШУВ", serial_number="3080")
    )
    service.directories.save_product(
        ProductItem(object_id=other_object_id, name="ШУВ", serial_number="2323")
    )

    service.sync(source_path)
    row = service.list_rows()[0]

    assert row.object_id == object_id
    assert row.product_id == expected_product_id
    assert row.raw_text == message


def test_product_serial_number_overrides_conflicting_object_text(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    message = "Сборка Жигалово шув 2323, 8 часов"
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "UPDATE messages SET raw_text = ? WHERE max_message_id = 'message-1'",
            (message,),
        )
        connection.execute(
            "UPDATE reports SET work_types = ?, object_name = 'Жигалово', hours = 8 "
            "WHERE source_message_id = 'message-1'",
            (message,),
        )
    service, _worklogs, _employee_id, _location_id, object_id, *_rest = _create_prolog(tmp_path)
    other_object_id = service.directories.ensure("objects", "Другой объект")
    service.directories.save_product(
        ProductItem(object_id=object_id, name="ШУВ", serial_number="3080")
    )
    expected_product_id = service.directories.save_product(
        ProductItem(object_id=other_object_id, name="ШУВ", serial_number="2323")
    )

    service.sync(source_path)
    row = service.list_rows()[0]

    assert row.object_id == other_object_id
    assert row.product_id == expected_product_id
    assert row.object_text == "Другой объект"
    assert row.raw_text == message


def test_numbered_message_is_split_by_object_and_hours(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    with sqlite3.connect(source_path) as connection:
        connection.execute("DELETE FROM reports")
        connection.execute(
            """
            UPDATE messages
            SET raw_text = ?, parse_status = 'invalid', parse_error = 'Свободная форма'
            WHERE max_message_id = 'message-1'
            """,
            (
                "Микулицкая Е.А\n"
                "Газетная 23 с8:00-17:00\n"
                "1) Сборка шкафа Жигалово ШУ2 N3076 (5часов)\n"
                "2) Установка контакторов и подключение их УНР ШУФ 9 (3часа)",
            ),
        )
    service, _worklogs, _employee_id, _location_id, object_id, _work_type_id, _product_id = (
        _create_prolog(tmp_path)
    )
    directories = service.directories
    assembly_id = directories.ensure("work_types", "Сборка шкафа")
    connection_id = directories.ensure("work_types", "Подключение")
    unr_id = directories.ensure("objects", "УНР")
    first_product_id = directories.save_product(
        ProductItem(object_id=object_id, name="ШУ2", serial_number="3076", code="ШУ2")
    )
    second_product_id = directories.save_product(
        ProductItem(object_id=unr_id, name="ШУФ 9", code="ШУФ 9")
    )
    directories.save_alias(
        AliasItem(
            "work_type",
            "Установка контакторов и подключение их",
            connection_id,
        )
    )

    result = service.sync(source_path)
    rows = sorted(service.list_rows(), key=lambda item: item.source_index)

    assert result.added_rows == 2
    assert [row.source_kind for row in rows] == ["segmented", "segmented"]
    assert [row.hours for row in rows] == [5.0, 3.0]
    assert [row.object_id for row in rows] == [object_id, unr_id]
    assert [row.product_id for row in rows] == [first_product_id, second_product_id]
    assert [row.work_type_id for row in rows] == [assembly_id, connection_id]
    assert "Жигалово" in rows[0].source_fragment
    assert "УНР" in rows[1].source_fragment


def test_historical_message_is_split_for_each_explicit_employee(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    fragment = (
        "Иванов И. И.\n"
        "Петров П. П.\n"
        "1) Монтаж Жигалово ШУ-12 (5 часов)\n"
        "2) Подключение УНР ШУФ 9 (3 часа)"
    )
    with sqlite3.connect(source_path) as connection:
        connection.execute("DELETE FROM reports")
        connection.execute(
            "UPDATE messages SET raw_text = ?, parse_status = 'parsed_legacy' WHERE max_message_id = 'message-1'",
            (fragment,),
        )
        for source_index, employee_name in enumerate(
            ("Иванов И.И.", "Петров П.П.")
        ):
            connection.execute(
                """
                INSERT INTO historical_reports (
                    source_message_id, source_index, employee_name, work_date, work_types,
                    hours, object_name, location, confidence, source_fragment, created_at
                ) VALUES ('message-1', ?, ?, '2026-07-30', ?, 8, 'Жигалово; УНР',
                          'Производство', 1, ?, '2026-07-30')
                """,
                (source_index, employee_name, fragment, fragment),
            )
    service, _worklogs, first_employee_id, _location_id, object_id, *_rest = _create_prolog(tmp_path)
    second_employee_id = service.employees.save(Employee("Петров Петр Петрович", "Слесарь", "1"))
    unr_id = service.directories.ensure("objects", "УНР")
    service.directories.ensure("work_types", "Подключение")
    service.directories.save_product(ProductItem(object_id=unr_id, name="ШУФ 9", code="ШУФ 9"))
    service.repository.save_employee_binding(100, first_employee_id, "Отправитель")

    result = service.sync(source_path)
    rows = sorted(service.list_rows(), key=lambda item: item.source_index)

    assert result.added_rows == 4
    assert {row.source_kind for row in rows} == {"historical_segmented"}
    assert [row.employee_id for row in rows] == [
        first_employee_id,
        first_employee_id,
        second_employee_id,
        second_employee_id,
    ]
    assert [row.object_id for row in rows] == [object_id, unr_id, object_id, unr_id]
    assert [row.hours for row in rows] == [5.0, 3.0, 5.0, 3.0]
    assert all(row.raw_text == fragment for row in rows)
    stats = service.inbox_stats(source_path)
    assert stats.source_messages == 1
    assert stats.total_rows == 4
    assert stats.imported_rows == 0
    assert stats.error_rows == 0


def test_single_workbot_row_with_multiple_names_is_split_by_employee(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    message = (
        "Иванов Иван Иванович\n"
        "Петров Петр Петрович\n"
        "Монтаж ШУ-12, 8 часов"
    )
    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE messages SET raw_text = ? WHERE max_message_id = 'message-1'", (message,))
        connection.execute(
            "UPDATE reports SET work_types = 'Монтаж', hours = 8 WHERE source_message_id = 'message-1'"
        )
    service, _worklogs, first_employee_id, *_rest = _create_prolog(tmp_path)
    second_employee_id = service.employees.save(Employee("Петров Петр Петрович", "Слесарь", "1"))
    service.repository.save_employee_binding(100, first_employee_id, "Отправитель")

    result = service.sync(source_path)
    rows = service.list_rows()

    assert result.added_rows == 2
    assert {row.source_kind for row in rows} == {"employee_segmented"}
    assert {row.employee_id for row in rows} == {first_employee_id, second_employee_id}
    assert {row.hours for row in rows} == {8.0}
    assert {row.status for row in rows} == {STATUS_READY}


def test_multiple_products_get_separate_rows_and_explicit_hours(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    message = "Монтаж ШУ-12 3 часа; ШУ-13 5 часов"
    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE messages SET raw_text = ? WHERE max_message_id = 'message-1'", (message,))
        connection.execute(
            "UPDATE reports SET work_types = ?, hours = 8 WHERE source_message_id = 'message-1'",
            (message,),
        )
    service, _worklogs, _employee_id, _location_id, object_id, *_rest = _create_prolog(tmp_path)
    second_product_id = service.directories.save_product(
        ProductItem(object_id=object_id, name="Второй шкаф", code="ШУ-13")
    )

    result = service.sync(source_path)
    rows = service.list_rows()

    assert result.added_rows == 2
    assert {row.source_kind for row in rows} == {"product_segmented"}
    assert {row.product_id for row in rows} == {_rest[-1], second_product_id}
    assert sorted(row.hours for row in rows) == [3.0, 5.0]
    assert {row.status for row in rows} == {STATUS_READY}


def test_multiple_products_without_allocation_require_manual_hours(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    message = "Монтаж шкафов ШУ-12 и ШУ-13"
    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE messages SET raw_text = ? WHERE max_message_id = 'message-1'", (message,))
        connection.execute(
            "UPDATE reports SET work_types = ?, hours = 8 WHERE source_message_id = 'message-1'",
            (message,),
        )
    service, _worklogs, _employee_id, _location_id, object_id, *_rest = _create_prolog(tmp_path)
    service.directories.save_product(ProductItem(object_id=object_id, name="Второй шкаф", code="ШУ-13"))

    service.sync(source_path)
    rows = service.list_rows()

    assert len(rows) == 2
    assert {row.hours for row in rows} == {0.0}
    assert {row.status for row in rows} == {STATUS_INVALID_HOURS}
    assert all("по каждому изделию" in row.error_message for row in rows)
    assert service.inbox_stats(source_path).error_rows == 2


def test_short_product_names_are_split_inside_message_object(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    message = "Монтаж Жигалово ШУ5 и ШУ2, всего 8 часов"
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "UPDATE messages SET raw_text = ? WHERE max_message_id = 'message-1'",
            (message,),
        )
        connection.execute(
            "UPDATE reports SET work_types = ?, object_name = 'Жигалово', hours = 8 "
            "WHERE source_message_id = 'message-1'",
            (message,),
        )
    service, _worklogs, _employee_id, _location_id, object_id, *_rest = _create_prolog(tmp_path)
    first_id = service.directories.save_product(ProductItem(object_id=object_id, name="ШУ5"))
    second_id = service.directories.save_product(ProductItem(object_id=object_id, name="ШУ2"))
    other_object_id = service.directories.ensure("objects", "Другой объект")
    service.directories.save_product(ProductItem(object_id=other_object_id, name="ШУ5"))

    service.sync(source_path)
    rows = service.list_rows()

    assert len(rows) == 2
    assert {row.product_id for row in rows} == {first_id, second_id}
    assert {row.object_id for row in rows} == {object_id}
    assert {row.status for row in rows} == {STATUS_INVALID_HOURS}
    assert all(row.raw_text == message for row in rows)


def test_inactive_work_type_is_not_offered_to_workbot(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    service, _worklogs, _employee_id, _location_id, _object_id, work_type_id, _product_id = (
        _create_prolog(tmp_path)
    )
    service.directories.save_alias(AliasItem("work_type", "Монтаж", work_type_id))
    service.directories.set_active("work_types", work_type_id, False)

    service.sync(source_path)
    row = service.list_rows()[0]

    assert row.work_type_id is None
    assert row.status == STATUS_NEEDS_WORK_TYPE


def test_imported_manual_links_survive_repeated_sync(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    service, worklogs, employee_id, location_id, _object_id, _work_type_id, _product_id = (
        _create_prolog(tmp_path)
    )
    service.sync(source_path)
    row = service.list_rows()[0]
    manual_object_id = service.directories.ensure("objects", "Электроцех (Газетная 23)")
    manual_work_type_id = service.directories.ensure("work_types", "Общепроизводственные")
    worklog_id = service.import_row(
        row.id,
        employee_id=employee_id,
        work_date=row.work_date,
        location_id=location_id,
        object_id=manual_object_id,
        work_type_id=manual_work_type_id,
        product_id=None,
        description="Общепроизводственные работы",
        hours=7.5,
        remember=False,
    )

    service.sync(source_path)
    imported_row = service.list_rows()[0]

    assert worklogs.get(worklog_id) is not None
    assert imported_row.status == STATUS_IMPORTED
    assert imported_row.object_id == manual_object_id
    assert imported_row.work_type_id == manual_work_type_id


def test_only_manually_corrected_workbot_values_become_aliases(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    service, _worklogs, employee_id, location_id, _object_id, _work_type_id, _product_id = (
        _create_prolog(tmp_path)
    )
    service.sync(source_path)
    row = service.list_rows()[0]
    corrected_object_id = service.directories.ensure("objects", "Исправленный объект")
    corrected_work_type_id = service.directories.ensure(
        "work_types", "Исправленный вид работ"
    )

    service.import_row(
        row.id,
        employee_id=employee_id,
        work_date=row.work_date,
        location_id=location_id,
        object_id=corrected_object_id,
        work_type_id=corrected_work_type_id,
        product_id=None,
        description="Исправленный вид работ",
        hours=7.5,
        remember=True,
    )

    aliases = service.directories.list_aliases()
    assert {(alias.alias_type, alias.target_id) for alias in aliases} == {
        ("object", corrected_object_id),
        ("work_type", corrected_work_type_id),
    }


def test_long_work_description_is_not_saved_as_work_type_alias(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    long_description = (
        "Прокладка сигнальных проводов, подключение шкафов, установка контакторов "
        "и проверка оборудования после завершения монтажных работ"
    )
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "UPDATE reports SET work_types = ? WHERE source_message_id = 'message-1'",
            (long_description,),
        )
    service, _worklogs, employee_id, location_id, object_id, work_type_id, product_id = (
        _create_prolog(tmp_path)
    )
    service.sync(source_path)
    row = service.list_rows()[0]

    service.import_row(
        row.id,
        employee_id=employee_id,
        work_date=row.work_date,
        location_id=location_id,
        object_id=object_id,
        work_type_id=work_type_id,
        product_id=product_id,
        description=long_description,
        hours=7.5,
        remember=True,
    )

    assert not any(
        alias.alias_type == "work_type" for alias in service.directories.list_aliases()
    )


def test_deleting_imported_worklog_reopens_workbot_row(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    service, worklogs, employee_id, location_id, object_id, work_type_id, product_id = (
        _create_prolog(tmp_path)
    )
    service.sync(source_path)
    row = service.list_rows()[0]
    worklog_id = service.import_row(
        row.id,
        employee_id=employee_id,
        work_date=row.work_date,
        location_id=location_id,
        object_id=object_id,
        work_type_id=work_type_id,
        product_id=product_id,
        description="Монтаж",
        hours=7.5,
        remember=False,
    )

    worklogs.delete(worklog_id)
    reopened = service.list_rows()[0]

    assert worklogs.get(worklog_id) is None
    assert reopened.status == STATUS_READY
    assert reopened.worklog_entry_id is None
    assert "повторно" in reopened.error_message


def test_workbot_sync_is_idempotent_and_preserves_revisions(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    service, worklogs, employee_id, location_id, object_id, work_type_id, product_id = _create_prolog(tmp_path)

    first = service.sync(source_path)
    second = service.sync(source_path)
    assert first.added_rows == 1
    assert second.added_rows == 0
    assert second.unchanged_messages == 1

    row = service.list_rows()[0]
    assert row.status == STATUS_READY
    assert row.hours == 7.5
    assert row.product_id == product_id
    worklog_id = service.import_row(
        row.id,
        employee_id=employee_id,
        work_date=date(2026, 7, 30),
        location_id=location_id,
        object_id=object_id,
        work_type_id=work_type_id,
        product_id=product_id,
        product_alias_text="ШУ-12",
        description="Монтаж",
        hours=7.5,
        remember=True,
    )
    imported = worklogs.get(worklog_id)
    assert imported.hours == 7.5
    assert imported.product_id == product_id
    assert service.directories.list_aliases() == []
    with pytest.raises(ValueError, match="уже импортирован"):
        service.import_row(
            row.id,
            employee_id=employee_id,
            work_date=date(2026, 7, 30),
            location_id=location_id,
            object_id=object_id,
            work_type_id=work_type_id,
            product_id=product_id,
            product_alias_text="ШУ-12",
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


def test_max_user_can_be_bound_before_report_sync(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE users SET employee_name = 'Василий' WHERE max_user_id = 100")
        connection.execute("UPDATE reports SET employee_name = 'Василий' WHERE sender_id = 100")
    service, _worklogs, employee_id, *_rest = _create_prolog(tmp_path)
    first = service.sync(source_path)
    assert first.added_rows == 1
    assert service.list_rows()[0].status == STATUS_NEEDS_EMPLOYEE
    links = service.list_user_links(source_path)
    assert len(links) == 1
    assert links[0].employee_id is None
    assert not links[0].binding_saved
    service.save_user_links(
        [
            WorkBotUserLink(
                max_user_id=links[0].max_user_id,
                profile_name=links[0].profile_name,
                employee_id=employee_id,
            )
        ]
    )
    rematched = service.sync(source_path)
    saved = service.list_user_links(source_path)[0]
    assert saved.employee_id == employee_id
    assert saved.binding_saved
    assert rematched.added_rows == 0
    assert rematched.unchanged_messages == 1
    assert service.list_rows()[0].status == STATUS_READY


def test_verified_phone_is_not_used_for_automatic_binding(tmp_path: Path) -> None:
    source_path = tmp_path / "workbot.sqlite3"
    _create_workbot_source(source_path)
    with sqlite3.connect(source_path) as connection:
        connection.execute(
            "ALTER TABLE users ADD COLUMN verified_phone TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "ALTER TABLE users ADD COLUMN phone_verified_at TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            """
            UPDATE users
            SET employee_name = 'Василий', verified_phone = '+79991234567',
                phone_verified_at = '2026-08-03T10:00:00'
            WHERE max_user_id = 100
            """
        )
        connection.execute(
            "UPDATE reports SET employee_name = 'Василий' WHERE sender_id = 100"
        )
    service, _worklogs, employee_id, *_rest = _create_prolog(tmp_path)

    result = service.sync(source_path)

    assert result.added_rows == 1
    assert service.repository.employee_binding(100) is None
    link = service.list_user_links(source_path)[0]
    assert not link.binding_saved
    assert link.employee_id is None
    assert service.list_rows()[0].status == STATUS_NEEDS_EMPLOYEE


def _create_prolog(tmp_path: Path):
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories = DirectoryService(DirectoryRepository(database))
    employees = EmployeeService(EmployeeRepository(database), directories)
    worklogs = WorkLogService(WorkLogRepository(database), directories)
    employee_id = employees.save(
        Employee(
            "Иванов Иван Иванович",
            "Слесарь",
            "1",
            mobile_phone="+7 999 123-45-67",
        )
    )
    location_id = directories.ensure("locations", "Производство")
    object_id = directories.ensure("objects", "Жигалово")
    work_type_id = directories.ensure("work_types", "Монтаж")
    product_id = directories.save_product(
        ProductItem(
            object_id=object_id,
            object_name="Жигалово",
            name="Шкаф управления",
            serial_number="ЗН-001",
            code="ШУ-12",
        )
    )
    service = WorkBotIntegrationService(
        WorkBotRepository(database),
        employees,
        directories,
        worklogs,
    )
    return service, worklogs, employee_id, location_id, object_id, work_type_id, product_id


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
                'message-1', 10, 100, '2026-07-30T18:00:00', 'Монтаж ШУ-12',
                'parsed', ''
            );
            INSERT INTO reports VALUES (
                1, 'message-1', 100, 'Иванов Иван Иванович', '2026-07-30',
                'Монтаж', 7.5, 'Жигалово', 'Производство', '2026-07-30T18:00:00'
            );
            """
        )
