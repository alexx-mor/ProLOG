"""SQLite repository for Attachment metadata only."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID

from production.errors import AttachmentSourceExistsError
from production.attachment_types import AttachmentStorageReference
from production.models import Attachment

if TYPE_CHECKING:
    from database import Database


class AttachmentRepository:
    """Persist metadata without reading, copying or deleting physical files."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, attachment: Attachment) -> Attachment:
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO Attachments (
                        uid, storage_key, sha256, original_name, mime_type,
                        size_bytes, width, height, captured_at_utc,
                        received_at_utc, source_type, source_message_id,
                        source_attachment_id, created_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._params(attachment),
                )
        except sqlite3.IntegrityError as error:
            if self._has_complete_source(attachment) and self.find_by_source(
                str(attachment.source_type),
                str(attachment.source_message_id),
                str(attachment.source_attachment_id),
            ) is not None:
                raise AttachmentSourceExistsError(
                    "Вложение с такими идентификаторами источника уже зарегистрировано"
                ) from error
            raise
        return replace(attachment, id=int(cursor.lastrowid))

    def get_by_id(self, attachment_id: int) -> Attachment | None:
        return self._get("id = ?", (attachment_id,))

    def get_by_uid(self, uid: UUID) -> Attachment | None:
        return self._get("uid = ?", (str(uid),))

    def find_by_sha256(self, sha256: str) -> list[Attachment]:
        return self._list("WHERE sha256 = ?", (sha256.lower(),))

    def find_by_source(
        self,
        source_type: str,
        source_message_id: str,
        source_attachment_id: str,
    ) -> Attachment | None:
        return self._get(
            """
            source_type = ? AND source_message_id = ? AND source_attachment_id = ?
            """,
            (source_type, source_message_id, source_attachment_id),
        )

    def list_for_diagnostics(self) -> list[AttachmentStorageReference]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, storage_key, sha256 FROM Attachments ORDER BY id"
            ).fetchall()
        return [
            AttachmentStorageReference(
                attachment_id=int(row["id"]),
                storage_key=str(row["storage_key"]),
                sha256=str(row["sha256"]),
            )
            for row in rows
        ]

    def exists(self, attachment_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM Attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        return row is not None

    def _get(self, condition: str, params: tuple[object, ...]) -> Attachment | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM Attachments WHERE {condition}",
                params,
            ).fetchone()
        return self._map(row) if row else None

    def _list(self, where_sql: str, params: tuple[object, ...]) -> list[Attachment]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM Attachments {where_sql} ORDER BY id",
                params,
            ).fetchall()
        return [self._map(row) for row in rows]

    @staticmethod
    def _params(attachment: Attachment) -> tuple[object, ...]:
        return (
            str(attachment.uid),
            attachment.storage_key,
            attachment.sha256.lower(),
            attachment.original_name,
            attachment.mime_type,
            attachment.size_bytes,
            attachment.width,
            attachment.height,
            attachment.captured_at_utc.isoformat()
            if attachment.captured_at_utc
            else None,
            attachment.received_at_utc.isoformat(),
            attachment.source_type,
            attachment.source_message_id,
            attachment.source_attachment_id,
            attachment.created_at_utc.isoformat(),
        )

    @staticmethod
    def _map(row: sqlite3.Row) -> Attachment:
        from datetime import datetime

        return Attachment(
            id=int(row["id"]),
            uid=UUID(str(row["uid"])),
            storage_key=str(row["storage_key"]),
            sha256=str(row["sha256"]),
            original_name=str(row["original_name"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            width=int(row["width"]) if row["width"] is not None else None,
            height=int(row["height"]) if row["height"] is not None else None,
            captured_at_utc=datetime.fromisoformat(str(row["captured_at_utc"]))
            if row["captured_at_utc"]
            else None,
            received_at_utc=datetime.fromisoformat(str(row["received_at_utc"])),
            source_type=str(row["source_type"]) if row["source_type"] is not None else None,
            source_message_id=str(row["source_message_id"])
            if row["source_message_id"] is not None
            else None,
            source_attachment_id=str(row["source_attachment_id"])
            if row["source_attachment_id"] is not None
            else None,
            created_at_utc=datetime.fromisoformat(str(row["created_at_utc"])),
        )

    @staticmethod
    def _has_complete_source(attachment: Attachment) -> bool:
        return all(
            value and value.strip()
            for value in (
                attachment.source_type,
                attachment.source_message_id,
                attachment.source_attachment_id,
            )
        )
