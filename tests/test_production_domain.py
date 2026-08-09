"""Unit tests for infrastructure-independent production-domain contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from production import (
    ActorRef,
    ActorType,
    Attachment,
    CorrectProductionEvent,
    CorrectionSourceRequiredError,
    InvalidLifecycleStateError,
    InvalidReadinessError,
    InvalidUtcDateTimeError,
    ProductRequiredForConfirmationError,
    ProductionEvent,
    ProductionEventStatus,
    ProductionEventType,
    ProductionInboxBundle,
    ProductionInboxStatus,
    ProductionSourceType,
    ProductionStage,
)


UTC_TIME = datetime(2026, 8, 9, 12, 30, tzinfo=timezone.utc)


def _actor() -> ActorRef:
    return ActorRef(
        actor_type=ActorType.LOCAL_USER,
        display_name="Local administrator",
        local_user_id=7,
    )


def _event(**overrides: object) -> ProductionEvent:
    values: dict[str, object] = {
        "event_type": ProductionEventType.OBSERVATION,
        "observed_at_utc": UTC_TIME,
        "source_type": ProductionSourceType.MANUAL,
        "created_by": _actor(),
    }
    values.update(overrides)
    return ProductionEvent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("readiness", [None, 0, 100])
def test_readiness_accepts_null_and_boundaries(readiness: int | None) -> None:
    assert _event(readiness_percent=readiness).readiness_percent == readiness


@pytest.mark.parametrize("readiness", [-1, 101])
def test_readiness_rejects_values_outside_range(readiness: int) -> None:
    with pytest.raises(InvalidReadinessError):
        _event(readiness_percent=readiness)


def test_confirmed_event_requires_product() -> None:
    with pytest.raises(ProductRequiredForConfirmationError):
        _event(status=ProductionEventStatus.CONFIRMED, confirmed_by=_actor())


def test_draft_event_may_exist_before_product_is_known() -> None:
    event = _event(product_id=None, stage_id=None, readiness_percent=None)

    assert event.status is ProductionEventStatus.DRAFT
    assert event.product_id is None


def test_new_production_entities_receive_uuid_v4() -> None:
    entities = [
        _actor(),
        ProductionStage(code="assembly", name="Assembly"),
        _event(),
        Attachment(
            storage_key="2026/08/photo.jpg",
            sha256="a" * 64,
            original_name="photo.jpg",
            mime_type="image/jpeg",
            size_bytes=123,
            received_at_utc=UTC_TIME,
        ),
        ProductionInboxBundle(
            status=ProductionInboxStatus.COLLECTING,
            received_at_utc=UTC_TIME,
            source_type=ProductionSourceType.INTEGRATION,
        ),
    ]

    assert all(isinstance(entity.uid, UUID) and entity.uid.version == 4 for entity in entities)


def test_uid_is_immutable_after_creation() -> None:
    stage = ProductionStage(code="wiring", name="Wiring")

    with pytest.raises(FrozenInstanceError):
        stage.uid = UUID("00000000-0000-4000-8000-000000000001")  # type: ignore[misc]


def test_actor_identity_is_not_employee_identity() -> None:
    actor = _actor()

    assert actor.local_user_id == 7
    assert not hasattr(actor, "employee_id")


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(InvalidUtcDateTimeError):
        _event(observed_at_utc=datetime(2026, 8, 9, 12, 30))


def test_non_utc_aware_datetime_is_rejected() -> None:
    moscow = timezone(timedelta(hours=3))

    with pytest.raises(InvalidUtcDateTimeError):
        _event(observed_at_utc=datetime(2026, 8, 9, 15, 30, tzinfo=moscow))


def test_utc_datetime_is_accepted() -> None:
    assert _event(observed_at_utc=UTC_TIME).observed_at_utc == UTC_TIME


def test_correction_event_requires_original_event_reference() -> None:
    with pytest.raises(CorrectionSourceRequiredError):
        _event(event_type=ProductionEventType.CORRECTION)


def test_correction_command_requires_original_event_reference() -> None:
    with pytest.raises(CorrectionSourceRequiredError):
        CorrectProductionEvent(
            actor=_actor(),
            observed_at_utc=UTC_TIME,
            source_event_id=None,
            source_type=ProductionSourceType.MANUAL,
        )


def test_confirmed_event_records_separate_actor() -> None:
    creator = _actor()
    confirmer = ActorRef(ActorType.LOCAL_USER, "Production manager", local_user_id=8)

    event = _event(
        product_id=42,
        status=ProductionEventStatus.CONFIRMED,
        created_by=creator,
        confirmed_by=confirmer,
    )

    assert event.created_by.uid != event.confirmed_by.uid


def test_invalid_confirmation_actor_state_is_rejected() -> None:
    with pytest.raises(InvalidLifecycleStateError):
        _event(status=ProductionEventStatus.DRAFT, confirmed_by=_actor())


def test_unknown_lifecycle_state_is_rejected() -> None:
    with pytest.raises(InvalidLifecycleStateError):
        _event(status="unknown")


def test_production_package_has_no_forbidden_infrastructure_imports() -> None:
    package_path = Path(__file__).parents[1] / "production"
    domain_files = {
        "__init__.py",
        "commands.py",
        "errors.py",
        "models.py",
        "queries.py",
    }
    forbidden_roots = {"PySide6", "sqlite3", "ui", "workbot"}
    imported_roots: set[str] = set()

    for source_path in (package_path / name for name in domain_files):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(forbidden_roots)
