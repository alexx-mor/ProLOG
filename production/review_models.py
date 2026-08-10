"""Typed P11 contracts for review of immutable production inbox proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from production.matching_models import MatchCandidate, ProposalEvidence, ProposalIssue
from production.models import ActorRef, ProductionEventType, require_utc_datetime, validate_readiness


class ReviewStatus(StrEnum):
    REQUIRES_REVIEW = "requires_review"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SOURCE_CHANGED = "source_changed"
    KEPT_EXISTING = "kept_existing"
    FAILED = "failed"


class ReviewFilter(StrEnum):
    REQUIRES_REVIEW = "requires_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SOURCE_CHANGED = "source_changed"
    NEEDS_DESCRIPTION = "needs_description"
    TEXT_ONLY = "text_only"
    ALL = "all"


class RejectionCode(StrEnum):
    NOT_PRODUCTION = "not_production"
    ERRONEOUS_MESSAGE = "erroneous_message"
    DUPLICATE = "duplicate"
    INSUFFICIENT_DATA = "insufficient_data"
    OTHER = "other"


class ManualGroupingOperation(StrEnum):
    SPLIT = "split"
    MERGE = "merge"


@dataclass(frozen=True, slots=True)
class InboxSourceMessageView:
    id: int
    bundle_order: int
    message_role: str
    source_message_id: str
    source_revision_id: int
    source_revision_number: int
    source_text: str
    message_timestamp_utc: datetime
    sender_max_user_id: int | None
    sender_display_snapshot: str

    def __post_init__(self) -> None:
        require_utc_datetime(self.message_timestamp_utc, "message_timestamp_utc")


@dataclass(frozen=True, slots=True)
class InboxSourceAttachmentView:
    id: int
    inbox_message_id: int
    bundle_order: int
    source_order: int
    source_message_id: str
    source_attachment_id: str
    original_name: str
    mime_type: str
    source_sha256: str
    source_storage_key: str
    media_state: str
    source_download_status: str


@dataclass(frozen=True, slots=True)
class ProductionInboxReviewItem:
    bundle_id: int
    bundle_uid: UUID
    bundle_fingerprint: str
    grouping_status: str
    origin: str
    source_id: int
    chat_id: int | None
    sender_max_user_id: int | None
    sender_display_snapshot: str
    observed_at_utc: datetime
    match_run_id: int
    matcher_rule_version: str
    directory_context_fingerprint: str
    proposal_id: int
    proposal_order: int
    source_text: str
    product_id: int | None
    object_id: int | None
    stage_id: int | None
    readiness_percent: int | None
    description_text: str
    match_quality: str
    requires_review: bool
    issue_code: str
    has_media: bool
    attachment_count: int
    review_id: int | None
    review_status: ReviewStatus
    production_event_id: int | None
    source_changed_from_review_id: int | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.observed_at_utc, "observed_at_utc")


@dataclass(frozen=True, slots=True)
class ProductionInboxReviewDetail:
    item: ProductionInboxReviewItem
    messages: tuple[InboxSourceMessageView, ...]
    attachments: tuple[InboxSourceAttachmentView, ...]
    product_candidates: tuple[MatchCandidate, ...]
    stage_candidates: tuple[MatchCandidate, ...]
    object_candidates: tuple[MatchCandidate, ...]
    evidence: tuple[ProposalEvidence, ...]
    issues: tuple[ProposalIssue, ...]
    reported_by_employee_id: int | None
    previous_source_text: str = ""


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    bundle_id: int
    bundle_fingerprint: str
    match_run_id: int
    proposal_id: int
    product_id: int
    stage_id: int | None
    readiness_percent: int | None
    description: str
    observed_at_utc: datetime
    reported_by_employee_id: int | None
    actor: ActorRef
    event_type: ProductionEventType = ProductionEventType.OBSERVATION
    change_reason: str = ""
    correction_source_event_id: int | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.observed_at_utc, "observed_at_utc")
        validate_readiness(self.readiness_percent)
        if self.product_id <= 0:
            raise ValueError("Для подтверждения требуется изделие")
        if self.event_type is ProductionEventType.CORRECTION and not self.correction_source_event_id:
            raise ValueError("Для исправления требуется исходное событие")


@dataclass(frozen=True, slots=True)
class ReviewResult:
    review_id: int
    review_uid: UUID
    status: ReviewStatus
    production_event_id: int | None
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class RefreshSummary:
    imported_messages: int = 0
    changed_messages: int = 0
    source_errors: int = 0
    new_bundles: int = 0
    updated_bundles: int = 0
    new_match_runs: int = 0
    requires_review: int = 0
    source_changed: int = 0
    refreshed_at_utc: datetime | None = None


class ReviewDiagnosticKind(StrEnum):
    CONFIRMED_WITHOUT_EVENT = "confirmed_review_without_event"
    EVENT_MISSING = "review_event_missing"
    STALE_BUNDLE = "stale_bundle_fingerprint"
    STALE_MATCH_RUN = "stale_match_run"
    SOURCE_REF_MISMATCH = "event_source_ref_mismatch"
    PROMOTION_PROVENANCE_MISSING = "promotion_provenance_missing"
    PROMOTION_INCOMPLETE = "promotion_incomplete"
    EVENT_CONFLICT = "review_event_conflict"
    BROKEN_MANUAL_LINEAGE = "broken_manual_lineage"
    DUPLICATE_REVIEW_EFFECTIVE = "duplicate_review_effective_bundle"
    SOURCE_CHANGE_NOT_SURFACED = "source_change_not_surfaced"


@dataclass(frozen=True, slots=True)
class ReviewDiagnosticIssue:
    kind: ReviewDiagnosticKind
    message: str
    review_id: int | None = None
    bundle_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReviewDiagnosticsReport:
    issues: tuple[ReviewDiagnosticIssue, ...]
    review_count: int
    confirmed_count: int
    pending_count: int

    @property
    def is_healthy(self) -> bool:
        return not self.issues
