"""Typed contracts for immutable MAX source messages and media."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class MediaDownloadStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class MediaUnavailableError(RuntimeError):
    """MAX did not expose retrievable original media for this attachment."""


@dataclass(frozen=True, slots=True)
class SourceAttachmentInput:
    source_attachment_id: str
    identity_kind: str
    source_order: int
    attachment_type: str
    mime_type: str
    original_name: str
    source_size: int | None
    source_url: str | None
    source_token: str | None
    source_payload_json: str


@dataclass(frozen=True, slots=True)
class SourceRevisionInput:
    source_message_id: str
    chat_id: int | None
    sender_max_user_id: int
    sender_display_snapshot: str
    message_timestamp_utc: datetime
    edited_at_utc: datetime | None
    source_sequence: int | None
    source_text: str | None
    content_hash: str
    content_json: str
    raw_envelope_json: str
    received_at_utc: datetime
    attachments: tuple[SourceAttachmentInput, ...]


@dataclass(frozen=True, slots=True)
class StoredSourceRevision:
    source_message_id: str
    revision_id: int
    revision_number: int
    content_hash: str
    created: bool
    attachment_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SourceMediaAttachment:
    id: int
    revision_id: int
    source_message_id: str
    revision_number: int
    source_attachment_id: str
    identity_kind: str
    source_order: int
    attachment_type: str
    mime_type: str
    original_name: str
    source_size: int | None
    source_url: str | None
    source_token: str | None
    source_payload_json: str
    download_status: MediaDownloadStatus
    sha256: str
    storage_key: str
    download_attempts: int
    last_error: str
    received_at_utc: datetime
    last_attempt_at_utc: datetime | None
    next_retry_at_utc: datetime | None
    downloaded_at_utc: datetime | None


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    content: bytes
    mime_type: str = ""
    original_name: str = ""


class WorkBotMediaDiagnosticKind(StrEnum):
    REVISION_WITHOUT_MESSAGE = "revision_without_message"
    ATTACHMENT_WITHOUT_REVISION = "attachment_without_revision"
    MISSING_FILE = "missing_file"
    HASH_MISMATCH = "hash_mismatch"
    ORPHAN_FILE = "orphan_file"
    TEMP_FILE = "temp_file"
    DUPLICATE_SOURCE_IDENTITY = "duplicate_source_identity"
    STALE_PENDING = "stale_pending"
    FAILED_DOWNLOAD = "failed_download"
    UNAVAILABLE_MEDIA = "unavailable_media"
    UNSAFE_STORAGE_KEY = "unsafe_storage_key"
    ROOT_UNAVAILABLE = "root_unavailable"
    REVISION_CONTENT_INCONSISTENCY = "revision_content_inconsistency"


@dataclass(frozen=True, slots=True)
class WorkBotMediaDiagnosticIssue:
    kind: WorkBotMediaDiagnosticKind
    message: str
    source_message_id: str = ""
    revision_id: int | None = None
    attachment_id: int | None = None
    storage_key: str = ""


@dataclass(frozen=True, slots=True)
class WorkBotMediaDiagnosticsReport:
    root: Path
    issues: tuple[WorkBotMediaDiagnosticIssue, ...]
    checked_revisions: int = 0
    checked_attachments: int = 0
    checked_files: int = 0

    @property
    def is_healthy(self) -> bool:
        return not self.issues

    def count(self, kind: WorkBotMediaDiagnosticKind) -> int:
        return sum(issue.kind is kind for issue in self.issues)
