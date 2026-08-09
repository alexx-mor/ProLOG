"""Cross-database reference diagnostics for attached ProLOG components."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReferenceIntegrityIssue:
    code: str
    message: str
    count: int
    sample_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DatabaseIntegrityReport:
    issues: tuple[ReferenceIntegrityIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues


class CrossDatabaseIntegrityError(RuntimeError):
    def __init__(self, report: DatabaseIntegrityReport) -> None:
        self.report = report
        details = "; ".join(
            f"{issue.message}: {issue.count}"
            for issue in report.issues
        )
        super().__init__(f"Нарушена целостность связанных баз ProLOG. {details}")


def check_cross_database_references(
    connection: sqlite3.Connection,
) -> DatabaseIntegrityReport:
    checks = [
        (
            "worklog_employee",
            "Записи работ ссылаются на отсутствующих сотрудников",
            """
            SELECT w.id
            FROM WorkLogEntries w
            LEFT JOIN employees_db.Employees e ON e.id = w.employee_id
            WHERE e.id IS NULL
            """,
        ),
        (
            "worklog_object",
            "Записи работ ссылаются на отсутствующие объекты",
            """
            SELECT w.id
            FROM WorkLogEntries w
            LEFT JOIN objects_db.Objects o ON o.id = w.object_id
            WHERE w.object_id IS NOT NULL AND o.id IS NULL
            """,
        ),
        (
            "worklog_product",
            "Записи работ ссылаются на отсутствующие изделия",
            """
            SELECT w.id
            FROM WorkLogEntries w
            LEFT JOIN products_db.Products p ON p.id = w.product_id
            WHERE w.product_id IS NOT NULL AND p.id IS NULL
            """,
        ),
        (
            "worklog_product_object",
            "Изделие записи работ относится к другому объекту",
            """
            SELECT w.id
            FROM WorkLogEntries w
            JOIN products_db.Products p ON p.id = w.product_id
            WHERE w.object_id IS NOT NULL AND w.object_id <> p.object_id
            """,
        ),
        (
            "product_object",
            "Изделия ссылаются на отсутствующие объекты",
            """
            SELECT p.id
            FROM products_db.Products p
            LEFT JOIN objects_db.Objects o ON o.id = p.object_id
            WHERE o.id IS NULL
            """,
        ),
        (
            "max_binding_employee",
            "Привязки MAX ссылаются на отсутствующих сотрудников",
            """
            SELECT b.max_user_id
            FROM MaxUserBindings b
            LEFT JOIN employees_db.Employees e ON e.id = b.employee_id
            WHERE e.id IS NULL
            """,
        ),
        (
            "workbot_employee",
            "Входящие строки WorkBot ссылаются на отсутствующих сотрудников",
            """
            SELECT r.id
            FROM WorkBotImportRows r
            LEFT JOIN employees_db.Employees e ON e.id = r.employee_id
            WHERE r.employee_id IS NOT NULL AND e.id IS NULL
            """,
        ),
        (
            "workbot_object",
            "Входящие строки WorkBot ссылаются на отсутствующие объекты",
            """
            SELECT r.id
            FROM WorkBotImportRows r
            LEFT JOIN objects_db.Objects o ON o.id = r.object_id
            WHERE r.object_id IS NOT NULL AND o.id IS NULL
            """,
        ),
        (
            "workbot_product",
            "Входящие строки WorkBot ссылаются на отсутствующие изделия",
            """
            SELECT r.id
            FROM WorkBotImportRows r
            LEFT JOIN products_db.Products p ON p.id = r.product_id
            WHERE r.product_id IS NOT NULL AND p.id IS NULL
            """,
        ),
        (
            "workbot_worklog",
            "Входящие строки WorkBot ссылаются на отсутствующие записи работ",
            """
            SELECT r.id
            FROM WorkBotImportRows r
            LEFT JOIN WorkLogEntries w ON w.id = r.worklog_entry_id
            WHERE r.worklog_entry_id IS NOT NULL AND w.id IS NULL
            """,
        ),
        (
            "employee_alias",
            "Алиасы сотрудников ссылаются на отсутствующих сотрудников",
            """
            SELECT a.rowid
            FROM aliases_db.EmployeeAliases a
            LEFT JOIN employees_db.Employees e ON e.id = a.employee_id
            WHERE e.id IS NULL
            """,
        ),
        (
            "object_alias",
            "Алиасы объектов ссылаются на отсутствующие объекты",
            """
            SELECT a.rowid
            FROM aliases_db.ObjectAliases a
            LEFT JOIN objects_db.Objects o ON o.id = a.object_id
            WHERE o.id IS NULL
            """,
        ),
        (
            "product_alias",
            "Алиасы изделий ссылаются на отсутствующие изделия",
            """
            SELECT a.rowid
            FROM aliases_db.ProductAliases a
            LEFT JOIN products_db.Products p ON p.id = a.product_id
            WHERE p.id IS NULL
            """,
        ),
        (
            "location_alias",
            "Алиасы местонахождений ссылаются на отсутствующие позиции",
            """
            SELECT a.rowid
            FROM aliases_db.LocationAliases a
            LEFT JOIN Locations l ON l.id = a.location_id
            WHERE l.id IS NULL
            """,
        ),
        (
            "work_type_alias",
            "Алиасы видов работ ссылаются на отсутствующие позиции",
            """
            SELECT a.rowid
            FROM aliases_db.WorkTypeAliases a
            LEFT JOIN WorkTypes w ON w.id = a.work_type_id
            WHERE w.id IS NULL
            """,
        ),
    ]
    production_events_exist = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'ProductionEvents'
        """
    ).fetchone()
    if production_events_exist is not None:
        checks.extend(
            (
                (
                    "production_event_product",
                    "Производственные события ссылаются на отсутствующие изделия",
                    """
                    SELECT event.id
                    FROM ProductionEvents event
                    LEFT JOIN products_db.Products product ON product.id = event.product_id
                    WHERE event.product_id IS NOT NULL AND product.id IS NULL
                    """,
                ),
                (
                    "production_event_object_snapshot",
                    "Снимки производственных событий ссылаются на отсутствующие объекты",
                    """
                    SELECT event.id
                    FROM ProductionEvents event
                    LEFT JOIN objects_db.Objects object
                        ON object.id = event.object_id_snapshot
                    WHERE event.object_id_snapshot IS NOT NULL AND object.id IS NULL
                    """,
                ),
                (
                    "production_event_employee",
                    "Производственные события ссылаются на отсутствующих сотрудников",
                    """
                    SELECT event.id
                    FROM ProductionEvents event
                    LEFT JOIN employees_db.Employees employee
                        ON employee.id = event.reported_by_employee_id
                    WHERE event.reported_by_employee_id IS NOT NULL
                      AND employee.id IS NULL
                    """,
                ),
                (
                    "production_event_stage",
                    "Производственные события ссылаются на отсутствующие этапы",
                    """
                    SELECT event.id
                    FROM ProductionEvents event
                    LEFT JOIN ProductionStages stage ON stage.id = event.stage_id
                    WHERE event.stage_id IS NOT NULL AND stage.id IS NULL
                    """,
                ),
                (
                    "production_event_supersedes",
                    "Корректировки ссылаются на отсутствующие исходные события",
                    """
                    SELECT event.id
                    FROM ProductionEvents event
                    LEFT JOIN ProductionEvents source
                        ON source.id = event.supersedes_event_id
                    WHERE event.supersedes_event_id IS NOT NULL AND source.id IS NULL
                    """,
                ),
                (
                    "production_attachment_event",
                    "Связи Attachment ссылаются на отсутствующие production event",
                    """
                    SELECT relation.production_event_id
                    FROM ProductionEventAttachments relation
                    LEFT JOIN ProductionEvents event
                        ON event.id = relation.production_event_id
                    WHERE event.id IS NULL
                    """,
                ),
                (
                    "production_event_attachment",
                    "Связи production event ссылаются на отсутствующие Attachment",
                    """
                    SELECT relation.production_event_id
                    FROM ProductionEventAttachments relation
                    LEFT JOIN Attachments attachment
                        ON attachment.id = relation.attachment_id
                    WHERE attachment.id IS NULL
                    """,
                ),
                (
                    "production_worklog_event",
                    "Связи WorkLog ссылаются на отсутствующие production event",
                    """
                    SELECT relation.production_event_id
                    FROM ProductionEventWorkLogs relation
                    LEFT JOIN ProductionEvents event
                        ON event.id = relation.production_event_id
                    WHERE event.id IS NULL
                    """,
                ),
                (
                    "production_event_worklog",
                    "Связи production event ссылаются на отсутствующие записи работ",
                    """
                    SELECT relation.production_event_id
                    FROM ProductionEventWorkLogs relation
                    LEFT JOIN WorkLogEntries worklog
                        ON worklog.id = relation.worklog_entry_id
                    WHERE worklog.id IS NULL
                    """,
                ),
            )
        )
    issues: list[ReferenceIntegrityIssue] = []
    for code, message, query in checks:
        rows = connection.execute(query).fetchall()
        if not rows:
            continue
        issues.append(
            ReferenceIntegrityIssue(
                code=code,
                message=message,
                count=len(rows),
                sample_ids=tuple(int(row[0]) for row in rows[:10]),
            )
        )
    return DatabaseIntegrityReport(tuple(issues))
