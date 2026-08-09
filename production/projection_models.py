"""Typed read models derived from immutable production facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from models import WorkLogEntry
from production.models import (
    Attachment,
    ProductionEvent,
    ProductionEventWorkLog,
    ProductionStage,
)


class ReadinessSource(StrEnum):
    LEGACY_SNAPSHOT = "legacy_snapshot"
    PRODUCTION_EVENT = "production_event"


@dataclass(frozen=True, slots=True)
class ProductProductionState:
    product_id: int
    object_id: int
    current_stage_id: int | None
    current_stage_code: str | None
    current_stage_name: str | None
    readiness_percent: int | None
    readiness_source: ReadinessSource
    last_observed_at_utc: datetime | None
    latest_effective_event_id: int | None
    latest_effective_event_uid: UUID | None
    event_count: int
    attachment_count: int
    first_observed_at_utc: datetime | None


@dataclass(frozen=True, slots=True)
class TimelineAttachment:
    attachment: Attachment
    sort_order: int


@dataclass(frozen=True, slots=True)
class TimelineWorkLog:
    relation: ProductionEventWorkLog
    worklog: WorkLogEntry


@dataclass(frozen=True, slots=True)
class ProductionTimelineItem:
    event: ProductionEvent
    stage: ProductionStage | None
    reported_employee_name: str | None
    attachments: tuple[TimelineAttachment, ...]
    worklogs: tuple[TimelineWorkLog, ...]
    is_effective: bool
    superseded_by_event_id: int | None
    superseded_by_event_uid: UUID | None


@dataclass(frozen=True, slots=True)
class ProductLaborInterval:
    product_id: int
    previous_event_id: int
    current_event_id: int
    previous_observed_at_utc: datetime
    current_observed_at_utc: datetime
    work_date_from_exclusive: date
    work_date_to_inclusive: date
    worklog_ids: tuple[int, ...]
    worklog_count: int
    employee_count: int
    total_hours: float
    person_hours: float
    day_granularity_ambiguous: bool


class ProjectionDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ProjectionDiagnosticKind(StrEnum):
    PRODUCT_MISSING = "production_event_product_missing"
    STAGE_MISSING = "production_event_stage_missing"
    STAGE_INACTIVE = "production_event_stage_inactive"
    SUPERSEDE_CHAIN_INCONSISTENT = "production_supersede_chain_inconsistent"
    SNAPSHOT_MISMATCH = "product_readiness_snapshot_mismatch"


@dataclass(frozen=True, slots=True)
class ProjectionDiagnosticIssue:
    kind: ProjectionDiagnosticKind
    severity: ProjectionDiagnosticSeverity
    product_id: int
    message: str
    event_id: int | None = None
    expected_readiness: int | None = None
    actual_readiness: int | None = None


@dataclass(frozen=True, slots=True)
class ProjectionDiagnosticsReport:
    checked_product_ids: tuple[int, ...]
    issues: tuple[ProjectionDiagnosticIssue, ...]

    @property
    def is_healthy(self) -> bool:
        return not any(
            issue.severity is ProjectionDiagnosticSeverity.ERROR
            for issue in self.issues
        )

    def count(self, kind: ProjectionDiagnosticKind) -> int:
        return sum(issue.kind is kind for issue in self.issues)
