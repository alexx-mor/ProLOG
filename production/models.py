"""Pure domain models for observed production progress.

Production facts are deliberately separate from work-log facts. This module is
infrastructure-free: it can be imported and tested without UI, SQLite, WorkBot
or a filesystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from production.errors import (
    CorrectionSourceRequiredError,
    InvalidAttachmentMetadataError,
    InvalidLifecycleStateError,
    InvalidReadinessError,
    InvalidUtcDateTimeError,
    ProductRequiredForConfirmationError,
)


class ActorType(StrEnum):
    """Kinds of subjects that may perform an information-system action."""

    LOCAL_USER = "local_user"
    SERVER_USER = "server_user"
    SYSTEM_PROCESS = "system_process"
    INTEGRATION = "integration"


class ProductionEventType(StrEnum):
    """Initial typed facts supported by the production domain."""

    OBSERVATION = "observation"
    BASELINE = "baseline"
    CORRECTION = "correction"
    REWORK = "rework"


class ProductionEventStatus(StrEnum):
    """Lifecycle states of a production event."""

    DRAFT = "draft"
    READY = "ready"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ProductionSourceType(StrEnum):
    """Origin of a production-domain candidate or fact."""

    MANUAL = "manual"
    INTEGRATION = "integration"
    IMPORT = "import"
    SYSTEM = "system"


class ProductionInboxStatus(StrEnum):
    """Lifecycle states reserved for a future production inbox bundle."""

    COLLECTING = "collecting"
    NEEDS_DESCRIPTION = "needs_description"
    NEEDS_PRODUCT = "needs_product"
    READY = "ready"
    CONFIRMED = "confirmed"
    CHANGED = "changed"
    REJECTED = "rejected"


def _require_enum(value: object, enum_type: type[StrEnum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise InvalidLifecycleStateError(
            f"{field_name} must be a valid {enum_type.__name__} value"
        )


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for production entities."""

    return datetime.now(timezone.utc)


def require_utc_datetime(value: datetime, field_name: str) -> None:
    """Enforce the production policy: aware timestamps with a zero UTC offset."""

    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidUtcDateTimeError(
            f"{field_name} must be a timezone-aware UTC datetime"
        )
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as error:
        raise InvalidUtcDateTimeError(
            f"{field_name} must be a valid timezone-aware UTC datetime"
        ) from error
    if offset != timedelta(0):
        raise InvalidUtcDateTimeError(f"{field_name} must use UTC")


def validate_readiness(value: int | None) -> None:
    """Validate an optional readiness percentage without interpreting changes."""

    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise InvalidReadinessError("readiness_percent must be NULL or an integer 0..100")


@dataclass(frozen=True, slots=True)
class ActorRef:
    """Identity of the subject that performed an information-system action.

    `local_user_id` may point to today's local account. It is intentionally not
    an Employee ID: an Actor and an Employee remain separate concepts.
    """

    actor_type: ActorType
    display_name: str
    local_user_id: int | None = None
    uid: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_enum(self.actor_type, ActorType, "actor_type")
        if not self.display_name.strip():
            raise InvalidLifecycleStateError("Actor display_name must not be empty")
        if self.local_user_id is not None and self.local_user_id <= 0:
            raise InvalidLifecycleStateError("Actor local_user_id must be positive")


@dataclass(frozen=True, slots=True)
class ProductionStage:
    """User-editable production stage with a stable machine code."""

    code: str
    name: str
    sort_order: int = 0
    is_active: bool = True
    id: int | None = None
    uid: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.code.strip() or any(character.isspace() for character in self.code):
            raise InvalidLifecycleStateError(
                "ProductionStage code must be a non-empty machine identifier without spaces"
            )
        if not self.name.strip():
            raise InvalidLifecycleStateError("ProductionStage name must not be empty")
        if self.sort_order < 0:
            raise InvalidLifecycleStateError("ProductionStage sort_order must not be negative")


