"""Typed contracts for deterministic Production Inbox interpretation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


MATCHER_RULE_VERSION = "production-matcher-v1"


class MatchQuality(StrEnum):
    EXACT = "exact"
    STRONG = "strong"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


class MatchRunStatus(StrEnum):
    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    NO_TEXT = "no_text"


@dataclass(frozen=True, slots=True)
class ProductionStageAlias:
    stage_id: int
    alias_text: str
    normalized_alias: str
    is_active: bool = True
    id: int | None = None
    uid: UUID | None = None


@dataclass(frozen=True, slots=True)
class MatchingObject:
    id: int
    name: str
    project_number: str
    contract_number: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class MatchingProduct:
    id: int
    object_id: int
    serial_number: str
    name: str
    code: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class MatchingStage:
    id: int
    code: str
    name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class MatchingAlias:
    target_id: int
    alias_text: str
    normalized_alias: str
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class MatchingContext:
    objects: tuple[MatchingObject, ...]
    products: tuple[MatchingProduct, ...]
    stages: tuple[MatchingStage, ...]
    object_aliases: tuple[MatchingAlias, ...]
    product_aliases: tuple[MatchingAlias, ...]
    stage_aliases: tuple[MatchingAlias, ...]


@dataclass(frozen=True, slots=True)
class BundleMatchingInput:
    bundle_id: int
    bundle_fingerprint: str
    grouping_status: str
    source_text: str
    has_media: bool


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    target_id: int
    rank: int
    score: int
    method: str
    matched_text: str
    evidence: str
    is_active: bool
    object_id: int | None = None


@dataclass(frozen=True, slots=True)
class ProposalEvidence:
    field_name: str
    method: str
    matched_text: str
    explanation: str


@dataclass(frozen=True, slots=True)
class ProposalIssue:
    code: str
    message: str
    evidence_text: str = ""


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    order: int
    source_segment_text: str
    normalized_segment_text: str
    source_segment_start: int | None
    source_segment_end: int | None
    object_id: int | None
    object_match_method: str | None
    product_id: int | None
    product_match_method: str | None
    stage_id: int | None
    stage_match_method: str | None
    readiness_percent: int | None
    readiness_match_method: str | None
    description_text: str
    normalized_description: str
    match_quality: MatchQuality
    requires_review: bool
    product_candidates: tuple[MatchCandidate, ...] = ()
    object_candidates: tuple[MatchCandidate, ...] = ()
    stage_candidates: tuple[MatchCandidate, ...] = ()
    evidence: tuple[ProposalEvidence, ...] = ()
    issues: tuple[ProposalIssue, ...] = ()

    @property
    def issue_code(self) -> str | None:
        return self.issues[0].code if self.issues else None


@dataclass(frozen=True, slots=True)
class MatchAnalysis:
    bundle: BundleMatchingInput
    normalized_text: str
    context_fingerprint: str
    input_text_hash: str
    result_fingerprint: str
    status: MatchRunStatus
    proposals: tuple[ProposalDraft, ...]


@dataclass(frozen=True, slots=True)
class ProductionInboxMatchRun:
    id: int
    uid: UUID
    bundle_id: int
    bundle_fingerprint: str
    matcher_rule_version: str
    directory_context_fingerprint: str
    input_text_hash: str
    result_fingerprint: str
    source_text: str
    normalized_text: str
    has_media: bool
    status: MatchRunStatus
    is_current: bool
    supersedes_match_run_id: int | None
    created_at_utc: datetime


@dataclass(frozen=True, slots=True)
class PersistedProposal:
    id: int
    match_run_id: int
    draft: ProposalDraft


@dataclass(frozen=True, slots=True)
class MatchingResult:
    run: ProductionInboxMatchRun
    proposals: tuple[PersistedProposal, ...]
    created: bool


class MatchingDiagnosticKind(StrEnum):
    CURRENT_BUNDLE_WITHOUT_RUN = "current_bundle_without_current_match_run"
    BUNDLE_FINGERPRINT_MISMATCH = "match_run_bundle_fingerprint_mismatch"
    CONTEXT_FINGERPRINT_MISMATCH = "match_context_fingerprint_mismatch"
    PRODUCT_MISSING = "proposal_product_missing"
    OBJECT_MISSING = "proposal_object_missing"
    STAGE_MISSING = "proposal_stage_missing"
    PRODUCT_OBJECT_CONFLICT = "proposal_product_object_conflict"
    DUPLICATE_CANDIDATE_RANK = "duplicate_candidate_rank"
    INVALID_READINESS = "invalid_readiness"
    READINESS_AMBIGUOUS = "readiness_ambiguous"
    INACTIVE_SELECTED = "inactive_selected_candidate"
    UNKNOWN_RULE_VERSION = "unknown_matcher_rule_version"
    BROKEN_LINEAGE = "broken_match_run_lineage"


@dataclass(frozen=True, slots=True)
class MatchingDiagnosticIssue:
    kind: MatchingDiagnosticKind
    message: str
    bundle_id: int | None = None
    match_run_id: int | None = None
    proposal_id: int | None = None


@dataclass(frozen=True, slots=True)
class MatchingDiagnosticsReport:
    issues: tuple[MatchingDiagnosticIssue, ...] = field(default_factory=tuple)
    current_bundle_count: int = 0
    current_run_count: int = 0
    proposal_count: int = 0

    @property
    def is_healthy(self) -> bool:
        return not self.issues
