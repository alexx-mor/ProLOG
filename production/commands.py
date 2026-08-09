"""Typed application-command contracts for a future ProductionService."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from production.errors import CorrectionSourceRequiredError
from production.models import (
    ActorRef,
    ProductionEventType,
    ProductionSourceType,
    require_utc_datetime,
    validate_readiness,
)


@dataclass(frozen=True, slots=True)
class CreateProductionEvent:
    actor: ActorRef
    observed_at_utc: datetime
    source_type: ProductionSourceType
    product_id: int | None = None
    object_id_snapshot: int | None = None
    stage_id: int | None = None
    event_type: ProductionEventType = ProductionEventType.OBSERVATION
    readiness_percent: int | None = None
    description: str = ""
    source_ref: str | None = None
    reported_by_employee_id: int | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.observed_at_utc, "observed_at_utc")
        validate_readiness(self.readiness_percent)


@dataclass(frozen=True, slots=True)
class ConfirmProductionEvent:
    event_id: int
    actor: ActorRef
    confirmed_at_utc: datetime

    def __post_init__(self) -> None:
        require_utc_datetime(self.confirmed_at_utc, "confirmed_at_utc")


@dataclass(frozen=True, slots=True)
class RejectProductionEvent:
    event_id: int
    actor: ActorRef
    rejected_at_utc: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        require_utc_datetime(self.rejected_at_utc, "rejected_at_utc")


@dataclass(frozen=True, slots=True)
class CorrectProductionEvent:
    actor: ActorRef
    observed_at_utc: datetime
    source_event_id: int | None
    source_type: ProductionSourceType
    product_id: int | None = None
    object_id_snapshot: int | None = None
    stage_id: int | None = None
    readiness_percent: int | None = None
    description: str = ""
    source_ref: str | None = None
    reported_by_employee_id: int | None = None
    idempotency_key: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.source_event_id is None:
            raise CorrectionSourceRequiredError(
                "CorrectProductionEvent requires source_event_id"
            )
        require_utc_datetime(self.observed_at_utc, "observed_at_utc")
        validate_readiness(self.readiness_percent)