@dataclass(frozen=True, slots=True)
class ProductionEvent:
    """An immutable observed or corrected fact about one product's state."""

    event_type: ProductionEventType
    observed_at_utc: datetime
    source_type: ProductionSourceType
    created_by: ActorRef
    product_id: int | None = None
    object_id_snapshot: int | None = None
    stage_id: int | None = None
    readiness_percent: int | None = None
    description: str = ""
    recorded_at_utc: datetime = field(default_factory=utc_now)
    source_ref: str | None = None
    reported_by_employee_id: int | None = None
    status: ProductionEventStatus = ProductionEventStatus.DRAFT
    supersedes_event_id: int | None = None
    confirmed_by: ActorRef | None = None
    idempotency_key: str | None = None
    id: int | None = None
    uid: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_enum(self.event_type, ProductionEventType, "event_type")
        _require_enum(self.source_type, ProductionSourceType, "source_type")
        _require_enum(self.status, ProductionEventStatus, "status")
        validate_readiness(self.readiness_percent)
        require_utc_datetime(self.observed_at_utc, "observed_at_utc")
        require_utc_datetime(self.recorded_at_utc, "recorded_at_utc")
        if self.status is ProductionEventStatus.CONFIRMED:
            if self.product_id is None:
                raise ProductRequiredForConfirmationError(
                    "A confirmed ProductionEvent requires product_id"
                )
            if self.confirmed_by is None:
                raise InvalidLifecycleStateError(
                    "A confirmed ProductionEvent requires confirmed_by ActorRef"
                )
        elif self.confirmed_by is not None:
            raise InvalidLifecycleStateError(
                "confirmed_by is only valid for a confirmed ProductionEvent"
            )
        if (
            self.event_type is ProductionEventType.CORRECTION
            and self.supersedes_event_id is None
        ):
            raise CorrectionSourceRequiredError(
                "A correction ProductionEvent requires supersedes_event_id"
            )


@dataclass(frozen=True, slots=True)
class Attachment:
    """Infrastructure-neutral metadata for an externally stored attachment."""

    storage_key: str
    sha256: str
    original_name: str
    mime_type: str
    size_bytes: int
    received_at_utc: datetime
    width: int | None = None
    height: int | None = None
    captured_at_utc: datetime | None = None
    source_type: ProductionSourceType | None = None
    source_message_id: str | None = None
    source_attachment_id: str | None = None
    id: int | None = None
    uid: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.source_type is not None:
            _require_enum(self.source_type, ProductionSourceType, "source_type")
        _validate_storage_key(self.storage_key)
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256) is None:
            raise InvalidAttachmentMetadataError("sha256 must contain 64 hexadecimal characters")
        if not self.original_name.strip():
            raise InvalidAttachmentMetadataError("original_name must not be empty")
        if not self.mime_type.strip():
            raise InvalidAttachmentMetadataError("mime_type must not be empty")
        if self.size_bytes < 0:
            raise InvalidAttachmentMetadataError("size_bytes must not be negative")
        for field_name, value in (("width", self.width), ("height", self.height)):
            if value is not None and value <= 0:
                raise InvalidAttachmentMetadataError(f"{field_name} must be positive")
        require_utc_datetime(self.received_at_utc, "received_at_utc")
        if self.captured_at_utc is not None:
            require_utc_datetime(self.captured_at_utc, "captured_at_utc")


@dataclass(frozen=True, slots=True)
class ProductionInboxBundle:
    """Contract for a future group of incoming production messages and media."""

    status: ProductionInboxStatus
    received_at_utc: datetime
    source_type: ProductionSourceType
    source_ref: str | None = None
    description: str = ""
    product_id: int | None = None
    attachment_uids: tuple[UUID, ...] = ()
    id: int | None = None
    uid: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_enum(self.status, ProductionInboxStatus, "status")
        _require_enum(self.source_type, ProductionSourceType, "source_type")
        require_utc_datetime(self.received_at_utc, "received_at_utc")
        if self.status is ProductionInboxStatus.CONFIRMED and self.product_id is None:
            raise ProductRequiredForConfirmationError(
                "A confirmed ProductionInboxBundle requires product_id"
            )
        if (
            self.status is ProductionInboxStatus.NEEDS_DESCRIPTION
            and self.description.strip()
        ):
            raise InvalidLifecycleStateError(
                "A bundle with a description cannot be in needs_description state"
            )


def _validate_storage_key(storage_key: str) -> None:
    if not storage_key.strip() or "\\" in storage_key or ":" in storage_key:
        raise InvalidAttachmentMetadataError(
            "storage_key must be a non-empty relative POSIX key"
        )
    path = PurePosixPath(storage_key)
    if path.is_absolute() or ".." in path.parts:
        raise InvalidAttachmentMetadataError(
            "storage_key must not contain an absolute path or parent traversal"
        )
