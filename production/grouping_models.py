"""Typed deterministic grouping contracts for Production Inbox P9."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from production.models import require_utc_datetime


GROUPING_RULE_VERSION = "deterministic-v1"


class GroupingStatus(StrEnum):
    COLLECTING = "collecting"
    COMPLETE = "complete"
    NEEDS_DESCRIPTION = "needs_description"
    TEXT_ONLY = "text_only"
    INVALID = "invalid"


class BundleMessageRole(StrEnum):
    PHOTO_SOURCE = "photo_source"
    CLOSING_TEXT = "closing_text"
    CAPTIONED_MEDIA = "captioned_media"
    TEXT_ONLY = "text_only"
    SOURCE_ONLY = "source_only"


class BundleOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class EffectiveInboxMessage:
    id: int
    source_id: int
    source_message_id: str
    source_revision_id: int
    source_revision_number: int
    chat_id: int | None
    sender_max_user_id: int | None
    sender_display_snapshot: str
    message_timestamp_utc: datetime
    source_sequence: int | None
    source_text: str
    content_hash: str
    attachment_orders: tuple[int, ...]

    def __post_init__(self) -> None:
        require_utc_datetime(self.message_timestamp_utc, "message_timestamp_utc")

    @property
    def has_text(self) -> bool:
        return bool(self.source_text.strip())

    @property
    def has_media(self) -> bool:
        return bool(self.attachment_orders)


@dataclass(frozen=True, slots=True)
class BundleMessageCandidate:
    message: EffectiveInboxMessage
    role: BundleMessageRole


@dataclass(frozen=True, slots=True)
class BundleCandidate:
    source_id: int
    chat_id: int | None
    sender_max_user_id: int | None
    sender_display_snapshot: str
    started_at_utc: datetime
    ended_at_utc: datetime
    grouping_status: GroupingStatus
    close_reason: str
    grouping_rule_version: str
    grouping_window_seconds: int
    day_boundary_utc_offset_minutes: int
    source_fingerprint: str
    messages: tuple[BundleMessageCandidate, ...]


@dataclass(frozen=True, slots=True)
class ProductionInboxGroupedBundle:
    id: int
    uid: UUID
    source_id: int
    chat_id: int | None
    sender_max_user_id: int | None
    sender_display_snapshot: str
    started_at_utc: datetime
    ended_at_utc: datetime
    grouping_status: GroupingStatus
    close_reason: str
    origin: BundleOrigin
    grouping_rule_version: str
    grouping_window_seconds: int
    day_boundary_utc_offset_minutes: int
    source_fingerprint: str
    supersedes_bundle_id: int | None
    is_current: bool
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class GroupingResult:
    source_count: int
    effective_message_count: int
    candidate_count: int
    created_count: int = 0
    unchanged_count: int = 0
    updated_count: int = 0
    superseded_count: int = 0


class GroupingDiagnosticKind(StrEnum):
    UNGROUPED_EFFECTIVE_MESSAGE = "ungrouped_effective_message"
    MULTIPLE_CURRENT_BUNDLES = "multiple_current_bundles"
    MIXED_SENDER = "mixed_sender"
    MIXED_SOURCE_OR_CHAT = "mixed_source_or_chat"
    BROKEN_BUNDLE_ORDER = "broken_bundle_order"
    BROKEN_ATTACHMENT_ORDER = "broken_attachment_order"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    UNKNOWN_RULE_VERSION = "unknown_rule_version"
    STALE_CURRENT_BUNDLE = "stale_current_bundle"
    EXPIRED_COLLECTING_BUNDLE = "expired_collecting_bundle"
    BROKEN_LINEAGE = "broken_lineage"


@dataclass(frozen=True, slots=True)
class GroupingDiagnosticIssue:
    kind: GroupingDiagnosticKind
    message: str
    bundle_id: int | None = None
    inbox_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class GroupingDiagnosticsReport:
    issues: tuple[GroupingDiagnosticIssue, ...]
    effective_message_count: int
    current_bundle_count: int
    historical_bundle_count: int

    @property
    def is_healthy(self) -> bool:
        return not self.issues
