"""P11 human review, confirmation, media promotion and grouping tests."""

from __future__ import annotations

import ast
import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from auth import AuthSession, ROLE_ADMIN
from database import Database, DirectoryRepository
from models import ProductItem
from production.actor_adapter import actor_from_auth_session
from production.errors import UnexplainedReadinessDecreaseError
from production.models import ProductionEventStatus, ProductionEventType
from production.module import build_production_module
from production.review_models import RejectionCode, ReviewDecision, ReviewFilter, ReviewStatus
from production.review_repository import StaleProductionInboxReviewError


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
UTC_TIME = datetime(2026, 8, 10, 9, tzinfo=timezone.utc)


class MemoryMediaReader:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def read_media(self, storage_key: str, expected_sha256: str) -> bytes:
        value = self.values[storage_key]
        if hashlib.sha256(value).hexdigest() != expected_sha256:
            raise ValueError("corrupt")
        return value


class Context:
    def __init__(self, tmp_path: Path) -> None:
        self.database = Database(tmp_path / "prolog.sqlite3")
        self.database.initialize()
        self.directories = DirectoryRepository(self.database)
        self.object_id = self.directories.upsert("objects", "Объект P11")
        self.product_id = self.directories.save_product(ProductItem(
            object_id=self.object_id, name="ШУ1", serial_number="3075",
        ))
        self.module = build_production_module(self.database, tmp_path / "attachments")
        self.actor = actor_from_auth_session(AuthSession(
            "Руководитель", ROLE_ADMIN, "Организация", "Отдел", "Руководитель",
        ))
        self.counter = 0

    def bundle(self, text="3075 электромонтаж 70%", *, photos=0, sender=100) -> int:
        self.counter += 1
        bundle_id = _create_bundle(self.database, self.counter, text, photos, sender)
        self.module.matching.match_bundle(bundle_id)
        if photos:
            self.module.review.set_source_media_reader(MemoryMediaReader({
                f"media/{self.counter}/{index}.png": PNG for index in range(photos)
            }))
        return bundle_id

    def item(self, bundle_id: int):
        return next(
            row for row in self.module.review.list_items(ReviewFilter.ALL)
            if row.bundle_id == bundle_id
        )

    def decision(self, item, **changes):
        values = dict(
            bundle_id=item.bundle_id,
            bundle_fingerprint=item.bundle_fingerprint,
            match_run_id=item.match_run_id,
            proposal_id=item.proposal_id,
            product_id=self.product_id,
            stage_id=item.stage_id,
            readiness_percent=item.readiness_percent,
            description=item.description_text,
            observed_at_utc=UTC_TIME,
            reported_by_employee_id=None,
            actor=self.actor,
            event_type=ProductionEventType.OBSERVATION,
            change_reason="",
        )
        values.update(changes)
        return ReviewDecision(**values)


@pytest.fixture
def context(tmp_path: Path) -> Context:
    return Context(tmp_path)


def test_core_v8_is_additive_and_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    database.initialize()
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        version = connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations WHERE component='prolog'"
        ).fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 0
    assert version == 8
    assert {
        "ProductionInboxReviews", "ProductionInboxReviewActions",
        "ProductionInboxReviewAttachmentPromotions",
        "ProductionInboxManualBundleSources",
    }.issubset(tables)


def test_v7_to_v8_migration_preserves_legacy_and_component_databases(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect(foreign_keys=False) as connection:
        _drop_v8(connection)
        connection.execute("INSERT INTO Settings(key,value) VALUES ('p11-legacy','keep')")
    component_hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in database.database_paths().items() if name != "prolog"
    }
    database.initialize()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM Settings WHERE key='p11-legacy'"
        ).fetchone()[0] == "keep"
    assert component_hashes == {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in database.database_paths().items() if name != "prolog"
    }


def test_v8_migration_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect(foreign_keys=False) as connection:
        _drop_v8(connection)

    def fail(connection):
        connection.execute("CREATE TABLE P11ShouldRollback(id INTEGER)")
        raise RuntimeError("p11 migration failure")

    monkeypatch.setattr("database.apply_production_inbox_review_migration", fail)
    with pytest.raises(RuntimeError, match="p11 migration failure"):
        database.initialize()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='P11ShouldRollback'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations WHERE component='prolog'"
        ).fetchone()[0] == 7


