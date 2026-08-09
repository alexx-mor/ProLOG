"""Coordinator for physical attachment content and SQLite metadata."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from production.attachment_inspection import inspect_attachment
from production.attachment_repository import AttachmentRepository
from production.attachment_store import AttachmentStore
from production.attachment_types import (
    AttachmentDiagnosticsReport,
)
from production.errors import (
    AttachmentIntegrityError,
    AttachmentSourceExistsError,
)
from production.models import Attachment, require_utc_datetime, utc_now


class AttachmentService:
    """Safely create metadata only after physical content is durable."""

    def __init__(
        self,
        repository: AttachmentRepository,
        store: AttachmentStore,
    ) -> None:
        self.repository = repository
        self.store = store

    def store_file(
        self,
        source_path: Path,
        *,
        received_at_utc: datetime,
        source_type: str | None = None,
        source_message_id: str | None = None,
        source_attachment_id: str | None = None,
    ) -> Attachment:
        path = Path(source_path)
        return self.store_bytes(
            path.read_bytes(),
            original_name=path.name,
            received_at_utc=received_at_utc,
            source_type=source_type,
            source_message_id=source_message_id,
            source_attachment_id=source_attachment_id,
        )

    def store_bytes(
        self,
        content: bytes,
        *,
        original_name: str,
        received_at_utc: datetime,
        source_type: str | None = None,
        source_message_id: str | None = None,
        source_attachment_id: str | None = None,
    ) -> Attachment:
        require_utc_datetime(received_at_utc, "received_at_utc")
        sha256 = hashlib.sha256(content).hexdigest()
        existing = self._find_existing_source(
            source_type,
            source_message_id,
            source_attachment_id,
        )
        if existing is not None:
            self._require_matching_source_content(existing, sha256)
            return existing

        inspection = inspect_attachment(content)
        stored = self.store.put(content, sha256)
        attachment = Attachment(
            storage_key=stored.storage_key,
            sha256=sha256,
            original_name=Path(original_name).name or "attachment",
            mime_type=inspection.mime_type,
            size_bytes=len(content),
            width=inspection.width,
            height=inspection.height,
            captured_at_utc=inspection.captured_at_utc,
            received_at_utc=received_at_utc,
            source_type=source_type,
            source_message_id=source_message_id,
            source_attachment_id=source_attachment_id,
            created_at_utc=utc_now(),
        )
        try:
            return self.repository.create(attachment)
        except AttachmentSourceExistsError:
            concurrent = self._find_existing_source(
                source_type,
                source_message_id,
                source_attachment_id,
            )
            if concurrent is None:
                raise
            self._require_matching_source_content(concurrent, sha256)
            return concurrent

    def diagnostics(self) -> AttachmentDiagnosticsReport:
        return self.store.diagnostics(self.repository.list_for_diagnostics())

    def _find_existing_source(
        self,
        source_type: str | None,
        source_message_id: str | None,
        source_attachment_id: str | None,
    ) -> Attachment | None:
        values = (source_type, source_message_id, source_attachment_id)
        if not all(value and value.strip() for value in values):
            return None
        return self.repository.find_by_source(*values)  # type: ignore[arg-type]

    def _require_valid_physical_file(self, attachment: Attachment) -> None:
        result = self.store.verify(attachment.storage_key, attachment.sha256)
        if not result.exists:
            raise AttachmentIntegrityError(
                "Повторно доставленное вложение зарегистрировано, но физический файл отсутствует"
            )
        if not result.is_valid:
            raise AttachmentIntegrityError(
                "Повторно доставленное вложение зарегистрировано, но физический файл поврежден"
            )

    def _require_matching_source_content(
        self,
        attachment: Attachment,
        incoming_sha256: str,
    ) -> None:
        if attachment.sha256 != incoming_sha256:
            raise AttachmentIntegrityError(
                "Один source attachment получен с различающимся физическим содержимым"
            )
        self._require_valid_physical_file(attachment)
