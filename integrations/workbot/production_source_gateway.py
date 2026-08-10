"""Read-only P8 adapter from WorkBot schema 2 to production transport DTOs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from infrastructure.content_store import ContentStoreError
from production.source_transport_models import (
    SourceAttachmentSnapshot,
    SourceMediaState,
    SourceRevisionSnapshot,
    SourceRevisionFailure,
    SourceSyncCursor,
)
from workbot.media_store import WorkBotMediaStore


class WorkBotProductionSourceGateway:
    def __init__(self, database_path: Path, media_root: Path) -> None:
        self.database_path = database_path
        self.media_root = media_root
        self.store = WorkBotMediaStore(media_root)

    def fetch_new(
        self,
        chat_id: int,
        after_revision_id: int,
        *,
        limit: int,
    ) -> tuple[SourceRevisionSnapshot | SourceRevisionFailure, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT revision.*, message.chat_id, message.sender_max_user_id,
                       message.sender_display_snapshot
                FROM source_message_revisions revision
                JOIN source_messages message
                  ON message.source_message_id = revision.source_message_id
                WHERE message.chat_id = ? AND revision.id > ?
                ORDER BY revision.id
                LIMIT ?
                """,
                (chat_id, after_revision_id, max(1, limit)),
            ).fetchall()
            return tuple(self._snapshot_or_failure(connection, row) for row in rows)

    def fetch_revisions(
        self,
        chat_id: int,
        revision_ids: Sequence[int],
    ) -> tuple[SourceRevisionSnapshot | SourceRevisionFailure, ...]:
        unique_ids = tuple(sorted({int(value) for value in revision_ids if value > 0}))
        if not unique_ids:
            return ()
        placeholders = ",".join("?" for _ in unique_ids)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT revision.*, message.chat_id, message.sender_max_user_id,
                       message.sender_display_snapshot
                FROM source_message_revisions revision
                JOIN source_messages message
                  ON message.source_message_id = revision.source_message_id
                WHERE message.chat_id = ? AND revision.id IN ({placeholders})
                ORDER BY revision.id
                """,
                (chat_id, *unique_ids),
            ).fetchall()
            return tuple(self._snapshot_or_failure(connection, row) for row in rows)

    def revision_identity(
        self,
        chat_id: int,
        revision_id: int,
    ) -> SourceSyncCursor | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT revision.id, revision.source_message_id,
                       revision.revision_number, revision.content_hash
                FROM source_message_revisions revision
                JOIN source_messages message
                  ON message.source_message_id = revision.source_message_id
                WHERE message.chat_id = ? AND revision.id = ?
                """,
                (chat_id, revision_id),
            ).fetchone()
        if row is None:
            return None
        return SourceSyncCursor(
            int(row["id"]), str(row["source_message_id"]),
            int(row["revision_number"]), str(row["content_hash"]),
        )

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise FileNotFoundError(f"База WorkBot не найдена: {self.database_path}")
        uri = self.database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        version = connection.execute(
            """
            SELECT MAX(version) FROM SchemaMigrations WHERE component = 'workbot'
            """
        ).fetchone()[0]
        if version != 2:
            connection.close()
            raise RuntimeError(
                f"Production transport требует WorkBot schema 2, обнаружено: {version}"
            )
        return connection

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> SourceRevisionSnapshot:
        attachment_rows = connection.execute(
            """
            SELECT * FROM source_message_attachments
            WHERE revision_id = ? ORDER BY source_order, id
            """,
            (int(row["id"]),),
        ).fetchall()
        raw_envelope = str(row["raw_envelope_json"])
        sender_is_bot = False
        try:
            raw = json.loads(raw_envelope)
            sender = (raw.get("message") or {}).get("sender") or {}
            sender_is_bot = bool(sender.get("is_bot"))
        except (AttributeError, TypeError, ValueError):
            pass
        return SourceRevisionSnapshot(
            revision_id=int(row["id"]),
            revision_number=int(row["revision_number"]),
            source_message_id=str(row["source_message_id"]),
            chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
            sender_max_user_id=(
                int(row["sender_max_user_id"])
                if row["sender_max_user_id"] is not None else None
            ),
            sender_display_snapshot=str(row["sender_display_snapshot"]),
            sender_is_bot=sender_is_bot,
            message_timestamp_utc=_datetime(row["message_timestamp_utc"]),
            edited_at_utc=(
                _datetime(row["edited_at_utc"])
                if row["edited_at_utc"] else None
            ),
            received_at_utc=_datetime(row["received_at_utc"]),
            source_sequence=(
                int(row["source_sequence"])
                if row["source_sequence"] is not None else None
            ),
            source_text=(
                str(row["source_text"]) if row["source_text"] is not None else None
            ),
            content_hash=str(row["content_hash"]),
            content_json=str(row["content_json"]),
            raw_envelope_json=raw_envelope,
            attachments=tuple(self._attachment(item) for item in attachment_rows),
        )

    def _snapshot_or_failure(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> SourceRevisionSnapshot | SourceRevisionFailure:
        try:
            return self._snapshot(connection, row)
        except Exception as exc:
            return SourceRevisionFailure(
                int(row["id"]),
                int(row["revision_number"]),
                str(row["source_message_id"]),
                str(row["content_hash"]),
                str(exc),
            )

    def _attachment(self, row: sqlite3.Row) -> SourceAttachmentSnapshot:
        status = str(row["download_status"])
        storage_key = str(row["storage_key"] or "")
        sha256 = str(row["sha256"] or "")
        state, issue_code, issue_message = _status_state(status)
        if status == "downloaded":
            try:
                verification = self.store.verify(storage_key, sha256)
                if not verification.exists:
                    state = SourceMediaState.MISSING
                    issue_code = "source_media_missing"
                    issue_message = "WorkBot media metadata существует, физический файл отсутствует"
                elif not verification.is_valid:
                    state = SourceMediaState.CORRUPT
                    issue_code = "source_media_corrupt"
                    issue_message = "SHA-256 WorkBot media не совпадает с metadata"
                else:
                    state = SourceMediaState.AVAILABLE
            except (ContentStoreError, ValueError) as exc:
                state = SourceMediaState.UNSAFE
                issue_code = "source_media_unsafe"
                issue_message = str(exc)
                storage_key = ""
        return SourceAttachmentSnapshot(
            source_row_id=int(row["id"]),
            source_attachment_id=str(row["source_attachment_id"]),
            identity_kind=str(row["identity_kind"]),
            source_order=int(row["source_order"]),
            attachment_type=str(row["attachment_type"]),
            mime_type=str(row["mime_type"]),
            original_name=str(row["original_name"]),
            source_size=int(row["source_size"]) if row["source_size"] is not None else None,
            download_status=status,
            sha256=sha256,
            storage_key=storage_key,
            downloaded_at_utc=(
                _datetime(row["downloaded_at_utc"])
                if row["downloaded_at_utc"] else None
            ),
            media_state=state,
            source_metadata_json=str(row["source_payload_json"]),
            issue_code=issue_code,
            issue_message=issue_message,
        )


def _status_state(status: str) -> tuple[SourceMediaState, str, str]:
    if status in {"pending", "downloading"}:
        return SourceMediaState.PENDING, "source_media_pending", "WorkBot media еще не загружено"
    if status == "failed":
        return SourceMediaState.FAILED, "source_media_failed", "Загрузка WorkBot media завершилась ошибкой"
    if status == "unavailable":
        return SourceMediaState.UNAVAILABLE, "source_media_unavailable", "MAX media недоступно"
    return SourceMediaState.AVAILABLE, "", ""


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("WorkBot source datetime должен содержать timezone")
    return parsed.astimezone(timezone.utc)