def test_text_only_confirmation_creates_one_confirmed_event(context: Context) -> None:
    item = context.item(context.bundle())
    result = context.module.review.confirm(context.decision(item))
    event = context.module.events.get_event(result.production_event_id or 0)
    assert result.status is ReviewStatus.CONFIRMED
    assert event.status is ProductionEventStatus.CONFIRMED
    assert event.product_id == context.product_id
    assert str(result.review_uid) in (event.source_ref or "")
    assert context.module.projections.get_product_state(context.product_id).readiness_percent == 70


def test_photo_confirmation_materializes_originals_in_order(context: Context) -> None:
    item = context.item(context.bundle(photos=3))
    result = context.module.review.confirm(context.decision(item))
    relations = context.module.events.events.list_attachments(result.production_event_id or 0)
    assert [row.sort_order for row in relations] == [0, 1, 2]
    assert all(context.module.attachments.read_bytes(row.attachment_id) == PNG for row in relations)


def test_retry_does_not_duplicate_event_or_attachments(context: Context) -> None:
    item = context.item(context.bundle(photos=2))
    decision = context.decision(item)
    first = context.module.review.confirm(decision)
    second = context.module.review.confirm(decision)
    assert first.production_event_id == second.production_event_id
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0] == 2


@pytest.mark.parametrize("state", ["missing", "corrupt", "pending", "failed"])
def test_unavailable_photo_blocks_confirmation(context: Context, state: str) -> None:
    item = context.item(context.bundle(photos=1))
    with context.database.connect() as connection:
        connection.execute("DROP TRIGGER trg_production_inbox_attachments_immutable_update")
        connection.execute("UPDATE ProductionInboxAttachments SET media_state = ?", (state,))
    with pytest.raises(RuntimeError, match="фотографии"):
        context.module.review.confirm(context.decision(item))
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 0


def test_source_change_before_confirmation_is_blocked(context: Context) -> None:
    item = context.item(context.bundle())
    with context.database.connect() as connection:
        connection.execute(
            "UPDATE ProductionInboxBundles SET is_current = 0, superseded_at_utc = ?, superseded_reason = 'test' WHERE id = ?",
            (UTC_TIME.isoformat(), item.bundle_id),
        )
    with pytest.raises(StaleProductionInboxReviewError):
        context.module.review.confirm(context.decision(item))


def test_reject_is_audited_and_creates_no_event(context: Context) -> None:
    item = context.item(context.bundle("Есть"))
    result = context.module.review.reject(
        item, context.actor, RejectionCode.NOT_PRODUCTION, "Служебное сообщение"
    )
    assert result.status is ReviewStatus.REJECTED
    assert context.module.review.list_items(ReviewFilter.REQUIRES_REVIEW) == []
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 0
        assert connection.execute(
            "SELECT action_type FROM ProductionInboxReviewActions"
        ).fetchone()[0] == "rejected"


def test_source_edit_after_confirmation_surfaces_and_correction_supersedes(
    context: Context,
) -> None:
    old_item = context.item(context.bundle("3075 электромонтаж 60%"))
    original = context.module.review.confirm(context.decision(old_item))
    new_bundle = context.bundle("3075 электромонтаж 80%")
    with context.database.connect() as connection:
        connection.execute(
            "UPDATE ProductionInboxBundles SET is_current=0, superseded_at_utc=?, "
            "superseded_reason='source_changed' WHERE id=?",
            (UTC_TIME.isoformat(), old_item.bundle_id),
        )
        connection.execute(
            "UPDATE ProductionInboxBundles SET supersedes_bundle_id=? WHERE id=?",
            (old_item.bundle_id, new_bundle),
        )
    context.module.matching.match_all_current()
    changed = context.item(new_bundle)
    assert changed.review_status is ReviewStatus.SOURCE_CHANGED
    correction = context.module.review.confirm(context.decision(
        changed,
        event_type=ProductionEventType.CORRECTION,
        correction_source_event_id=original.production_event_id,
        change_reason="Исправлен текст MAX",
    ))
    old_event = context.module.events.get_event(original.production_event_id or 0)
    new_event = context.module.events.get_event(correction.production_event_id or 0)
    assert old_event.status is ProductionEventStatus.SUPERSEDED
    assert new_event.event_type is ProductionEventType.CORRECTION
    assert new_event.readiness_percent == 80


