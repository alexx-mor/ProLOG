"""Persistence and application-service tests for ProductionEvent P4."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from dataclasses import FrozenInstanceError, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from models import Employee, ProductItem, WorkLogEntry
from production.attachment_repository import AttachmentRepository
from production.attachment_service import AttachmentService
from production.commands import (
    ConfirmProductionEvent,
    CorrectProductionEvent,
    CreateProductionEvent,
    RejectProductionEvent,
)
from production.errors import (
    InvalidProductionCorrectionError,
    InvalidProductionTransitionError,
    ObjectSnapshotManagedError,
    ProductionEventIdempotencyConflictError,
    ProductionEventImmutableError,
    ProductionReferenceNotFoundError,
    UnexplainedReadinessDecreaseError,
)
from production.event_repository import ProductionEventRepository
from production.event_service import ProductionService
from production.local_attachment_store import LocalAttachmentStore
from production.migrations import apply_production_events_migration
from production.models import (
    ActorRef,
    ActorType,
    ProductionEvent,
    ProductionEventStatus,
    ProductionEventType,
    ProductionSourceType,
    WorkLogRelationType,
)
from production.repository import ProductionStageRepository
from schema_migrations import Migration, MigrationComponent, MigrationRunner


UTC_TIME = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


@dataclass(slots=True)
class EventContext:
    database: Database
    directories: DirectoryRepository
    employees: EmployeeRepository
    worklogs: WorkLogRepository
    attachments: AttachmentRepository
    events: ProductionEventRepository
    service: ProductionService
    store: LocalAttachmentStore
    actor: ActorRef
    employee_id: int
    object_id: int
    product_id: int
    worklog_id: int
    stage_id: int


@pytest.fixture
def context(tmp_path: Path) -> EventContext:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories = DirectoryRepository(database)
    employees = EmployeeRepository(database)
    worklogs = WorkLogRepository(database)
    attachments = AttachmentRepository(database)
    events = ProductionEventRepository(database)
    object_id = directories.upsert("objects", "Тестовый объект")
    product_id = directories.save_product(
        ProductItem(
            object_id=object_id,
            name="Шкаф управления",
            readiness_percent=17,
        )
    )
    employee_id = employees.save(Employee("Иванов Иван Иванович"))
    worklog_id = worklogs.save(
        WorkLogEntry(
            employee_id=employee_id,
            work_date=date(2026, 8, 9),
            location_id=None,
            object_id=object_id,
            product_id=product_id,
            work_type_id=None,
            description="Сборка шкафа",
            hours=8,
        )
    )
    stage = ProductionStageRepository(database).list_active()[0]
    assert stage.id is not None
    actor = ActorRef(
        ActorType.LOCAL_USER,
        "Администратор производства",
        local_user_id=11,
    )
    store = LocalAttachmentStore(tmp_path / "attachments")
    service = ProductionService(
        events,
        ProductionStageRepository(database),
        attachments,
        directories,
        employees,
        worklogs,
        clock=lambda: UTC_TIME,
    )
    return EventContext(
        database,
        directories,
        employees,
        worklogs,
        attachments,
        events,
        service,
        store,
        actor,
        employee_id,
        object_id,
        product_id,
        worklog_id,
        stage.id,
    )


def _create(
    context: EventContext,
    *,
    readiness: int | None = None,
    product_id: int | None | object = ...,
    event_type: ProductionEventType = ProductionEventType.OBSERVATION,
    stage_id: int | None | object = ...,
    description: str = "Наблюдение",
    change_reason: str = "",
    idempotency_key: str | None = None,
    source_ref: str | None = None,
) -> ProductionEvent:
    actual_product = context.product_id if product_id is ... else product_id
    actual_stage = context.stage_id if stage_id is ... else stage_id
    return context.service.create_event(
        CreateProductionEvent(
            actor=context.actor,
            observed_at_utc=UTC_TIME,
            source_type=ProductionSourceType.MANUAL,
            product_id=actual_product,  # type: ignore[arg-type]
            stage_id=actual_stage,  # type: ignore[arg-type]
            readiness_percent=readiness,
            event_type=event_type,
            description=description,
            change_reason=change_reason,
            idempotency_key=idempotency_key,
            source_ref=source_ref,
            reported_by_employee_id=context.employee_id,
        )
    )


def _confirm(context: EventContext, event: ProductionEvent) -> ProductionEvent:
    assert event.id is not None
    context.service.mark_ready(event.id)
    return context.service.confirm_event(
        ConfirmProductionEvent(event.id, context.actor, UTC_TIME)
    )


def _downgrade_core_to_v3(database: Database) -> None:
    with database.connect() as connection:
        connection.execute("DROP TABLE ProductionEventAttachments")
        connection.execute("DROP TABLE ProductionEventWorkLogs")
        connection.execute("DROP TABLE ProductionEvents")
        connection.execute(
            "DELETE FROM SchemaMigrations WHERE component = 'prolog' AND version = 4"
        )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migration_v3_to_v4_creates_exact_tables_and_foreign_keys(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_core_to_v3(database)

    database.initialize()

    assert {item.component: item.current_version for item in database.schema_versions()} == {
        "prolog": 4,
        "employees": 1,
        "objects": 1,
        "products": 1,
        "aliases": 1,
    }
    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(ProductionEvents)")
        }
        attachment_fks = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in connection.execute(
                "PRAGMA foreign_key_list(ProductionEventAttachments)"
            )
        }
        worklog_fks = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in connection.execute("PRAGMA foreign_key_list(ProductionEventWorkLogs)")
        }
    assert {
        "ProductionEvents",
        "ProductionEventAttachments",
        "ProductionEventWorkLogs",
    } <= tables
    assert {
        "uid",
        "product_id",
        "object_id_snapshot",
        "stage_id",
        "event_type",
        "readiness_percent",
        "change_reason",
        "created_actor_type",
        "created_actor_uid",
        "created_actor_local_user_id",
        "created_actor_display_name_snapshot",
        "confirmed_actor_type",
        "rejected_actor_type",
    } <= event_columns
    assert ("production_event_id", "ProductionEvents", "id", "CASCADE") in attachment_fks
    assert ("attachment_id", "Attachments", "id", "RESTRICT") in attachment_fks
    assert ("production_event_id", "ProductionEvents", "id", "CASCADE") in worklog_fks
    assert ("worklog_entry_id", "WorkLogEntries", "id", "CASCADE") in worklog_fks


def test_v4_migration_retry_preserves_legacy_and_attachment_metadata(
    context: EventContext,
) -> None:
    attachment = AttachmentService(context.attachments, context.store).store_bytes(
        b"existing attachment",
        original_name="existing.bin",
        received_at_utc=UTC_TIME,
    )
    before_attachment = context.attachments.get_by_id(attachment.id or 0)
    with context.database.connect() as connection:
        before_worklogs = [
            tuple(row) for row in connection.execute("SELECT * FROM WorkLogEntries")
        ]
    _downgrade_core_to_v3(context.database)

    context.database.initialize()
    context.database.initialize()

    with context.database.connect() as connection:
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM WorkLogEntries")
        ] == before_worklogs
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM SchemaMigrations WHERE version = 4"
        ).fetchone()[0] == 1
    assert context.attachments.get_by_id(attachment.id or 0) == before_attachment


def test_v4_migration_rolls_back_on_artificial_failure(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "rollback-v4.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("CREATE TABLE ProductionStages(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE Attachments(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE WorkLogEntries(id INTEGER PRIMARY KEY)")
    component = MigrationComponent("prolog")
    migrations = tuple(
        Migration(version, f"v{version}", f"p4-rollback-v{version}", lambda _c: None)
        for version in range(1, 4)
    )
    MigrationRunner((component,), migrations, app_version="test").migrate(connection)

    def fail_after_schema(active: sqlite3.Connection) -> None:
        apply_production_events_migration(active)
        raise RuntimeError("artificial P4 failure")

    runner = MigrationRunner(
        (component,),
        (*migrations, Migration(4, "Events", "p4-rollback-v4", fail_after_schema)),
        app_version="test",
    )
    with pytest.raises(RuntimeError, match="artificial P4 failure"):
        runner.migrate(connection)
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    history = [
        row[0]
        for row in connection.execute("SELECT version FROM SchemaMigrations ORDER BY version")
    ]
    connection.close()
    assert "ProductionEvents" not in tables
    assert history == [1, 2, 3]


def test_v4_migration_does_not_change_component_files(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_core_to_v3(database)
    component_paths = {
        key: path for key, path in database.database_paths().items() if key != "prolog"
    }
    before = {key: _file_hash(path) for key, path in component_paths.items()}

    database.initialize()

    assert {key: _file_hash(path) for key, path in component_paths.items()} == before


@pytest.mark.parametrize("readiness", [None, 0, 100])
def test_draft_creation_accepts_readiness_boundaries_and_generates_uuid_v4(
    context: EventContext,
    readiness: int | None,
) -> None:
    event = _create(context, readiness=readiness, product_id=None, stage_id=None)

    assert event.status is ProductionEventStatus.DRAFT
    assert event.product_id is None
    assert isinstance(event.uid, UUID) and event.uid.version == 4
    with pytest.raises(FrozenInstanceError):
        event.uid = UUID("00000000-0000-4000-8000-000000000001")  # type: ignore[misc]


def test_nonexistent_references_and_managed_snapshot_are_rejected(
    context: EventContext,
) -> None:
    with pytest.raises(ProductionReferenceNotFoundError, match="этап"):
        context.service.create_event(
            CreateProductionEvent(
                context.actor,
                UTC_TIME,
                ProductionSourceType.MANUAL,
                stage_id=999999,
            )
        )
    with pytest.raises(ObjectSnapshotManagedError):
        context.service.create_event(
            CreateProductionEvent(
                context.actor,
                UTC_TIME,
                ProductionSourceType.MANUAL,
                product_id=context.product_id,
                object_id_snapshot=context.object_id,
            )
        )


def test_draft_ready_confirmed_and_object_snapshot_is_stable(context: EventContext) -> None:
    event = _create(context, readiness=35)
    assert event.id is not None

    ready = context.service.mark_ready(event.id)
    confirmed = context.service.confirm_event(
        ConfirmProductionEvent(event.id, context.actor, UTC_TIME)
    )

    assert ready.status is ProductionEventStatus.READY
    assert confirmed.status is ProductionEventStatus.CONFIRMED
    assert confirmed.object_id_snapshot == context.object_id
    second_object = context.directories.upsert("objects", "Новый объект")
    product = context.directories.get_product(context.product_id)
    assert product is not None
    product.object_id = second_object
    context.directories.save_product(product)
    assert context.service.get_event(event.id).object_id_snapshot == context.object_id


def test_confirmation_requires_existing_product(context: EventContext) -> None:
    event = _create(context, product_id=None)
    assert event.id is not None
    context.service.mark_ready(event.id)

    with pytest.raises(ProductionReferenceNotFoundError, match="изделие"):
        context.service.confirm_event(
            ConfirmProductionEvent(event.id, context.actor, UTC_TIME)
        )


def test_rejection_and_invalid_lifecycle_transitions(context: EventContext) -> None:
    draft = _create(context)
    assert draft.id is not None
    rejected = context.service.reject_event(
        RejectProductionEvent(draft.id, context.actor, UTC_TIME, "Ошибка ввода")
    )

    assert rejected.status is ProductionEventStatus.REJECTED
    assert rejected.rejected_by == context.actor
    assert rejected.rejection_reason == "Ошибка ввода"
    with pytest.raises(InvalidProductionTransitionError):
        context.service.mark_ready(draft.id)
    with pytest.raises(InvalidProductionTransitionError):
        context.service.confirm_event(
            ConfirmProductionEvent(draft.id, context.actor, UTC_TIME)
        )


def test_confirmed_business_fields_are_immutable_in_sql(context: EventContext) -> None:
    event = _confirm(context, _create(context, readiness=40))
    assert event.id is not None

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with context.database.connect() as connection:
            connection.execute(
                "UPDATE ProductionEvents SET description = 'changed' WHERE id = ?",
                (event.id,),
            )
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        with context.database.connect() as connection:
            connection.execute("DELETE FROM ProductionEvents WHERE id = ?", (event.id,))
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
        with context.database.connect() as connection:
            connection.execute(
                "UPDATE ProductionEvents SET status = 'draft' WHERE id = ?",
                (event.id,),
            )


def test_correction_draft_keeps_original_confirmed_until_atomic_confirmation(
    context: EventContext,
) -> None:
    original = _confirm(context, _create(context, readiness=70, description="Ошибочно"))
    assert original.id is not None
    original_before = context.events.get_by_id(original.id)
    correction = context.service.correct_event(
        CorrectProductionEvent(
            context.actor,
            UTC_TIME,
            original.id,
            ProductionSourceType.MANUAL,
            product_id=context.product_id,
            stage_id=context.stage_id,
            readiness_percent=55,
            description="Исправлено",
            reason="Исправление оценки",
        )
    )

    assert correction.id != original.id and correction.uid != original.uid
    assert context.service.get_event(original.id).status is ProductionEventStatus.CONFIRMED
    assert correction.status is ProductionEventStatus.DRAFT
    corrected = _confirm(context, correction)
    original_after = context.service.get_event(original.id)

    assert corrected.status is ProductionEventStatus.CONFIRMED
    assert original_after.status is ProductionEventStatus.SUPERSEDED
    assert original_before is not None
    assert original_after.description == original_before.description
    assert original_after.readiness_percent == original_before.readiness_percent
    assert context.events.find_superseding_event(original.id) == corrected


def test_correction_confirmation_rolls_back_both_statuses_on_failure(
    context: EventContext,
) -> None:
    original = _confirm(context, _create(context, readiness=70))
    assert original.id is not None
    correction = context.service.correct_event(
        CorrectProductionEvent(
            context.actor,
            UTC_TIME,
            original.id,
            ProductionSourceType.MANUAL,
            product_id=context.product_id,
            stage_id=context.stage_id,
            readiness_percent=60,
            reason="Исправление",
        )
    )
    assert correction.id is not None
    context.service.mark_ready(correction.id)
    with context.database.connect() as connection:
        connection.execute(
            f"""
            CREATE TRIGGER fail_p4_source_update
            BEFORE UPDATE OF status ON ProductionEvents
            WHEN OLD.id = {original.id} AND NEW.status = 'superseded'
            BEGIN SELECT RAISE(ABORT, 'artificial correction failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="artificial correction failure"):
        context.service.confirm_event(
            ConfirmProductionEvent(correction.id, context.actor, UTC_TIME)
        )

    assert context.service.get_event(original.id).status is ProductionEventStatus.CONFIRMED
    assert context.service.get_event(correction.id).status is ProductionEventStatus.READY


def test_invalid_correction_sources_and_repeat_supersede_are_rejected(
    context: EventContext,
) -> None:
    rejected = _create(context)
    assert rejected.id is not None
    context.service.reject_event(
        RejectProductionEvent(rejected.id, context.actor, UTC_TIME, "reject")
    )
    with pytest.raises(InvalidProductionCorrectionError):
        context.service.correct_event(
            CorrectProductionEvent(
                context.actor,
                UTC_TIME,
                rejected.id,
                ProductionSourceType.MANUAL,
                product_id=context.product_id,
            )
        )

    original = _confirm(context, _create(context, readiness=50))
    assert original.id is not None
    first = context.service.correct_event(
        CorrectProductionEvent(
            context.actor,
            UTC_TIME,
            original.id,
            ProductionSourceType.MANUAL,
            product_id=context.product_id,
            readiness_percent=45,
            reason="correction",
        )
    )
    _confirm(context, first)
    with pytest.raises(InvalidProductionCorrectionError):
        context.service.correct_event(
            CorrectProductionEvent(
                context.actor,
                UTC_TIME,
                original.id,
                ProductionSourceType.MANUAL,
                product_id=context.product_id,
            )
        )


def test_multilevel_correction_chain_is_reproducible(context: EventContext) -> None:
    original = _confirm(context, _create(context, readiness=80))
    assert original.id is not None
    first = context.service.correct_event(
        CorrectProductionEvent(
            context.actor,
            UTC_TIME,
            original.id,
            ProductionSourceType.MANUAL,
            product_id=context.product_id,
            readiness_percent=70,
            reason="Первая корректировка",
        )
    )
    first = _confirm(context, first)
    assert first.id is not None
    second = context.service.correct_event(
        CorrectProductionEvent(
            context.actor,
            UTC_TIME,
            first.id,
            ProductionSourceType.MANUAL,
            product_id=context.product_id,
            readiness_percent=65,
            reason="Вторая корректировка",
        )
    )
    second = _confirm(context, second)

    assert context.service.get_event(original.id).status is ProductionEventStatus.SUPERSEDED
    assert context.service.get_event(first.id).status is ProductionEventStatus.SUPERSEDED
    assert second.status is ProductionEventStatus.CONFIRMED
    assert second.supersedes_event_id == first.id
    assert first.supersedes_event_id == original.id


def test_correction_idempotency_survives_source_supersede(context: EventContext) -> None:
    original = _confirm(context, _create(context, readiness=80))
    assert original.id is not None
    command = CorrectProductionEvent(
        context.actor,
        UTC_TIME,
        original.id,
        ProductionSourceType.MANUAL,
        product_id=context.product_id,
        readiness_percent=70,
        idempotency_key="correction-key",
        reason="Исправление",
    )
    correction = context.service.correct_event(command)
    _confirm(context, correction)

    repeated = context.service.correct_event(command)

    assert repeated.id == correction.id
    assert repeated.uid == correction.uid


def test_self_correction_is_rejected_by_schema(context: EventContext) -> None:
    original = _confirm(context, _create(context))
    assert original.id is not None
    correction = context.service.correct_event(
        CorrectProductionEvent(
            context.actor,
            UTC_TIME,
            original.id,
            ProductionSourceType.MANUAL,
            product_id=context.product_id,
        )
    )
    assert correction.id is not None
    with pytest.raises(sqlite3.IntegrityError):
        with context.database.connect() as connection:
            connection.execute(
                "UPDATE ProductionEvents SET supersedes_event_id = ? WHERE id = ?",
                (correction.id, correction.id),
            )


def test_readiness_increase_equal_rework_and_explained_decrease(context: EventContext) -> None:
    _confirm(context, _create(context, readiness=50))
    _confirm(context, _create(context, readiness=60))
    _confirm(context, _create(context, readiness=60))
    rework = _confirm(
        context,
        _create(context, readiness=30, event_type=ProductionEventType.REWORK),
    )
    explained = _confirm(
        context,
        _create(context, readiness=20, change_reason="Повторная оценка мастера"),
    )

    assert rework.status is ProductionEventStatus.CONFIRMED
    assert explained.status is ProductionEventStatus.CONFIRMED


def test_unexplained_observation_decrease_is_not_confirmed(context: EventContext) -> None:
    _confirm(context, _create(context, readiness=70))
    lower = _create(context, readiness=50)
    assert lower.id is not None
    context.service.mark_ready(lower.id)

    with pytest.raises(UnexplainedReadinessDecreaseError):
        context.service.confirm_event(
            ConfirmProductionEvent(lower.id, context.actor, UTC_TIME)
        )
    assert context.service.get_event(lower.id).status is ProductionEventStatus.READY


def test_product_legacy_readiness_is_not_changed_by_confirmation(context: EventContext) -> None:
    before = context.directories.get_product(context.product_id)
    assert before is not None

    _confirm(context, _create(context, readiness=88))

    after = context.directories.get_product(context.product_id)
    assert after is not None
    assert after.readiness_percent == before.readiness_percent == 17


def test_multiple_attachments_preserve_order_and_do_not_change_files(
    context: EventContext,
) -> None:
    attachment_service = AttachmentService(context.attachments, context.store)
    first = attachment_service.store_bytes(
        b"first photo",
        original_name="first.bin",
        received_at_utc=UTC_TIME,
    )
    second = attachment_service.store_bytes(
        b"second photo",
        original_name="second.bin",
        received_at_utc=UTC_TIME,
    )
    event = _create(context)
    assert event.id is not None and first.id is not None and second.id is not None
    context.service.attach_existing_attachment(event.id, second.id, sort_order=2)
    context.service.attach_existing_attachment(event.id, first.id, sort_order=1)
    hashes_before = {
        item.storage_key: _file_hash(context.store.resolve(item.storage_key))
        for item in (first, second)
    }

    _confirm(context, event)

    assert [item.attachment_id for item in context.events.list_attachments(event.id)] == [
        first.id,
        second.id,
    ]
    assert hashes_before == {
        item.storage_key: _file_hash(context.store.resolve(item.storage_key))
        for item in (first, second)
    }
    with pytest.raises(ProductionEventImmutableError):
        context.service.detach_attachment(event.id, first.id)


def test_attachment_can_be_reused_and_relation_removal_keeps_metadata_and_file(
    context: EventContext,
) -> None:
    attachment = AttachmentService(context.attachments, context.store).store_bytes(
        b"shared photo",
        original_name="shared.bin",
        received_at_utc=UTC_TIME,
    )
    first = _create(context)
    second = _create(context)
    assert attachment.id and first.id and second.id
    context.service.attach_existing_attachment(first.id, attachment.id)
    context.service.attach_existing_attachment(second.id, attachment.id)

    context.service.detach_attachment(first.id, attachment.id)

    assert context.events.list_attachments(first.id) == []
    assert len(context.events.list_attachments(second.id)) == 1
    assert context.attachments.get_by_id(attachment.id) is not None
    assert context.store.exists(attachment.storage_key)


def test_missing_attachment_cannot_be_linked(context: EventContext) -> None:
    event = _create(context)
    assert event.id is not None
    with pytest.raises(ProductionReferenceNotFoundError, match="Attachment"):
        context.service.attach_existing_attachment(event.id, 999999)


def test_worklog_relations_are_explicit_and_worklog_delete_preserves_event(
    context: EventContext,
) -> None:
    event = _create(context)
    assert event.id is not None
    relation = context.service.link_worklog(
        event.id,
        context.worklog_id,
        context.actor,
        WorkLogRelationType.MANUAL,
    )
    _confirm(context, event)

    context.worklogs.delete(context.worklog_id)

    assert relation.created_by == context.actor
    assert context.events.list_worklogs(event.id) == []
    assert context.service.get_event(event.id).status is ProductionEventStatus.CONFIRMED


def test_worklog_can_link_multiple_events_and_derived_relation_is_not_persisted(
    context: EventContext,
) -> None:
    first = _create(context)
    second = _create(context)
    assert first.id and second.id
    context.service.link_worklog(first.id, context.worklog_id, context.actor)
    context.service.link_worklog(second.id, context.worklog_id, context.actor)
    assert len(context.events.list_worklogs(first.id)) == 1
    assert len(context.events.list_worklogs(second.id)) == 1
    with pytest.raises(InvalidProductionTransitionError, match="производной"):
        context.service.link_worklog(
            first.id,
            context.worklog_id,
            context.actor,
            WorkLogRelationType.SAME_PRODUCT_PERIOD,
        )


def test_missing_worklog_is_rejected(context: EventContext) -> None:
    event = _create(context)
    assert event.id is not None
    with pytest.raises(ProductionReferenceNotFoundError, match="журнала"):
        context.service.link_worklog(event.id, 999999, context.actor)


def test_idempotency_repeat_is_safe_and_conflicting_payload_is_rejected(
    context: EventContext,
) -> None:
    first = _create(context, readiness=25, idempotency_key="event-key")
    repeated = _create(context, readiness=25, idempotency_key="event-key")

    assert repeated.id == first.id and repeated.uid == first.uid
    with pytest.raises(ProductionEventIdempotencyConflictError):
        _create(
            context,
            readiness=26,
            description="Другой payload",
            idempotency_key="event-key",
        )


def test_source_ref_is_not_globally_unique(context: EventContext) -> None:
    first = _create(context, source_ref="same-source")
    second = _create(context, source_ref="same-source")
    assert first.id != second.id


def test_actor_creation_confirmation_and_system_identity_round_trip(
    context: EventContext,
) -> None:
    system_actor = ActorRef(ActorType.SYSTEM_PROCESS, "Production scheduler")
    event = context.service.create_event(
        CreateProductionEvent(
            system_actor,
            UTC_TIME,
            ProductionSourceType.SYSTEM,
            product_id=context.product_id,
        )
    )
    assert event.id is not None
    context.service.mark_ready(event.id)
    confirmed = context.service.confirm_event(
        ConfirmProductionEvent(event.id, context.actor, UTC_TIME)
    )

    assert confirmed.created_by == system_actor
    assert confirmed.confirmed_by == context.actor
    assert confirmed.created_by.uid != confirmed.confirmed_by.uid
    assert not hasattr(confirmed.created_by, "employee_id")


def test_cross_database_diagnostics_find_production_references(
    context: EventContext,
) -> None:
    product_event = _create(context)
    object_event = _create(context)
    employee_event = _create(context)
    assert product_event.id and object_event.id and employee_event.id
    with context.database.connect(foreign_keys=False) as connection:
        connection.execute(
            "UPDATE ProductionEvents SET product_id = 999991 WHERE id = ?",
            (product_event.id,),
        )
        connection.execute(
            "UPDATE ProductionEvents SET object_id_snapshot = 999992 WHERE id = ?",
            (object_event.id,),
        )
        connection.execute(
            "UPDATE ProductionEvents SET reported_by_employee_id = 999993 WHERE id = ?",
            (employee_event.id,),
        )

    codes = {issue.code for issue in context.database.check_references().issues}
    assert {
        "production_event_product",
        "production_event_object_snapshot",
        "production_event_employee",
    } <= codes


def test_reference_deletion_guards_preserve_historical_snapshots(
    context: EventContext,
) -> None:
    event = _confirm(context, _create(context))
    assert event.id is not None
    with pytest.raises(ValueError, match="истории производства"):
        context.directories.delete_product(context.product_id)
    with pytest.raises(ValueError, match="истории производства"):
        context.employees.delete(context.employee_id)


def test_internal_reference_diagnostics_find_broken_core_relations(
    context: EventContext,
) -> None:
    event = _create(context)
    attachment = AttachmentService(context.attachments, context.store).store_bytes(
        b"diagnostic attachment",
        original_name="diagnostic.bin",
        received_at_utc=UTC_TIME,
    )
    assert event.id and attachment.id
    with context.database.connect(foreign_keys=False) as connection:
        connection.execute(
            "UPDATE ProductionEvents SET stage_id = 999981 WHERE id = ?",
            (event.id,),
        )
        connection.execute(
            """
            INSERT INTO ProductionEventAttachments (
                production_event_id, attachment_id, sort_order
            ) VALUES (999982, ?, 0)
            """,
            (attachment.id,),
        )
        connection.execute(
            """
            INSERT INTO ProductionEventWorkLogs (
                production_event_id, worklog_entry_id, relation_type,
                created_at_utc, created_actor_type, created_actor_uid,
                created_actor_local_user_id,
                created_actor_display_name_snapshot
            ) VALUES (999983, ?, 'explicit', ?, ?, ?, ?, ?)
            """,
            (
                context.worklog_id,
                UTC_TIME.isoformat(),
                context.actor.actor_type.value,
                str(context.actor.uid),
                context.actor.local_user_id,
                context.actor.display_name,
            ),
        )

    codes = {issue.code for issue in context.database.check_references().issues}
    assert {
        "production_event_stage",
        "production_attachment_event",
        "production_worklog_event",
    } <= codes


def test_production_event_architecture_boundaries() -> None:
    root = Path(__file__).parents[1]
    repository = ast.parse(
        (root / "production" / "event_repository.py").read_text(encoding="utf-8")
    )
    service = ast.parse(
        (root / "production" / "event_service.py").read_text(encoding="utf-8")
    )
    attachment_store = (
        root / "production" / "attachment_store.py"
    ).read_text(encoding="utf-8")
    worklog_model = (root / "models.py").read_text(encoding="utf-8")
    stage_service = ast.parse(
        (root / "production" / "service.py").read_text(encoding="utf-8")
    )

    assert _imported_roots(repository).isdisjoint({"PySide6", "ui", "workbot"})
    assert _imported_roots(service).isdisjoint(
        {"PySide6", "ui", "workbot", "pathlib", "os", "shutil"}
    )
    assert "ProductionEvent" not in attachment_store
    assert "production_event" not in worklog_model.lower()
    assert _imported_roots(stage_service).isdisjoint({"models", "services"})


def _imported_roots(tree: ast.AST) -> set[str]:
    roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    return roots
