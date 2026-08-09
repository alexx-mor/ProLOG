"""Typed query contracts for future production read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from production.errors import InvalidLifecycleStateError
from production.models import require_utc_datetime


@dataclass(frozen=True, slots=True)
class GetProductionEvent:
    """Locate one event by its local ID or global UID."""

    event_id: int | None = None
    event_uid: UUID | None = None

    def __post_init__(self) -> None:
        if (self.event_id is None) == (self.event_uid is None):
            raise InvalidLifecycleStateError(
                "GetProductionEvent requires exactly one of event_id or event_uid"
            )


@dataclass(frozen=True, slots=True)
class GetProductTimeline:
    """Request the chronological production facts for one local product."""

    product_id: int
    observed_from_utc: datetime | None = None
    observed_to_utc: datetime | None = None
    include_rejected: bool = False

    def __post_init__(self) -> None:
        if self.product_id <= 0:
            raise InvalidLifecycleStateError("product_id must be positive")
        if self.observed_from_utc is not None:
            require_utc_datetime(self.observed_from_utc, "observed_from_utc")
        if self.observed_to_utc is not None:
            require_utc_datetime(self.observed_to_utc, "observed_to_utc")
        if (
            self.observed_from_utc is not None
            and self.observed_to_utc is not None
            and self.observed_from_utc > self.observed_to_utc
        ):
            raise InvalidLifecycleStateError(
                "observed_from_utc must not be later than observed_to_utc"
            )