def test_source_edit_can_leave_existing_event_unchanged(context: Context) -> None:
    old = context.item(context.bundle("3075 60%"))
    confirmed = context.module.review.confirm(context.decision(old))
    new_bundle = context.bundle("3075 61%")
    with context.database.connect() as connection:
        connection.execute(
            "UPDATE ProductionInboxBundles SET is_current=0, superseded_at_utc=?, "
            "superseded_reason='source_changed' WHERE id=?",
            (UTC_TIME.isoformat(), old.bundle_id),
        )
        connection.execute(
            "UPDATE ProductionInboxBundles SET supersedes_bundle_id=? WHERE id=?",
            (old.bundle_id, new_bundle),
        )
    context.module.matching.match_all_current()
    changed = context.item(new_bundle)
    result = context.module.review.keep_existing(changed, context.actor)
    assert result.status is ReviewStatus.KEPT_EXISTING
    assert result.production_event_id == confirmed.production_event_id
    assert context.module.events.get_event(
        confirmed.production_event_id or 0
    ).status is ProductionEventStatus.CONFIRMED


def test_source_edit_after_rejection_is_reviewable_without_existing_event(
    context: Context,
) -> None:
    old = context.item(context.bundle("Служебное сообщение"))
    context.module.review.reject(
        old, context.actor, RejectionCode.NOT_PRODUCTION, "Не фотоотчет"
    )
    new_bundle = context.bundle("3075 электромонтаж 70%")
    with context.database.connect() as connection:
        connection.execute(
            "UPDATE ProductionInboxBundles SET is_current=0, superseded_at_utc=?, "
            "superseded_reason='source_changed' WHERE id=?",
            (UTC_TIME.isoformat(), old.bundle_id),
        )
        connection.execute(
            "UPDATE ProductionInboxBundles SET supersedes_bundle_id=? WHERE id=?",
            (old.bundle_id, new_bundle),
        )
    context.module.matching.match_all_current()
    changed = context.item(new_bundle)
    assert changed.review_status is ReviewStatus.SOURCE_CHANGED
    assert context.module.review.source_changed_event_id(changed) is None
    result = context.module.review.confirm(context.decision(changed))
    assert result.status is ReviewStatus.CONFIRMED


def test_link_failure_after_confirm_recovers_without_second_event(
    context: Context,
    monkeypatch,
) -> None:
    item = context.item(context.bundle())
    decision = context.decision(item)
    original_finish = context.module.review.repository.finish_confirmation
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("review link failure")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(
        context.module.review.repository, "finish_confirmation", fail_once
    )
    result = context.module.review.confirm(decision)
    assert result.recovered
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 1


def test_actor_and_sender_binding_are_separate(context: Context) -> None:
    item = context.item(context.bundle(sender=777))
    with context.database.connect() as connection:
        employee_id = connection.execute(
            "INSERT INTO employees_db.Employees(full_name,status) VALUES ('Мастер','Активен')"
        ).lastrowid
        connection.execute(
            "INSERT INTO MaxUserBindings(max_user_id,employee_id,created_at,updated_at) VALUES (777,?,?,?)",
            (employee_id, UTC_TIME.isoformat(), UTC_TIME.isoformat()),
        )
    detail = context.module.review.detail(item.bundle_id, item.proposal_id)
    assert detail.reported_by_employee_id == employee_id
    assert context.actor.local_user_id is None


def test_manual_split_retains_original_and_replaces_review_queue(context: Context) -> None:
    bundle_id = context.bundle("3075 50%", photos=1)
    item = context.item(bundle_id)
    first_message = context.module.review.detail(bundle_id, item.proposal_id).messages[0].id
    second_message = _append_message(context.database, bundle_id, 800, "3075 80%")
    created = context.module.review.manual_split(
        bundle_id, ((first_message,), (second_message,)), context.actor
    )
    current_ids = {row.bundle_id for row in context.module.review.list_items(ReviewFilter.ALL)}
    assert len(created) == 2
    assert bundle_id not in current_ids
    assert set(created).issubset(current_ids)
    with context.database.connect() as connection:
        assert connection.execute(
            "SELECT is_current FROM ProductionInboxBundles WHERE id=?", (bundle_id,)
        ).fetchone()[0] == 1


