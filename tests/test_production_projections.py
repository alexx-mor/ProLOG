"""Read-model and reconciliation tests for production projections P5."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from models import Employee, ProductItem, ProductStatus, WorkLogEntry
from production.attachment_repository import AttachmentRepository
from production.commands import (
    ConfirmProductionEvent,
    CorrectProductionEvent,
    CreateProductionEvent,
    RejectProductionEvent,
)
from production.event_repository import ProductionEventRepository
from production.event_service import ProductionService
from production.models import (
    ActorRef,
    ActorType,
    Attachment,
    ProductionEvent,
    ProductionEventStatus,
    ProductionEventType,
    ProductionSourceType,
    WorkLogRelationType,
)
from production.projection_models import (
    ProjectionDiagnosticKind,
    ReadinessSource,
)
from production.projections import ProductionProjectionService
from production.repository import ProductionStageRepository


UTC = timezone.utc


@dataclass(slots=True)
class ProjectionContext:
    database: Database
    directories: DirectoryRepository
    employees: EmployeeRepository
    worklogs: WorkLogRepository
    attachments: AttachmentRepository
    events: ProductionEventRepository
    stages: ProductionStageRepository
    projections: ProductionProjectionService
    service: ProductionService
    actor: ActorRef
    product_id: int
    object_id: int
    employee_id: int
    now: list[datetime]


@pytest.fixture
def context(tmp_path: Path) -> ProjectionContext:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories = DirectoryRepository(database)
    employees = EmployeeRepository(database)
    worklogs = WorkLogRepository(database)
    attachments = AttachmentRepository(database)
    events = ProductionEventRepository(database)
    stages = ProductionStageRepository(database)
    object_id = directories.upsert("objects", "Проекционный объект")
    product_id = directories.save_product(
        ProductItem(
            object_id=object_id,
            name="ШУ проекционный",
            readiness_percent=17,
            product_status=ProductStatus.PAUSED.value,
        )
    )
    employee_id = employees.save(Employee("Петров Петр Петрович"))
    actor = ActorRef(ActorType.LOCAL_USER, "Руководитель", local_user_id=7)
    now = [datetime(2026, 8, 10, 8, tzinfo=UTC)]
    projections = ProductionProjectionService(
        events,
        stages,
        attachments,
        directories,
        employees,
        worklogs,
    )
    service = ProductionService(
        events,
        stages,
        attachments,
        directories,
        employees,
        worklogs,
        clock=lambda: now[0],
        projection_service=projections,
    )
    return ProjectionContext(
        database,
        directories,
        employees,
        worklogs,
        attachments,
        events,
        stages,
        projections,
        service,
        actor,
        product_id,
        object_id,
        employee_id,
        now,
    )


def _time(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def _create(
    context: ProjectionContext,
    day: int,
    *,
    readiness: int | None = None,
    stage_id: int | None = None,
    description: str = "Наблюдение",
    event_type: ProductionEventType = ProductionEventType.OBSERVATION,
    product_id: int | None = None,
    change_reason: str = "",
) -> ProductionEvent:
    return context.service.create_event(
        CreateProductionEvent(
            actor=context.actor,
            observed_at_utc=_time(day),
            source_type=ProductionSourceType.MANUAL,
            product_id=context.product_id if product_id is None else product_id,
            stage_id=stage_id,
            readiness_percent=readiness,
            description=description,
            event_type=event_type,
            reported_by_employee_id=context.employee_id,
            change_reason=change_reason,
        )
    )


def _confirm(context: ProjectionContext, event: ProductionEvent) -> ProductionEvent:
    assert event.id is not None
    context.service.mark_ready(event.id)
    return context.service.confirm_event(
        ConfirmProductionEvent(event.id, context.actor, context.now[0])
    )


def _correct(
    context: ProjectionContext,
    source: ProductionEvent,
    *,
    readiness: int | None,
) -> ProductionEvent:
    assert source.id is not None
    correction = context.service.correct_event(
        CorrectProductionEvent(
            actor=context.actor,
            observed_at_utc=source.observed_at_utc,
            source_event_id=source.id,
            source_type=ProductionSourceType.MANUAL,
            product_id=context.product_id,
            stage_id=source.stage_id,
            readiness_percent=readiness,
            description="Исправленная оценка",
            reported_by_employee_id=context.employee_id,
            reason="Ошибка исходного наблюдения",
        )
    )
    return _confirm(context, correction)


def test_product_without_events_uses_marked_legacy_readiness(
    context: ProjectionContext,
) -> None:
    state = context.projections.get_product_state(context.product_id)

    assert state.readiness_percent == 17
    assert state.readiness_source is ReadinessSource.LEGACY_SNAPSHOT
    assert state.event_count == 0
    assert state.latest_effective_event_id is None


def test_stage_only_event_preserves_legacy_fallback(context: ProjectionContext) -> None:
    stage = context.stages.list_active()[0]
    _confirm(context, _create(context, 1, stage_id=stage.id))

    state = context.projections.get_product_state(context.product_id)
    assert state.current_stage_id == stage.id
    assert state.readiness_percent == 17
    assert state.readiness_source is ReadinessSource.LEGACY_SNAPSHOT


def test_first_event_readiness_switches_source_and_snapshot(
    context: ProjectionContext,
) -> None:
    _confirm(context, _create(context, 1, readiness=30))

    state = context.projections.get_product_state(context.product_id)
    product = context.directories.get_product(context.product_id)
    assert state.readiness_percent == 30
    assert state.readiness_source is ReadinessSource.PRODUCTION_EVENT
    assert product is not None and product.readiness_percent == 30


def test_field_wise_projection_does_not_erase_known_values(
    context: ProjectionContext,
) -> None:
    first_stage, second_stage = context.stages.list_active()[:2]
    _confirm(context, _create(context, 1, readiness=40, stage_id=first_stage.id))
    _confirm(context, _create(context, 2, description="Только комментарий"))
    _confirm(context, _create(context, 3, stage_id=second_stage.id))

    state = context.projections.get_product_state(context.product_id)
    assert state.readiness_percent == 40
    assert state.current_stage_id == second_stage.id
    assert state.latest_effective_event_id is not None
    assert state.last_observed_at_utc == _time(3)


@pytest.mark.parametrize("readiness", [0, 100])
def test_readiness_boundary_values_are_projected(
    context: ProjectionContext,
    readiness: int,
) -> None:
    _confirm(context, _create(context, 1, readiness=readiness))
    assert context.projections.get_product_state(context.product_id).readiness_percent == readiness


def test_rework_can_reduce_current_readiness(context: ProjectionContext) -> None:
    _confirm(context, _create(context, 1, readiness=80))
    _confirm(
        context,
        _create(
            context,
            2,
            readiness=50,
            event_type=ProductionEventType.REWORK,
        ),
    )
    assert context.projections.get_product_state(context.product_id).readiness_percent == 50


def test_events_use_observed_time_not_recording_time(context: ProjectionContext) -> None:
    context.now[0] = _time(10)
    later = _confirm(context, _create(context, 7, readiness=80))
    context.now[0] = _time(11)
    earlier = _confirm(context, _create(context, 5, readiness=50))

    effective = context.projections.get_effective_events(context.product_id)
    assert [event.id for event in effective] == [earlier.id, later.id]
    assert context.projections.get_product_state(context.product_id).readiness_percent == 80


def test_backdated_event_is_inserted_without_becoming_current(
    context: ProjectionContext,
) -> None:
    first = _confirm(context, _create(context, 5, readiness=30))
    last = _confirm(context, _create(context, 7, readiness=80))
    backdated = _confirm(context, _create(context, 6, readiness=60))

    effective = context.projections.get_effective_events(context.product_id)
    assert [event.id for event in effective] == [first.id, backdated.id, last.id]
    assert context.projections.get_product_state(context.product_id).readiness_percent == 80


def test_equal_observed_time_uses_recorded_time_and_id_tie_breaker(
    context: ProjectionContext,
) -> None:
    context.now[0] = _time(8, 8)
    first = _confirm(context, _create(context, 5, readiness=20))
    context.now[0] = _time(8, 9)
    second = _confirm(context, _create(context, 5, readiness=30))

    effective = context.projections.get_effective_events(context.product_id)
    assert [event.id for event in effective] == [first.id, second.id]
    assert context.projections.get_product_state(context.product_id).readiness_percent == 30


def test_correction_of_earlier_event_does_not_override_later_observation(
    context: ProjectionContext,
) -> None:
    _confirm(context, _create(context, 1, readiness=30))
    middle = _confirm(context, _create(context, 3, readiness=70))
    _confirm(context, _create(context, 5, readiness=90))

    correction = _correct(context, middle, readiness=60)
    state = context.projections.get_product_state(context.product_id)
    assert correction.status is ProductionEventStatus.CONFIRMED
    assert state.readiness_percent == 90


def test_correction_chain_is_reproducible(context: ProjectionContext) -> None:
    original = _confirm(context, _create(context, 2, readiness=30))
    correction_b = _correct(context, original, readiness=40)
    correction_c = _correct(context, correction_b, readiness=45)

    timeline = context.projections.get_product_timeline(context.product_id)
    assert [item.event.status for item in timeline] == [
        ProductionEventStatus.SUPERSEDED,
        ProductionEventStatus.SUPERSEDED,
        ProductionEventStatus.CONFIRMED,
    ]
    assert [item.is_effective for item in timeline] == [False, False, True]
    assert timeline[0].superseded_by_event_id == correction_b.id
    assert timeline[1].superseded_by_event_id == correction_c.id
    assert context.projections.get_product_state(context.product_id).readiness_percent == 45


def test_timeline_default_and_audit_statuses(context: ProjectionContext) -> None:
    confirmed = _confirm(context, _create(context, 1, readiness=20))
    draft = _create(context, 2, readiness=30)
    rejected = _create(context, 3, readiness=40)
    assert rejected.id is not None
    context.service.reject_event(
        RejectProductionEvent(rejected.id, context.actor, context.now[0], "Не подтверждено")
    )

    default = context.projections.get_product_timeline(context.product_id)
    audit = context.projections.get_product_timeline(
        context.product_id,
        include_audit=True,
    )
    assert [item.event.id for item in default] == [confirmed.id]
    assert {item.event.id for item in audit} == {confirmed.id, draft.id, rejected.id}


def test_timeline_contains_ordered_attachment_metadata_actor_employee_and_worklog(
    context: ProjectionContext,
) -> None:
    first = context.attachments.create(
        Attachment(
            storage_key="aa/bb/first.jpg",
            sha256="a" * 64,
            original_name="first.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
            width=10,
            height=20,
            received_at_utc=context.now[0],
        )
    )
    second = context.attachments.create(
        Attachment(
            storage_key="cc/dd/second.png",
            sha256="b" * 64,
            original_name="second.png",
            mime_type="image/png",
            size_bytes=20,
            width=30,
            height=40,
            received_at_utc=context.now[0],
        )
    )
    worklog_id = context.worklogs.save(
        WorkLogEntry(
            employee_id=context.employee_id,
            work_date=date(2026, 8, 2),
            location_id=None,
            object_id=context.object_id,
            product_id=context.product_id,
            work_type_id=None,
            description="Работа по изделию",
            hours=8,
        )
    )
    event = _create(context, 2, readiness=30)
    assert event.id is not None and first.id is not None and second.id is not None
    context.service.attach_existing_attachment(event.id, second.id, sort_order=2)
    context.service.attach_existing_attachment(event.id, first.id, sort_order=1)
    context.service.link_worklog(
        event.id,
        worklog_id,
        context.actor,
        WorkLogRelationType.MANUAL,
    )
    _confirm(context, event)

    item = context.projections.get_product_timeline(context.product_id)[0]
    assert [entry.attachment.id for entry in item.attachments] == [first.id, second.id]
    assert item.reported_employee_name == "Петров Петр Петрович"
    assert item.event.created_by.uid == context.actor.uid
    assert item.event.confirmed_by is not None
    assert item.worklogs[0].worklog.id == worklog_id


def test_snapshot_mismatch_is_diagnosed_and_reconciled_without_event_changes(
    context: ProjectionContext,
) -> None:
    event = _confirm(context, _create(context, 1, readiness=70))
    before = context.events.get_by_id(event.id or 0)
    context.directories.update_product_readiness_snapshot(context.product_id, 12)

    report = context.projections.diagnose_product_projection(context.product_id)
    assert report.count(ProjectionDiagnosticKind.SNAPSHOT_MISMATCH) == 1
    context.projections.reconcile_product_snapshot(context.product_id)
    product = context.directories.get_product(context.product_id)
    assert product is not None and product.readiness_percent == 70
    assert context.events.get_by_id(event.id or 0) == before


def test_snapshot_sync_failure_does_not_rollback_confirmed_fact(
    context: ProjectionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _create(context, 1, readiness=65)
    assert event.id is not None
    context.service.mark_ready(event.id)
    monkeypatch.setattr(
        context.projections,
        "reconcile_product_snapshot",
        lambda product_id: (_ for _ in ()).throw(RuntimeError("snapshot unavailable")),
    )

    confirmed = context.service.confirm_event(
        ConfirmProductionEvent(event.id, context.actor, context.now[0])
    )
    assert confirmed.status is ProductionEventStatus.CONFIRMED
    assert context.events.get_by_id(event.id).status is ProductionEventStatus.CONFIRMED


def test_product_status_is_never_derived_from_stage_or_readiness(
    context: ProjectionContext,
) -> None:
    before = context.directories.get_product(context.product_id)
    assert before is not None and before.product_status == ProductStatus.PAUSED.value
    completed = context.stages.get_by_code("COMPLETED")
    assert completed is not None
    _confirm(context, _create(context, 1, readiness=100, stage_id=completed.id))

    after = context.directories.get_product(context.product_id)
    assert after is not None and after.product_status == ProductStatus.PAUSED.value


def test_inactive_stage_remains_visible_and_current(context: ProjectionContext) -> None:
    stage = context.stages.list_active()[0]
    assert stage.id is not None
    _confirm(context, _create(context, 1, stage_id=stage.id))
    context.stages.set_active(stage.id, False, updated_at_utc=context.now[0])

    state = context.projections.get_product_state(context.product_id)
    timeline = context.projections.get_product_timeline(context.product_id)
    report = context.projections.diagnose_product_projection(context.product_id)
    assert state.current_stage_id == stage.id
    assert timeline[0].stage is not None and not timeline[0].stage.is_active
    assert stage.id not in {item.id for item in context.stages.list_active()}
    assert report.count(ProjectionDiagnosticKind.STAGE_INACTIVE) == 1


def test_labor_intervals_aggregate_only_target_product(context: ProjectionContext) -> None:
    second_product_id = context.directories.save_product(
        ProductItem(object_id=context.object_id, name="Другое изделие")
    )
    _confirm(context, _create(context, 1, readiness=20))
    _confirm(context, _create(context, 4, readiness=50))
    second_employee_id = context.employees.save(Employee("Сидоров Сидор Сидорович"))
    expected_ids = []
    for employee_id, day, hours in (
        (context.employee_id, 2, 8),
        (second_employee_id, 3, 6),
    ):
        expected_ids.append(
            context.worklogs.save(
                WorkLogEntry(
                    employee_id=employee_id,
                    work_date=date(2026, 8, day),
                    location_id=None,
                    object_id=context.object_id,
                    product_id=context.product_id,
                    work_type_id=None,
                    description="Изготовление",
                    hours=hours,
                )
            )
        )
    context.worklogs.save(
        WorkLogEntry(
            employee_id=context.employee_id,
            work_date=date(2026, 8, 2),
            location_id=None,
            object_id=context.object_id,
            product_id=second_product_id,
            work_type_id=None,
            description="Чужое изделие",
            hours=10,
        )
    )

    interval = context.projections.get_labor_intervals(context.product_id)[0]
    assert set(interval.worklog_ids) == set(expected_ids)
    assert interval.worklog_count == 2
    assert interval.employee_count == 2
    assert interval.total_hours == interval.person_hours == 14
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ProductionEventWorkLogs").fetchone()[0] == 0


def test_same_day_labor_interval_is_marked_ambiguous_without_distribution(
    context: ProjectionContext,
) -> None:
    first = _confirm(context, _create(context, 2, readiness=20))
    context.now[0] = _time(2, 15)
    second = context.service.create_event(
        CreateProductionEvent(
            actor=context.actor,
            observed_at_utc=_time(2, 16),
            source_type=ProductionSourceType.MANUAL,
            product_id=context.product_id,
            readiness_percent=30,
        )
    )
    _confirm(context, second)
    context.worklogs.save(
        WorkLogEntry(
            employee_id=context.employee_id,
            work_date=date(2026, 8, 2),
            location_id=None,
            object_id=context.object_id,
            product_id=context.product_id,
            work_type_id=None,
            description="Суточная работа",
            hours=8,
        )
    )

    interval = context.projections.get_labor_intervals(context.product_id)[0]
    assert interval.previous_event_id == first.id
    assert interval.day_granularity_ambiguous
    assert interval.worklog_count == 0
    assert interval.person_hours == 0


def test_missing_product_reference_is_structurally_diagnosed(
    context: ProjectionContext,
) -> None:
    event = _confirm(context, _create(context, 1, readiness=25))
    assert event.id is not None
    with context.database.connect() as connection:
        connection.execute("DELETE FROM products_db.Products WHERE id = ?", (context.product_id,))

    report = context.projections.diagnose_all_product_snapshots()
    assert report.count(ProjectionDiagnosticKind.PRODUCT_MISSING) == 1


def test_p5_adds_no_persistent_projection_tables(context: ProjectionContext) -> None:
    with context.database.connect() as connection:
        names = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations WHERE component = 'prolog'"
        ).fetchone()[0]
    assert not names & {"ProductCurrentState", "ProductTimeline", "ProductAnalytics"}
    assert version == 5


def test_projection_layer_has_no_ui_workbot_sqlite_or_filesystem_imports() -> None:
    root = Path(__file__).parents[1]
    imported = set()
    for name in ("projection_models.py", "projections.py"):
        tree = ast.parse((root / "production" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(name.startswith("PySide6") for name in imported)
    assert "sqlite3" not in imported
    assert not any("workbot" in name.lower() for name in imported)
    assert not any("attachment_store" in name for name in imported)
