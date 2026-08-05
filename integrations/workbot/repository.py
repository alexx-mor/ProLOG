"""Persistence for WorkBot bindings, aliases and the review inbox."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

from database import Database
from integrations.workbot.models import (
    STATUS_CHANGED,
    STATUS_IMPORTED,
    WorkBotCandidate,
    WorkBotInboxRow,
    WorkBotSyncResult,
)
from models import WorkLogEntry


class WorkBotRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def sync(self, candidates: list[WorkBotCandidate]) -> WorkBotSyncResult:
        grouped: dict[str, list[WorkBotCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate.max_message_id].append(candidate)
        added = 0
        unchanged = 0
        revised = 0
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            for message_id, rows in grouped.items():
                previous = connection.execute(
                    """
                    SELECT revision, content_hash
                    FROM WorkBotImportRows
                    WHERE max_message_id = ?
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    (message_id,),
                ).fetchone()
                if previous and previous["content_hash"] == rows[0].content_hash:
                    for row in rows:
                        connection.execute(
                            """
                            UPDATE WorkBotImportRows
                            SET employee_id = ?, object_id = ?, location_id = ?, work_type_id = ?,
                                product_id = ?, product_text = ?,
                                status = CASE
                                    WHEN status IN ('imported', 'rejected', 'changed_after_import') THEN status
                                    ELSE ?
                                END,
                                error_message = CASE
                                    WHEN status IN ('imported', 'rejected', 'changed_after_import') THEN error_message
                                    ELSE ?
                                END,
                                updated_at = ?
                            WHERE max_message_id = ? AND revision = ?
                              AND source_index = ? AND source_kind = ?
                              AND status NOT IN ('imported', 'rejected', 'changed_after_import')
                            """,
                            (
                                row.employee_id,
                                row.object_id,
                                row.location_id,
                                row.work_type_id,
                                row.product_id,
                                row.product_text,
                                row.status,
                                row.error_message,
                                now,
                                message_id,
                                int(previous["revision"]),
                                row.source_index,
                                row.source_kind,
                            ),
                        )
                    unchanged += 1
                    continue
                revision = int(previous["revision"]) + 1 if previous else 1
                was_imported = bool(
                    connection.execute(
                        """
                        SELECT 1 FROM WorkBotImportRows
                        WHERE max_message_id = ? AND status = ?
                        LIMIT 1
                        """,
                        (message_id, STATUS_IMPORTED),
                    ).fetchone()
                )
                if previous:
                    revised += 1
                for row in rows:
                    status = STATUS_CHANGED if was_imported else row.status
                    connection.execute(
                        INSERT_ROW_SQL,
                        (
                            row.max_message_id,
                            revision,
                            row.source_index,
                            row.source_kind,
                            row.sender_id,
                            row.chat_id,
                            row.received_at,
                            row.content_hash,
                            row.raw_text,
                            row.source_fragment,
                            row.employee_text,
                            row.work_date.isoformat(),
                            row.work_types,
                            row.hours,
                            row.object_text,
                            row.location_text,
                            row.product_text,
                            row.confidence,
                            row.employee_id,
                            row.object_id,
                            row.location_id,
                            row.work_type_id,
                            row.product_id,
                            status,
                            row.error_message,
                            now,
                            now,
                        ),
                    )
                    added += 1
        return WorkBotSyncResult(len(candidates), added, unchanged, revised)

    def list_rows(self, status: str = "") -> list[WorkBotInboxRow]:
        sql = """
            SELECT r.*
            FROM WorkBotImportRows r
            JOIN (
                SELECT max_message_id, MAX(revision) AS revision
                FROM WorkBotImportRows
                GROUP BY max_message_id
            ) latest
              ON latest.max_message_id = r.max_message_id
             AND latest.revision = r.revision
        """
        params: tuple[object, ...] = ()
        if status:
            sql += " WHERE r.status = ?"
            params = (status,)
        sql += " ORDER BY r.work_date DESC, r.received_at DESC, r.source_index"
        with self.database.connect() as connection:
            return [self._map(row) for row in connection.execute(sql, params)]

    def inbox_counts(self) -> tuple[int, int, int]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                WITH latest AS (
                    SELECT max_message_id, MAX(revision) AS revision
                    FROM WorkBotImportRows
                    GROUP BY max_message_id
                )
                SELECT
                    COUNT(*) AS total_rows,
                    COALESCE(SUM(r.status = 'imported'), 0) AS imported_rows,
                    COALESCE(SUM(r.status NOT IN ('ready', 'imported', 'rejected')), 0) AS error_rows
                FROM WorkBotImportRows r
                JOIN latest
                  ON latest.max_message_id = r.max_message_id
                 AND latest.revision = r.revision
                """
            ).fetchone()
        return int(row["total_rows"]), int(row["imported_rows"]), int(row["error_rows"])

    def get(self, row_id: int) -> WorkBotInboxRow | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM WorkBotImportRows WHERE id = ?",
                (row_id,),
            ).fetchone()
            return self._map(row) if row else None

    def employee_binding(self, sender_id: int) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT employee_id FROM MaxUserBindings WHERE max_user_id = ? AND is_active = 1",
                (sender_id,),
            ).fetchone()
            return int(row["employee_id"]) if row else None

    def employee_bindings(self) -> dict[int, int]:
        with self.database.connect() as connection:
            return {
                int(row["max_user_id"]): int(row["employee_id"])
                for row in connection.execute(
                    "SELECT max_user_id, employee_id FROM MaxUserBindings WHERE is_active = 1"
                )
            }

    def save_employee_binding(
        self,
        max_user_id: int,
        employee_id: int | None,
        username_snapshot: str = "",
    ) -> None:
        now = _now()
        with self.database.connect() as connection:
            if employee_id is None:
                connection.execute(
                    "UPDATE MaxUserBindings SET is_active = 0, updated_at = ? WHERE max_user_id = ?",
                    (now, max_user_id),
                )
                return
            connection.execute(
                """
                UPDATE MaxUserBindings
                SET is_active = 0, updated_at = ?
                WHERE employee_id = ? AND max_user_id <> ?
                """,
                (now, employee_id, max_user_id),
            )
            connection.execute(
                """
                INSERT INTO MaxUserBindings(
                    max_user_id, employee_id, username_snapshot, is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(max_user_id) DO UPDATE SET
                    employee_id = excluded.employee_id,
                    username_snapshot = excluded.username_snapshot,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (max_user_id, employee_id, username_snapshot.strip(), now, now),
            )

    def alias_target(self, alias_table: str, value: str) -> int | None:
        table, target = ALIAS_TABLES[alias_table]
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {target} FROM {table} WHERE alias_normalized = ?",
                (value,),
            ).fetchone()
            return int(row[target]) if row else None

    def alias_targets(self, alias_table: str) -> dict[str, int]:
        table, target = ALIAS_TABLES[alias_table]
        with self.database.connect() as connection:
            return {
                str(row["alias_normalized"]): int(row[target])
                for row in connection.execute(f"SELECT alias_normalized, {target} FROM {table}")
            }

    def reject(self, row_id: int, reason: str, reviewer: str = "") -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE WorkBotImportRows
                SET status = 'rejected', error_message = ?, reviewed_by = ?,
                    reviewed_at = ?, updated_at = ?
                WHERE id = ? AND status <> 'imported'
                """,
                (reason.strip(), reviewer.strip(), _now(), _now(), row_id),
            )

    def import_entry(
        self,
        row_id: int,
        entry: WorkLogEntry,
        remember_aliases: bool,
        remember_sender: bool,
        product_alias_text: str = "",
        reviewer: str = "",
    ) -> int:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM WorkBotImportRows WHERE id = ?",
                (row_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Входящий отчет не найден")
            if row["status"] == STATUS_IMPORTED:
                raise ValueError("Этот отчет уже импортирован")
            cursor = connection.execute(
                """
                INSERT INTO WorkLogEntries (
                    employee_id, work_date, location_id, object_id, product_id, work_type_id,
                    description, hours, comment, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.employee_id,
                    entry.work_date.isoformat(),
                    entry.location_id,
                    entry.object_id,
                    entry.product_id,
                    entry.work_type_id,
                    entry.description.strip(),
                    entry.hours,
                    entry.comment.strip(),
                    now,
                    now,
                ),
            )
            worklog_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE WorkBotImportRows
                SET employee_id = ?, object_id = ?, location_id = ?, work_type_id = ?, product_id = ?,
                    product_text = ?,
                    work_date = ?, work_types = ?, hours = ?, status = 'imported',
                    error_message = '', worklog_entry_id = ?, imported_at = ?,
                    reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    entry.employee_id,
                    entry.object_id,
                    entry.location_id,
                    entry.work_type_id,
                    entry.product_id,
                    product_alias_text.strip(),
                    entry.work_date.isoformat(),
                    entry.description.strip(),
                    entry.hours,
                    worklog_id,
                    now,
                    reviewer.strip(),
                    now,
                    now,
                    row_id,
                ),
            )
            if remember_sender and row["source_kind"] == "strict":
                connection.execute(
                    """
                    INSERT INTO MaxUserBindings(max_user_id, employee_id, is_active, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(max_user_id) DO UPDATE SET
                        employee_id = excluded.employee_id,
                        is_active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (row["sender_id"], entry.employee_id, now, now),
                )
            if remember_aliases:
                self._remember_alias(connection, "aliases_db.EmployeeAliases", "employee_id", row["employee_text"], entry.employee_id, now)
                self._remember_alias(connection, "aliases_db.ObjectAliases", "object_id", row["object_text"], entry.object_id, now)
                self._remember_alias(connection, "aliases_db.LocationAliases", "location_id", row["location_text"], entry.location_id, now)
                self._remember_alias(
                    connection,
                    "aliases_db.WorkTypeAliases",
                    "work_type_id",
                    row["work_types"],
                    entry.work_type_id,
                    now,
                )
                self._remember_alias(
                    connection,
                    "aliases_db.ProductAliases",
                    "product_id",
                    product_alias_text or row["product_text"],
                    entry.product_id,
                    now,
                )
            return worklog_id

    def _remember_alias(
        self,
        connection: sqlite3.Connection,
        table: str,
        target_column: str,
        original: str,
        target_id: int | None,
        now: str,
    ) -> None:
        normalized = normalize_alias(original)
        if not normalized or target_id is None:
            return
        connection.execute(
            f"""
            INSERT INTO {table}(alias_normalized, original_alias, {target_column}, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias_normalized) DO UPDATE SET
                original_alias = excluded.original_alias,
                {target_column} = excluded.{target_column},
                updated_at = excluded.updated_at
            """,
            (normalized, original.strip(), target_id, now, now),
        )

    def _map(self, row: sqlite3.Row) -> WorkBotInboxRow:
        return WorkBotInboxRow(
            id=int(row["id"]),
            max_message_id=str(row["max_message_id"]),
            revision=int(row["revision"]),
            source_index=int(row["source_index"]),
            source_kind=str(row["source_kind"]),
            sender_id=int(row["sender_id"]),
            chat_id=row["chat_id"],
            received_at=str(row["received_at"] or ""),
            raw_text=str(row["raw_text"] or ""),
            source_fragment=str(row["source_fragment"] or ""),
            employee_text=str(row["employee_text"] or ""),
            work_date=datetime.strptime(str(row["work_date"]), "%Y-%m-%d").date(),
            work_types=str(row["work_types"] or ""),
            hours=float(row["hours"] or 0),
            object_text=str(row["object_text"] or ""),
            location_text=str(row["location_text"] or ""),
            product_text=str(row["product_text"] or ""),
            confidence=float(row["confidence"] or 0),
            employee_id=row["employee_id"],
            object_id=row["object_id"],
            location_id=row["location_id"],
            work_type_id=row["work_type_id"],
            product_id=row["product_id"],
            status=str(row["status"]),
            error_message=str(row["error_message"] or ""),
            worklog_entry_id=row["worklog_entry_id"],
        )


def normalize_alias(value: str) -> str:
    return " ".join(value.replace("ё", "е").replace("Ё", "Е").casefold().split())


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


ALIAS_TABLES = {
    "employee": ("aliases_db.EmployeeAliases", "employee_id"),
    "object": ("aliases_db.ObjectAliases", "object_id"),
    "location": ("aliases_db.LocationAliases", "location_id"),
    "work_type": ("aliases_db.WorkTypeAliases", "work_type_id"),
    "product": ("aliases_db.ProductAliases", "product_id"),
}

INSERT_ROW_SQL = """
INSERT INTO WorkBotImportRows (
    max_message_id, revision, source_index, source_kind, sender_id, chat_id,
    received_at, content_hash, raw_text, source_fragment, employee_text,
    work_date, work_types, hours, object_text, location_text, product_text, confidence,
    employee_id, object_id, location_id, work_type_id, product_id, status, error_message,
    created_at, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