def test_cross_chat_manual_merge_is_rejected(context: Context) -> None:
    first = context.item(context.bundle("3075 50%"))
    second_id = _create_bundle(context.database, 500, "3075 60%", 0, 100, chat_id=-2)
    context.module.matching.match_bundle(second_id)
    second = context.item(second_id)
    messages = tuple(
        message.id for item in (first, second)
        for message in context.module.review.detail(item.bundle_id, item.proposal_id).messages
    )
    with pytest.raises(ValueError, match="source/chat"):
        context.module.review.manual_merge(
            (first.bundle_id, second.bundle_id), messages, context.actor
        )


def test_manual_merge_reuses_source_messages_and_hides_deterministic_bundles(
    context: Context,
) -> None:
    first = context.item(context.bundle("3075"))
    second = context.item(context.bundle("электромонтаж 70%"))
    message_ids = tuple(
        message.id for item in (first, second)
        for message in context.module.review.detail(item.bundle_id, item.proposal_id).messages
    )
    merged_id = context.module.review.manual_merge(
        (first.bundle_id, second.bundle_id), message_ids, context.actor
    )
    queue_ids = {
        item.bundle_id for item in context.module.review.list_items(ReviewFilter.ALL)
    }
    assert merged_id in queue_ids
    assert first.bundle_id not in queue_ids
    assert second.bundle_id not in queue_ids
    merged = context.item(merged_id)
    assert tuple(
        message.id
        for message in context.module.review.detail(merged_id, merged.proposal_id).messages
    ) == message_ids


def test_readiness_decrease_requires_rework_correction_or_reason(
    context: Context,
) -> None:
    first = context.item(context.bundle("3075 80%"))
    context.module.review.confirm(context.decision(first, readiness_percent=80))

    unexplained = context.item(context.bundle("3075 50%"))
    with pytest.raises(UnexplainedReadinessDecreaseError):
        context.module.review.confirm(
            context.decision(unexplained, readiness_percent=50)
        )

    rework = context.item(context.bundle("3075 переработка 50%"))
    rework_result = context.module.review.confirm(context.decision(
        rework,
        readiness_percent=50,
        event_type=ProductionEventType.REWORK,
        change_reason="Возврат на доработку",
    ))
    assert context.module.events.get_event(
        rework_result.production_event_id or 0
    ).event_type is ProductionEventType.REWORK

    explained = context.item(context.bundle("3075 повторная оценка 40%"))
    explained_result = context.module.review.confirm(context.decision(
        explained,
        readiness_percent=40,
        change_reason="Повторная оценка мастера",
    ))
    assert context.module.events.get_event(
        explained_result.production_event_id or 0
    ).change_reason == "Повторная оценка мастера"


def test_matching_and_queue_refresh_never_create_events(context: Context) -> None:
    context.bundle("3075 электромонтаж 70%", photos=2)
    context.module.matching.match_all_current()
    context.module.review.list_items(ReviewFilter.ALL)
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0] == 0


