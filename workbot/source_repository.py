"""SQLite repository for immutable MAX source revisions and media metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from workbot.source_models import (
    MediaDownloadStatus,
    SourceMediaAttachment,
    SourceRevisionInput,
    StoredSourceRevision,
)
from workbot.storage import WorkBotStorage


class WorkBotSourceRepository:
    def __init__(self, storage: WorkBotStorage) -> None:
        self.storage = storage

    def record_revision(self, source: SourceRevisionInput) -> StoredSourceRevision:
        _require_aware(source.message_timestamp_utc, "message_timestamp_utc")
        _require_aware(source.received_at_utc, "received_at_utc")
        if source.edited_at_utc is not None:
            _require_aware(source.edited_at_utc, "edited_at_utc")
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_messages (
                    source_message_id, chat_id, sender_max_user_id,
                    sender_display_snapshot, message_timestamp_utc,
                    source_sequence, first_received_at_utc, last_received_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_message_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    sender_max_user_id = excluded.sender_max_user_id,
                    sender_display_snapshot = excluded.sender_display_snapshot,
                    message_timestamp_utc = excluded.message_timestamp_utc,
                    source_sequence = excluded.source_sequence,
                    last_received_at_utc = excluded.last_received_at_utc
                """,
                (
                    source.source_message_id,
                    source.chat_id,
                    source.sender_max_user_id,
                    source.sender_display_snapshot,
                    _iso(source.message_timestamp_utc),
                    source.source_sequence,
                    _iso(source.received_at_utc),
                    _iso(source.received_at_utc),
                ),
            )
            existing = connection.execute(
                """
                SELECT id, revision_number, content_hash
                FROM source_message_revisions
                WHERE source_message_id = ? AND content_hash = ?
                """,
                (source.source_message_id, source.content_hash),
            ).fetchone()
            if existing is not None:
                attachment_ids = tuple(
                    int(row["id"])
                    for row in connection.execute(
                        """
                        SELECT id FROM source_message_attachments
                        WHERE revision_id = ? ORDER BY source_order
                        """,
                        (existing["id"],),
                    )
                )
                return StoredSourceRevision(
                    source.source_message_id,
                    int(existing["id"]),
                    int(existing["revision_number"]),
                    str(existing["content_hash"]),
                    False,
                    attachment_ids,
                )
            revision_number = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(revision_number), 0) + 1
                    FROM source_message_revisions WHERE source_message_id = ?
                    """,
                    (source.source_message_id,),
                ).fetchone()[0]
            )
            cursor = connection.execute(
                """
                INSERT INTO source_message_revisions (
                    source_message_id, revision_number, source_sequence,
                    source_text, content_hash, content_json, raw_envelope_json,
                    message_timestamp_utc, edited_at_utc, received_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_message_id,
                    revision_number,
                    source.source_sequence,
                    source.source_text,
                    source.content_hash,
                    source.content_json,
                    source.raw_envelope_json,
                    _iso(source.message_timestamp_utc),
                    _iso(source.edited_at_utc) if source.edited_at_utc else None,
                    _iso(source.received_at_utc),
                ),
            )
            revision_id = int(cursor.lastrowid)
            attachment_ids: list[int] = []
            for attachment in source.attachments:
                has_locator = bool(attachment.source_url or attachment.source_token)
                status = (
                    MediaDownloadStatus.PENDING
                    if has_locator
                    else MediaDownloadStatus.UNAVAILABLE
                )
                error = "" if has_locator else "MAX не предоставил URL или token для загрузки"
                attachment_cursor = connection.execute(
                    """
                    INSERT INTO source_message_attachments (
                        revision_id, source_attachment_id, identity_kind,
                        source_order, attachment_type, mime_type, original_name,
                        source_size, source_url, source_token, source_payload_json,
                        download_status, last_error, received_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        attachment.source_attachment_id,
                        attachment.identity_kind,
                        attachment.source_order,
                        attachment.attachment_type,
                        attachment.mime_type,
                        attachment.original_name,
                        attachment.source_size,
                        attachment.source_url,
                        attachment.source_token,
                        attachment.source_payload_json,
                        status.value,
                        error,
                        _iso(source.received_at_utc),
                    ),
                )
                attachment_ids.append(int(attachment_cursor.lastrowid))
            return StoredSourceRevision(
                source.source_message_id,
                revision_id,
                revision_number,
                source.content_hash,
                True,
                tuple(attachment_ids),
            )

    def record_tombstone(
        self,
        source_message_id: str,
        chat_id: int | None,
        deleted_at_utc: datetime,
        raw_update_json: str,
    ) -> None:
        _require_aware(deleted_at_utc, "deleted_at_utc")
        with self.storage.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_message_tombstones (
                    source_message_id, chat_id, deleted_at_utc, raw_update_json
                ) VALUES (?, ?, ?, ?)
                """,
                (source_message_id, chat_id, _iso(deleted_at_utc), raw_update_json),
            )
            connection.execute(
                """
                UPDATE source_messages
                SET is_deleted = 1, deleted_at_utc = ?, last_received_at_utc = ?
                WHERE source_message_id = ?
                """,
                (_iso(deleted_at_utc), _iso(deleted_at_utc), source_message_id),
            )

    def get_attachment(self, attachment_id: int) -> SourceMediaAttachment | None:
        with self.storage.connect() as connection:
            row = connection.execute(_ATTACHMENT_SELECT + " WHERE a.id = ?", (attachment_id,)).fetchone()
        return _map_attachment(row) if row else None

    def list_revision_attachments(self, revision_id: int) -> list[SourceMediaAttachment]:
        with self.storage.connect() as connection:
            rows = connection.execute(
                _ATTACHMENT_SELECT + " WHERE a.revision_id = ? ORDER BY a.source_order",
                (revision_id,),
            ).fetchall()
        return [_map_attachment(row) for row in rows]

    def list_download_candidates(
        self,
        now_utc: datetime,
        stale_before_utc: datetime,
        max_attempts: int,
        limit: int = 50,
    ) -> list[SourceMediaAttachment]:
        _require_aware(now_utc, "now_utc")
        _require_aware(stale_before_utc, "stale_before_utc")
        with self.storage.connect() as connection:
            rows = connection.execute(
                _ATTACHMENT_SELECT
                + """
                  WHERE a.download_attempts < ? AND (
                      a.download_status = 'pending'
                      OR (
                          a.download_status = 'failed'
                          AND (a.next_retry_at_utc IS NULL OR a.next_retry_at_utc <= ?)
                      )
                      OR (
                          a.download_status = 'downloading'
                          AND a.last_attempt_at_utc <= ?
                      )
                  )
                  ORDER BY r.received_at_utc, a.source_order, a.id
                  LIMIT ?
                """,
                (max_attempts, _iso(now_utc), _iso(stale_before_utc), limit),
            ).fetchall()
        return [_map_attachment(row) for row in rows]

    def begin_download(self, attachment_id: int, attempted_at_utc: datetime) -> bool:
        _require_aware(attempted_at_utc, "attempted_at_utc")
        with self.storage.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE source_message_attachments
                SET download_status = 'downloading',
                    download_attempts = download_attempts + 1,
                    last_attempt_at_utc = ?, next_retry_at_utc = NULL,
                    last_error = ''
                WHERE id = ? AND download_status <> 'downloaded'
                """,
                (_iso(attempted_at_utc), attachment_id),
            )
            return cursor.rowcount == 1

    def complete_download(
        self,
        attachment_id: int,
        *,
        sha256: str,
        storage_key: str,
        size_bytes: int,
        mime_type: str,
        original_name: str,
        downloaded_at_utc: datetime,
    ) -> None:
        _require_aware(downloaded_at_utc, "downloaded_at_utc")
        with self.storage.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE source_message_attachments
                SET download_status = 'downloaded', sha256 = ?, storage_key = ?,
                    source_size = COALESCE(source_size, ?),
                    mime_type = CASE WHEN mime_type = '' THEN ? ELSE mime_type END,
                    original_name = CASE WHEN original_name = '' THEN ? ELSE original_name END,
                    downloaded_at_utc = ?, next_retry_at_utc = NULL, last_error = ''
                WHERE id = ?
                """,
                (
                    sha256,
                    storage_key,
                    size_bytes,
                    mime_type,
                    original_name,
                    _iso(downloaded_at_utc),
                    attachment_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Вложение WorkBot не найдено")

    def fail_download(
        self,
        attachment_id: int,
        *,
        status: MediaDownloadStatus,
        error: str,
        next_retry_at_utc: datetime | None,
    ) -> None:
        if status not in {MediaDownloadStatus.FAILED, MediaDownloadStatus.UNAVAILABLE}:
            raise ValueError("Некорректный статус неуспешной загрузки")
        if next_retry_at_utc is not None:
            _require_aware(next_retry_at_utc, "next_retry_at_utc")
        with self.storage.connect() as connection:
            connection.execute(
                """
                UPDATE source_message_attachments
                SET download_status = ?, last_error = ?, next_retry_at_utc = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    error[:2000],
                    _iso(next_retry_at_utc) if next_retry_at_utc else None,
                    attachment_id,
                ),
            )

    def list_revisions_for_diagnostics(self) -> list[sqlite3.Row]:
        with self.storage.connect() as connection:
            return connection.execute(
                """
                SELECT r.*, m.source_message_id AS parent_message_id
                FROM source_message_revisions r
                LEFT JOIN source_messages m
                    ON m.source_message_id = r.source_message_id
                ORDER BY r.id
                """
            ).fetchall()

    def list_attachments_for_diagnostics(self) -> list[sqlite3.Row]:
        with self.storage.connect() as connection:
            return connection.execute(
                """
                SELECT a.*, r.source_message_id, r.revision_number,
                       r.id AS parent_revision_id
                FROM source_message_attachments a
                LEFT JOIN source_message_revisions r ON r.id = a.revision_id
                ORDER BY a.id
                """
            ).fetchall()

    def counts(self) -> dict[str, int]:
        with self.storage.connect() as connection:
            return {
                "messages": int(connection.execute("SELECT COUNT(*) FROM source_messages").fetchone()[0]),
                "revisions": int(connection.execute("SELECT COUNT(*) FROM source_message_revisions").fetchone()[0]),
                "attachments": int(connection.execute("SELECT COUNT(*) FROM source_message_attachments").fetchone()[0]),
                "tombstones": int(connection.execute("SELECT COUNT(*) FROM source_message_tombstones").fetchone()[0]),
            }


_ATTACHMENT_SELECT = """
    SELECT a.*, r.source_message_id, r.revision_number
    FROM source_message_attachments a
    JOIN source_message_revisions r ON r.id = a.revision_id
"""


def _map_attachment(row: sqlite3.Row) -> SourceMediaAttachment:
    return SourceMediaAttachment(
        id=int(row["id"]),
        revision_id=int(row["revision_id"]),
        source_message_id=str(row["source_message_id"]),
        revision_number=int(row["revision_number"]),
        source_attachment_id=str(row["source_attachment_id"]),
        identity_kind=str(row["identity_kind"]),
        source_order=int(row["source_order"]),
        attachment_type=str(row["attachment_type"]),
        mime_type=str(row["mime_type"]),
        original_name=str(row["original_name"]),
        source_size=int(row["source_size"]) if row["source_size"] is not None else None,
        source_url=str(row["source_url"]) if row["source_url"] else None,
        source_token=str(row["source_token"]) if row["source_token"] else None,
        source_payload_json=str(row["source_payload_json"]),
        download_status=MediaDownloadStatus(str(row["download_status"])),
        sha256=str(row["sha256"]),
        storage_key=str(row["storage_key"]),
        download_attempts=int(row["download_attempts"]),
        last_error=str(row["last_error"]),
        received_at_utc=_datetime(row["received_at_utc"]),
        last_attempt_at_utc=_optional_datetime(row["last_attempt_at_utc"]),
        next_retry_at_utc=_optional_datetime(row["next_retry_at_utc"]),
        downloaded_at_utc=_optional_datetime(row["downloaded_at_utc"]),
    )


def _datetime(value: object) -> datetime:
    result = datetime.fromisoformat(str(value))
    _require_aware(result, "stored datetime")
    return result


def _optional_datetime(value: object) -> datetime | None:
    return _datetime(value) if value else None


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} должен содержать timezone")
