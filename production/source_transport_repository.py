"""SQLite repository for P8 production source configuration and snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from database import Database
from production.source_transport_models import (
    InboxChangeKind,
    InboxSourceType,
    ProductionInboxMessageSnapshot,
    ProductionInboxSource,
    SourceRevisionSnapshot,
    SourceRevisionFailure,
    SourceSyncCursor,
    SourceTombstoneSnapshot,
)


class ProductionSourceTransportError(RuntimeError):
    pass


class SourceRevisionConflictError(ProductionSourceTransportError):
    pass


class ProductionSourceTransportRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_source(self, source: ProductionInboxSource) -> ProductionInboxSource:
        now = _utc_now()
        source_ref = source.source_ref.strip()
        if not source_ref and source.source_type is InboxSourceType.MAX_CHAT and source.chat_id is not None:
            source_ref = f"max_chat:{source.chat_id}"
        with self.database.connect() as connection:
            existing = None
            if source.chat_id is not None:
                existing = connection.execute(
                    """
                    SELECT * FROM ProductionInboxSources
                    WHERE source_type = ? AND chat_id = ?
                    """,
                    (source.source_type.value, source.chat_id),
                ).fetchone()
            if existing is None and source_ref:
                existing = connection.execute(
                    """
                    SELECT * FROM ProductionInboxSources
                    WHERE source_type = ? AND source_ref = ?
                    """,
                    (source.source_type.value, source_ref),
                ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE ProductionInboxSources
                    SET display_name = ?, chat_id = ?, enabled = ?, web_url = ?,
                        source_ref = ?, updated_at_utc = ?
                    WHERE id = ?
                    """,
                    (
                        source.display_name.strip(),
                        source.chat_id,
                        int(source.enabled),
                        source.web_url.strip(),
                        source_ref,
                        _iso(now),
                        int(existing["id"]),
                    ),
                )
                row_id = int(existing["id"])
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO ProductionInboxSources (
                        uid, source_type, source_ref, display_name, chat_id,
                        enabled, web_url, created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(source.uid),
                        source.source_type.value,
                        source_ref,
                        source.display_name.strip(),
                        source.chat_id,
                        int(source.enabled),
                        source.web_url.strip(),
                        _iso(source.created_at_utc or now),
                        _iso(source.updated_at_utc or now),
                    ),
                )
                row_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT OR IGNORE INTO ProductionInboxSyncState(source_id, updated_at_utc)
                VALUES (?, ?)
                """,
                (row_id, _iso(now)),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO ProductionInboxTombstoneSyncState(
                    source_id, updated_at_utc
                ) VALUES (?, ?)
                """,
                (row_id, _iso(now)),
            )
            row = connection.execute(
                "SELECT * FROM ProductionInboxSources WHERE id = ?",
                (row_id,),
            ).fetchone()
        return _source_from_row(row)

    def get_source(self, source_id: int) -> ProductionInboxSource | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ProductionInboxSources WHERE id = ?",
                (source_id,),
            ).fetchone()
        return _source_from_row(row) if row is not None else None

    def list_sources(self, *, enabled_only: bool = False) -> list[ProductionInboxSource]:
        query = "SELECT * FROM ProductionInboxSources"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY display_name COLLATE NOCASE, id"
        with self.database.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [_source_from_row(row) for row in rows]

    def set_source_enabled(self, source_id: int, enabled: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ProductionInboxSources
                SET enabled = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (int(enabled), _iso(_utc_now()), source_id),
            )

    def cursor(self, source_id: int) -> SourceSyncCursor:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ProductionInboxSyncState WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return SourceSyncCursor()
        return SourceSyncCursor(
            int(row["cursor_revision_id"]),
            str(row["cursor_message_id"]),
            int(row["cursor_revision_number"]),
            str(row["cursor_content_hash"]),
        )

    def advance_cursor(
        self,
        source_id: int,
        revision: SourceRevisionSnapshot | SourceRevisionFailure,
        *,
        success: bool,
    ) -> None:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ProductionInboxSyncState (
                    source_id, cursor_revision_id, cursor_message_id,
                    cursor_revision_number, cursor_content_hash,
                    last_sync_at_utc, last_success_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    cursor_revision_id = excluded.cursor_revision_id,
                    cursor_message_id = excluded.cursor_message_id,
                    cursor_revision_number = excluded.cursor_revision_number,
                    cursor_content_hash = excluded.cursor_content_hash,
                    last_sync_at_utc = excluded.last_sync_at_utc,
                    last_success_at_utc = CASE
                        WHEN ? THEN excluded.last_success_at_utc
                        ELSE ProductionInboxSyncState.last_success_at_utc
                    END,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    source_id,
                    revision.revision_id,
                    revision.source_message_id,
                    revision.revision_number,
                    revision.content_hash,
                    now,
                    now if success else None,
                    now,
                    int(success),
                ),
            )

    def reset_cursor(self, source_id: int) -> None:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ProductionInboxSyncState
                SET cursor_revision_id = 0, cursor_message_id = '',
                    cursor_revision_number = 0, cursor_content_hash = '',
                    updated_at_utc = ?
                WHERE source_id = ?
                """,
                (now, source_id),
            )

    def tombstone_cursor(self, source_id: int) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT cursor_tombstone_id
                FROM ProductionInboxTombstoneSyncState WHERE source_id = ?
                """,
                (source_id,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def import_tombstone(
        self,
        source_id: int,
        tombstone: SourceTombstoneSnapshot,
    ) -> bool:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT raw_update_json FROM ProductionInboxSourceTombstones
                WHERE source_id = ? AND source_tombstone_id = ?
                """,
                (source_id, tombstone.tombstone_id),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != tombstone.raw_update_json:
                    raise SourceRevisionConflictError(
                        "Один source tombstone содержит различное содержимое"
                    )
                return False
            connection.execute(
                """
                INSERT INTO ProductionInboxSourceTombstones (
                    uid, source_id, source_tombstone_id, source_message_id,
                    chat_id, deleted_at_utc, raw_update_json, transported_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), source_id, tombstone.tombstone_id,
                    tombstone.source_message_id, tombstone.chat_id,
                    _iso(tombstone.deleted_at_utc), tombstone.raw_update_json, now,
                ),
            )
        return True

    def advance_tombstone_cursor(self, source_id: int, tombstone_id: int) -> None:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ProductionInboxTombstoneSyncState (
                    source_id, cursor_tombstone_id, last_sync_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    cursor_tombstone_id = excluded.cursor_tombstone_id,
                    last_sync_at_utc = excluded.last_sync_at_utc,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (source_id, tombstone_id, now, now),
            )

    def begin_run(self, source_id: int, cursor_before: int) -> int:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ProductionInboxSyncRuns (
                    uid, source_id, started_at_utc, cursor_before,
                    cursor_after, status
                ) VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (str(uuid4()), source_id, _iso(_utc_now()), cursor_before, cursor_before),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        result,
        *,
        error_summary: str = "",
        failed: bool = False,
    ) -> None:
        status = "failed" if failed else (
            "completed" if result.error_count == 0 else "partial"
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ProductionInboxSyncRuns
                SET completed_at_utc = ?, cursor_after = ?, status = ?,
                    read_count = ?, imported_count = ?, unchanged_count = ?,
                    changed_count = ?, skipped_count = ?, error_count = ?,
                    error_summary = ?
                WHERE id = ?
                """,
                (
                    _iso(_utc_now()),
                    result.cursor_after,
                    status,
                    result.read_count,
                    result.imported_count,
                    result.unchanged_count,
                    result.changed_count,
                    result.skipped_count,
                    result.error_count,
                    error_summary,
                    run_id,
                ),
            )

    def import_revision(
        self,
        source: ProductionInboxSource,
        revision: SourceRevisionSnapshot,
        run_id: int,
    ) -> tuple[ProductionInboxMessageSnapshot, bool]:
        if source.id is None:
            raise ValueError("source must be persistent")
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM ProductionInboxMessages
                WHERE source_id = ? AND source_message_id = ?
                  AND source_revision_number = ?
                """,
                (source.id, revision.source_message_id, revision.revision_number),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != revision.content_hash:
                    raise SourceRevisionConflictError(
                        "Одна source revision содержит различное содержимое"
                    )
                return _message_from_row(existing), False
            previous = connection.execute(
                """
                SELECT * FROM ProductionInboxMessages
                WHERE source_id = ? AND source_message_id = ?
                ORDER BY source_revision_number DESC, id DESC LIMIT 1
                """,
                (source.id, revision.source_message_id),
            ).fetchone()
            change_kind = (
                InboxChangeKind.CHANGED if previous is not None else InboxChangeKind.ORIGINAL
            )
            now = _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO ProductionInboxMessages (
                    uid, source_id, sync_run_id, source_type, source_ref,
                    source_message_id, source_revision_id, source_revision_number,
                    chat_id, sender_max_user_id, sender_display_snapshot,
                    message_timestamp_utc, edited_at_utc, source_received_at_utc,
                    transported_at_utc, source_sequence, source_text, content_hash,
                    source_content_json, raw_envelope_json, change_kind,
                    supersedes_inbox_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), source.id, run_id, source.source_type.value,
                    source.source_ref, revision.source_message_id,
                    revision.revision_id, revision.revision_number, revision.chat_id,
                    revision.sender_max_user_id, revision.sender_display_snapshot,
                    _iso(revision.message_timestamp_utc),
                    _iso(revision.edited_at_utc) if revision.edited_at_utc else None,
                    _iso(revision.received_at_utc), _iso(now), revision.source_sequence,
                    revision.source_text, revision.content_hash, revision.content_json,
                    revision.raw_envelope_json, change_kind.value,
                    int(previous["id"]) if previous is not None else None,
                ),
            )
            message_id = int(cursor.lastrowid)
            for attachment in revision.attachments:
                connection.execute(
                    """
                    INSERT INTO ProductionInboxAttachments (
                        uid, inbox_message_id, source_attachment_row_id,
                        source_attachment_id, identity_kind, source_order,
                        attachment_type, mime_type, original_name, source_size,
                        source_download_status, source_sha256, source_storage_key,
                        source_downloaded_at_utc, media_state, source_metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()), message_id, attachment.source_row_id,
                        attachment.source_attachment_id, attachment.identity_kind,
                        attachment.source_order, attachment.attachment_type,
                        attachment.mime_type, attachment.original_name,
                        attachment.source_size, attachment.download_status,
                        attachment.sha256, attachment.storage_key,
                        _iso(attachment.downloaded_at_utc)
                        if attachment.downloaded_at_utc else None,
                        attachment.media_state.value, attachment.source_metadata_json,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM ProductionInboxMessages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return _message_from_row(row), True

    def record_issue(
        self,
        source_id: int,
        revision_id: int,
        message_id: str,
        revision_number: int,
        issue_code: str,
        message: str,
        *,
        attachment_id: str = "",
    ) -> None:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ProductionInboxSyncIssues (
                    source_id, source_revision_id, source_message_id,
                    source_revision_number, source_attachment_id, issue_code,
                    message, first_seen_at_utc, last_seen_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_revision_id, source_attachment_id, issue_code)
                DO UPDATE SET
                    message = excluded.message,
                    attempts = ProductionInboxSyncIssues.attempts + 1,
                    last_seen_at_utc = excluded.last_seen_at_utc,
                    resolved_at_utc = NULL
                """,
                (
                    source_id, revision_id, message_id, revision_number,
                    attachment_id, issue_code, message, now, now,
                ),
            )

    def resolve_revision_issues(self, source_id: int, revision_id: int) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ProductionInboxSyncIssues
                SET resolved_at_utc = ?
                WHERE source_id = ? AND source_revision_id = ?
                  AND resolved_at_utc IS NULL
                """,
                (_iso(_utc_now()), source_id, revision_id),
            )

    def unresolved_revision_ids(self, source_id: int) -> tuple[int, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT source_revision_id
                FROM ProductionInboxSyncIssues
                WHERE source_id = ? AND resolved_at_utc IS NULL
                  AND source_revision_id > 0
                ORDER BY source_revision_id
                """,
                (source_id,),
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def list_messages(self, source_id: int | None = None):
        query = "SELECT * FROM ProductionInboxMessages"
        params: tuple = ()
        if source_id is not None:
            query += " WHERE source_id = ?"
            params = (source_id,)
        query += " ORDER BY source_revision_id, id"
        with self.database.connect() as connection:
            return connection.execute(query, params).fetchall()

    def list_attachments(self, inbox_message_id: int | None = None):
        query = "SELECT * FROM ProductionInboxAttachments"
        params: tuple = ()
        if inbox_message_id is not None:
            query += " WHERE inbox_message_id = ?"
            params = (inbox_message_id,)
        query += " ORDER BY inbox_message_id, source_order"
        with self.database.connect() as connection:
            return connection.execute(query, params).fetchall()

    def diagnostics_rows(self) -> dict[str, list]:
        with self.database.connect() as connection:
            return {
                "sources": connection.execute(
                    "SELECT * FROM ProductionInboxSources ORDER BY id"
                ).fetchall(),
                "messages_without_source": connection.execute(
                    """
                    SELECT message.id FROM ProductionInboxMessages message
                    LEFT JOIN ProductionInboxSources source ON source.id = message.source_id
                    WHERE source.id IS NULL
                    """
                ).fetchall(),
                "attachments_without_message": connection.execute(
                    """
                    SELECT attachment.id FROM ProductionInboxAttachments attachment
                    LEFT JOIN ProductionInboxMessages message
                      ON message.id = attachment.inbox_message_id
                    WHERE message.id IS NULL
                    """
                ).fetchall(),
                "unresolved_issues": connection.execute(
                    """
                    SELECT * FROM ProductionInboxSyncIssues
                    WHERE resolved_at_utc IS NULL ORDER BY source_id, source_revision_id
                    """
                ).fetchall(),
                "counts": connection.execute(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM ProductionInboxSources),
                      (SELECT COUNT(*) FROM ProductionInboxMessages),
                      (SELECT COUNT(*) FROM ProductionInboxAttachments)
                    """
                ).fetchone(),
            }


def _source_from_row(row) -> ProductionInboxSource:
    return ProductionInboxSource(
        id=int(row["id"]),
        uid=UUID(str(row["uid"])),
        source_type=InboxSourceType(str(row["source_type"])),
        source_ref=str(row["source_ref"]),
        display_name=str(row["display_name"]),
        chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
        enabled=bool(row["enabled"]),
        web_url=str(row["web_url"]),
        created_at_utc=_datetime(row["created_at_utc"]),
        updated_at_utc=_datetime(row["updated_at_utc"]),
    )


def _message_from_row(row) -> ProductionInboxMessageSnapshot:
    return ProductionInboxMessageSnapshot(
        id=int(row["id"]),
        uid=UUID(str(row["uid"])),
        source_id=int(row["source_id"]),
        source_message_id=str(row["source_message_id"]),
        source_revision_id=int(row["source_revision_id"]),
        source_revision_number=int(row["source_revision_number"]),
        content_hash=str(row["content_hash"]),
        change_kind=InboxChangeKind(str(row["change_kind"])),
        supersedes_inbox_message_id=(
            int(row["supersedes_inbox_message_id"])
            if row["supersedes_inbox_message_id"] is not None else None
        ),
    )


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
