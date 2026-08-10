"""Typed contracts for the P8 production source transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from production.models import require_utc_datetime


class InboxSourceType(StrEnum):
    MAX_CHAT = "max_chat"
    HISTORICAL_IMPORT = "historical_import"


class InboxChangeKind(StrEnum):
    ORIGINAL = "original"
    CHANGED = "changed"


class SourceMediaState(StrEnum):
    AVAILABLE = "available"
    PENDING = "pending"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class ProductionInboxSource:
    source_type: InboxSourceType
    display_name: str
    chat_id: int | None = None
    enabled: bool = True
    web_url: str = ""
    source_ref: str = ""
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None
    id: int | None = None
    uid: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if self.source_type is InboxSourceType.MAX_CHAT and self.chat_id == 0:
            raise ValueError("MAX chat_id must not be zero")
        for name, value in (
            ("created_at_utc", self.created_at_utc),
            ("updated_at_utc", self.updated_at_utc),
        ):
            if value is not None:
                require_utc_datetime(value, name)


@dataclass(frozen=True, slots=True)
class SourceSyncCursor:
    revision_id: int = 0
    message_id: str = ""
    revision_number: int = 0
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class SourceAttachmentSnapshot:
    source_row_id: int
    source_attachment_id: str
    identity_kind: str
    source_order: int
    attachment_type: str
    mime_type: str
    original_name: str
    source_size: int | None
    download_status: str
    sha256: str
    storage_key: str
    downloaded_at_utc: datetime | None
    media_state: SourceMediaState
    source_metadata_json: str
    issue_code: str = ""
    issue_message: str = ""


@dataclass(frozen=True, slots=True)
class SourceRevisionSnapshot:
    revision_id: int
    revision_number: int
    source_message_id: str
    chat_id: int | None
    sender_max_user_id: int | None
    sender_display_snapshot: str
    sender_is_bot: bool
    message_timestamp_utc: datetime
    edited_at_utc: datetime | None
    received_at_utc: datetime
    source_sequence: int | None
    source_text: str | None
    content_hash: str
    content_json: str
    raw_envelope_json: str
    attachments: tuple[SourceAttachmentSnapshot, ...] = ()

    def __post_init__(self) -> None:
        require_utc_datetime(self.message_timestamp_utc, "message_timestamp_utc")
        require_utc_datetime(self.received_at_utc, "received_at_utc")
        if self.edited_at_utc is not None:
            require_utc_datetime(self.edited_at_utc, "edited_at_utc")


@dataclass(frozen=True, slots=True)
class SourceRevisionFailure:
    revision_id: int
    revision_number: int
    source_message_id: str
    content_hash: str
    error: str


@dataclass(frozen=True, slots=True)
class ProductionInboxMessageSnapshot:
    source_id: int
    source_message_id: str
    source_revision_id: int
    source_revision_number: int
    content_hash: str
    change_kind: InboxChangeKind
    supersedes_inbox_message_id: int | None
    id: int
    uid: UUID


@dataclass(frozen=True, slots=True)
class ProductionSourceSyncResult:
    source_id: int
    read_count: int = 0
    imported_count: int = 0
    unchanged_count: int = 0
    changed_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    cursor_before: int = 0
    cursor_after: int = 0


class ProductionSourceDiagnosticKind(StrEnum):
    SOURCE_WITHOUT_CHAT_ID = "source_without_chat_id"
    CURSOR_IDENTITY_MISMATCH = "cursor_identity_mismatch"
    SYNC_ISSUE = "sync_issue"
    MESSAGE_WITHOUT_SOURCE = "message_without_source"
    ATTACHMENT_WITHOUT_MESSAGE = "attachment_without_message"
    DUPLICATE_SOURCE_IDENTITY = "duplicate_source_identity"
    PRODUCTION_EVENT_LINK = "production_event_link"


@dataclass(frozen=True, slots=True)
class ProductionSourceDiagnosticIssue:
    kind: ProductionSourceDiagnosticKind
    message: str
    source_id: int | None = None
    source_revision_id: int | None = None


@dataclass(frozen=True, slots=True)
class ProductionSourceDiagnosticsReport:
    issues: tuple[ProductionSourceDiagnosticIssue, ...]
    source_count: int
    message_count: int
    attachment_count: int

    @property
    def is_healthy(self) -> bool:
        return not self.issues