def test_review_domain_service_has_no_ui_workbot_or_sqlite_imports() -> None:
    forbidden = {"PySide6", "workbot", "sqlite3", "integrations.workbot"}
    for path in (Path("production/review_models.py"), Path("production/review_service.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        imported.update(
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert all(
            not any(name == blocked or name.startswith(blocked + ".") for blocked in forbidden)
            for name in imported
        )


def _create_bundle(database, number, text, photos, sender, *, chat_id=-77703766302910):
    now = UTC_TIME.isoformat()
    digest = hashlib.sha256(text.encode()).hexdigest()
    with database.connect() as connection:
        source = connection.execute(
            "SELECT id FROM ProductionInboxSources WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if source is None:
            source_id = connection.execute(
                "INSERT INTO ProductionInboxSources(uid,source_type,source_ref,display_name,chat_id,created_at_utc,updated_at_utc) VALUES (?,'max_chat',?,'Фотоотчеты',?,?,?)",
                (str(uuid4()), f"test:{chat_id}", chat_id, now, now),
            ).lastrowid
        else:
            source_id = int(source[0])
        message_id = connection.execute(
            """
            INSERT INTO ProductionInboxMessages(
                uid,source_id,source_type,source_ref,source_message_id,
                source_revision_id,source_revision_number,chat_id,sender_max_user_id,
                sender_display_snapshot,message_timestamp_utc,source_received_at_utc,
                transported_at_utc,source_sequence,source_text,content_hash,
                source_content_json,raw_envelope_json,change_kind
            ) VALUES (?,?,'max_chat','test',?,?,1,?,?,'Мастер',?,?,?,?,?,?, '{}','{}','original')
            """,
            (str(uuid4()), source_id, f"msg-{number}-{chat_id}", number, chat_id,
             sender, now, now, now, number, text, digest),
        ).lastrowid
        for index in range(photos):
            connection.execute(
                """
                INSERT INTO ProductionInboxAttachments(
                    uid,inbox_message_id,source_attachment_row_id,source_attachment_id,
                    identity_kind,source_order,attachment_type,mime_type,original_name,
                    source_size,source_download_status,source_sha256,source_storage_key,
                    media_state,source_metadata_json
                ) VALUES (?,?,?,?, 'file_id',?,'photo','image/png',?,?, 'downloaded',?,?, 'available','{}')
                """,
                (str(uuid4()), message_id, abs(number) * 10 + index + 1,
                 f"att-{number}-{index}", index, f"photo-{index}.png", len(PNG),
                 hashlib.sha256(PNG).hexdigest(), f"media/{number}/{index}.png"),
            )
        fingerprint = hashlib.sha256(f"bundle-{number}-{chat_id}".encode()).hexdigest()
        bundle_id = connection.execute(
            """
            INSERT INTO ProductionInboxBundles(
                uid,source_id,chat_id,sender_max_user_id,sender_display_snapshot,
                started_at_utc,ended_at_utc,grouping_status,close_reason,origin,
                grouping_rule_version,grouping_window_seconds,
                day_boundary_utc_offset_minutes,source_fingerprint,is_current,
                created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,'text_closes_sequence','deterministic',
                      'deterministic-v1',900,180,?,1,?,?)
            """,
            (str(uuid4()), source_id, chat_id, sender, "Мастер", now, now,
             "complete" if photos else "text_only", fingerprint, now, now),
        ).lastrowid
        connection.execute(
            "INSERT INTO ProductionInboxBundleMessages VALUES (?,?,0,?)",
            (bundle_id, message_id, "captioned_media" if photos else "text_only"),
        )
    return int(bundle_id)


def _append_message(database, bundle_id, number, text):
    now = datetime(2026, 8, 10, 9, 20, tzinfo=timezone.utc).isoformat()
    digest = hashlib.sha256(text.encode()).hexdigest()
    with database.connect() as connection:
        bundle = connection.execute(
            "SELECT * FROM ProductionInboxBundles WHERE id=?", (bundle_id,)
        ).fetchone()
        message_id = connection.execute(
            """
            INSERT INTO ProductionInboxMessages(
                uid,source_id,source_type,source_ref,source_message_id,source_revision_id,
                source_revision_number,chat_id,sender_max_user_id,sender_display_snapshot,
                message_timestamp_utc,source_received_at_utc,transported_at_utc,
                source_sequence,source_text,content_hash,source_content_json,
                raw_envelope_json,change_kind
            ) VALUES (?,?,'max_chat','test',?,?,1,?,?,'Мастер',?,?,?,?,?,?,'{}','{}','original')
            """,
            (str(uuid4()), bundle["source_id"], f"msg-{number}", number,
             bundle["chat_id"], bundle["sender_max_user_id"], now, now, now,
             number, text, digest),
        ).lastrowid
        connection.execute(
            "INSERT INTO ProductionInboxBundleMessages VALUES (?,?,1,'closing_text')",
            (bundle_id, message_id),
        )
    return int(message_id)


def _drop_v8(connection) -> None:
    for trigger in (
        "trg_production_review_identity_immutable",
        "trg_production_review_no_delete",
        "trg_production_review_actions_immutable_update",
        "trg_production_review_actions_immutable_delete",
        "trg_production_manual_lineage_immutable_update",
        "trg_production_manual_lineage_immutable_delete",
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "ProductionInboxReviewAttachmentPromotions",
        "ProductionInboxReviewActions",
        "ProductionInboxReviews",
        "ProductionInboxManualBundleSources",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute(
        "DELETE FROM SchemaMigrations WHERE component='prolog' AND version=8"
    )
