"""Audit storage for legacy imports."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from database import Database
from legacy_import.models import LegacyImportPreview, ResolvedLegacyRow

CHUNK_SIZE = 1024 * 1024


class LegacyImportAuditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def file_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def find_completed_batch(self, file_hash: str) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM ImportBatches
                WHERE source_file_hash = ? AND status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (file_hash,),
            ).fetchone()
        return int(row["id"]) if row else None

    def create_batch(self, preview: LegacyImportPreview) -> int:
        dates = [row.source.work_date for row in preview.rows]
        period_from = min(dates).isoformat() if dates else ""
        period_to = max(dates).isoformat() if dates else ""
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ImportBatches (
                    source_type, source_file_name, source_file_hash, period_from,
                    period_to, imported_at, status, total_rows, imported_rows,
                    skipped_rows, error_rows, message
                )
                VALUES ('legacy_excel', ?, ?, ?, ?, ?, 'started', ?, 0, 0, ?, '')
                """,
                (
                    preview.source_file.name,
                    preview.file_hash,
                    period_from,
                    period_to,
                    datetime.now().isoformat(timespec="seconds"),
                    preview.total_rows,
                    preview.error_count,
                ),
            )
            return int(cursor.lastrowid)

    def complete_batch(
        self,
        batch_id: int,
        imported_count: int,
        skipped_count: int,
        error_count: int,
        message: str = "",
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ImportBatches
                SET status = 'completed',
                    imported_rows = ?,
                    skipped_rows = ?,
                    error_rows = ?,
                    message = ?
                WHERE id = ?
                """,
                (imported_count, skipped_count, error_count, message, batch_id),
            )

    def fail_batch(self, batch_id: int, message: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ImportBatches
                SET status = 'failed', message = ?
                WHERE id = ?
                """,
                (message, batch_id),
            )

    def add_row(
        self,
        batch_id: int,
        row: ResolvedLegacyRow,
        status: str,
        message: str,
        worklog_entry_id: int | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ImportRows (
                    batch_id, sheet_name, excel_row, work_date, employee_text,
                    status, message, worklog_entry_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    row.source.sheet_name,
                    row.source.row_number,
                    row.source.work_date.isoformat(),
                    row.source.employee_text,
                    status,
                    message,
                    worklog_entry_id,
                ),
            )
