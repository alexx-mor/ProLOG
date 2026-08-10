"""P9 deterministic Production Inbox grouping tests."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from database import Database
from integrations.workbot.production_source_gateway import WorkBotProductionSourceGateway
from production.grouping_models import BundleMessageRole, GroupingStatus
from production.grouping_repository import ProductionInboxGroupingRepository
from production.grouping_service import ProductionInboxGroupingService
from production.source_transport_repository import ProductionSourceTransportRepository
from production.source_transport_service import ProductionSourceTransportService
from workbot.config import WorkBotConfig
from workbot.service import WorkBotService
from workbot.source_models import DownloadedMedia
from workbot.source_repository import WorkBotSourceRepository
from workbot.storage import WorkBotStorage


CHAT_A = -77703766302910
CHAT_B = -77703766302911
BASE = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)


class FakeClient:
    def download_media(self, source_url, _attachment_type, _source_token):
        return DownloadedMedia(source_url.encode(), "image/jpeg", Path(source_url).name)

    def send_message(self, *_args, **_kwargs):
        return {}

    def send_file(self, *_args, **_kwargs):
        return {}

    def answer_callback(self, *_args, **_kwargs):
        return {}


@dataclass
class Context:
    database: Database
    workbot: WorkBotStorage
    workbot_service: WorkBotService
    transport_repository: ProductionSourceTransportRepository
    transport: ProductionSourceTransportService
    gateway: WorkBotProductionSourceGateway
    grouping_repository: ProductionInboxGroupingRepository
    grouping: ProductionInboxGroupingService
    source_a: int
    source_b: int


@pytest.fixture
def context(tmp_path: Path) -> Context:
    database = Database(tmp_path / "core" / "prolog.sqlite3")
    database.initialize()
    workbot_path = tmp_path / "workbot" / "workbot.sqlite3"
    media_root = tmp_path / "workbot" / "workbot_media"
    workbot = WorkBotStorage(workbot_path)
    workbot.initialize()
    service = WorkBotService(
        WorkBotConfig(
            token="test",
            owner_ids=frozenset({1}),
            database_path=workbot_path,
            media_root=media_root,
            export_dir=tmp_path,
        ),
        workbot,
        FakeClient(),
    )
    transport_repository = ProductionSourceTransportRepository(database)
    transport = ProductionSourceTransportService(transport_repository)
    source_a = transport.register_max_chat("Production A", CHAT_A)
    source_b = transport.register_max_chat("Production B", CHAT_B)
    grouping_repository = ProductionInboxGroupingRepository(database)
    return Context(
        database,
        workbot,
        service,
        transport_repository,
        transport,
        WorkBotProductionSourceGateway(workbot_path, media_root),
        grouping_repository,
        ProductionInboxGroupingService(grouping_repository),
        source_a.id,
        source_b.id,
    )


def test_v5_to_v6_migration_is_additive_idempotent_and_preserves_p8(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO Settings(key, value) VALUES ('p9-legacy', 'keep')"
        )
    _downgrade_to_v5(database)
    component_hashes = _component_hashes(database)

    database.initialize()
    history = _migration_history(database)
    database.initialize()

    assert _migration_history(database) == history
    assert _component_hashes(database) == component_hashes
    assert {item.component: item.current_version for item in database.schema_versions()} == {
        "prolog": 8,
        "employees": 1,
        "objects": 1,
        "products": 1,
        "aliases": 1,
    }
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM Settings WHERE key = 'p9-legacy'"
        ).fetchone()[0] == "keep"


def test_v6_migration_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_to_v5(database)

    def fail(connection):
        connection.execute("CREATE TABLE P9ShouldRollback(id INTEGER)")
        raise RuntimeError("p9 migration failure")

    monkeypatch.setattr("database.apply_production_inbox_grouping_migration", fail)
    with pytest.raises(RuntimeError, match="p9 migration failure"):
        database.initialize()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'P9ShouldRollback'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations WHERE component = 'prolog'"
        ).fetchone()[0] == 5


def test_photo_sequence_and_closing_text_form_one_bundle(context: Context) -> None:
    for index in range(3):
        _send(context, f"photo-{index}", 10, BASE + timedelta(seconds=index), photos=1)
    _send(context, "text", 10, BASE + timedelta(minutes=1), text="ШУ1 70%")
    _sync(context)

    result = context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    bundles = context.grouping.list_bundles(current_only=True)
    rows = context.grouping_repository.list_bundle_message_rows(bundles[0].id)

    assert result.created_count == 1
    assert len(bundles) == 1
    assert bundles[0].grouping_status is GroupingStatus.COMPLETE
    assert [row["message_role"] for row in rows] == [
        BundleMessageRole.PHOTO_SOURCE.value,
        BundleMessageRole.PHOTO_SOURCE.value,
        BundleMessageRole.PHOTO_SOURCE.value,
        BundleMessageRole.CLOSING_TEXT.value,
    ]


def test_multi_photo_and_caption_semantics(context: Context) -> None:
    _send(context, "multi", 10, BASE, photos=3)
    _send(context, "captioned", 10, BASE + timedelta(minutes=1), text="готово", photos=2)
    _sync(context)

    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    bundle = context.grouping.list_bundles(current_only=True)[0]
    rows = context.grouping_repository.list_bundle_message_rows(bundle.id)
    attachments = context.transport_repository.list_attachments(rows[0]["inbox_message_id"])

    assert bundle.grouping_status is GroupingStatus.COMPLETE
    assert bundle.close_reason == "captioned_media"
    assert [row["message_role"] for row in rows] == [
        BundleMessageRole.PHOTO_SOURCE.value,
        BundleMessageRole.CAPTIONED_MEDIA.value,
    ]
    assert [row["source_order"] for row in attachments] == [0, 1, 2]


def test_interleaved_senders_keep_independent_open_contexts(context: Context) -> None:
    sequence = [
        ("a1", 10, "", 1),
        ("b1", 20, "", 1),
        ("a2", 10, "", 1),
        ("bt", 20, "B text", 0),
        ("at", 10, "A text", 0),
    ]
    for index, (mid, sender, text, photos) in enumerate(sequence):
        _send(context, mid, sender, BASE + timedelta(seconds=index), text=text, photos=photos)
    _sync(context)

    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    bundles = context.grouping.list_bundles(current_only=True)
    by_sender = {bundle.sender_max_user_id: bundle for bundle in bundles}

    assert set(by_sender) == {10, 20}
    assert len(context.grouping_repository.list_bundle_message_rows(by_sender[10].id)) == 3
    assert len(context.grouping_repository.list_bundle_message_rows(by_sender[20].id)) == 2


def test_timeout_produces_incomplete_photo_and_standalone_text(context: Context) -> None:
    _send(context, "photo", 10, BASE, photos=1)
    _send(context, "late", 10, BASE + timedelta(minutes=20), text="late text")
    _sync(context)

    context.grouping.regroup(window_minutes=15, as_of_utc=BASE + timedelta(minutes=21))
    bundles = context.grouping.list_bundles(current_only=True)

    assert [(item.grouping_status, item.close_reason) for item in bundles] == [
        (GroupingStatus.NEEDS_DESCRIPTION, "timeout"),
        (GroupingStatus.TEXT_ONLY, "standalone_text"),
    ]


def test_day_boundary_does_not_join_messages(context: Context) -> None:
    before_midnight = datetime(2026, 8, 10, 20, 59, tzinfo=timezone.utc)
    _send(context, "photo", 10, before_midnight, photos=1)
    _send(context, "text", 10, before_midnight + timedelta(minutes=2), text="next day")
    _sync(context)

    context.grouping.regroup(as_of_utc=before_midnight + timedelta(minutes=3))
    bundles = context.grouping.list_bundles(current_only=True)

    assert bundles[0].close_reason == "day_boundary"
    assert bundles[1].grouping_status is GroupingStatus.TEXT_ONLY


def test_same_sender_in_different_sources_never_merges(context: Context) -> None:
    _send(context, "a-photo", 10, BASE, photos=1, chat_id=CHAT_A)
    _send(context, "b-text", 10, BASE + timedelta(minutes=1), text="B", chat_id=CHAT_B)
    _sync(context, context.source_a)
    _sync(context, context.source_b)

    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=20))
    bundles = context.grouping.list_bundles(current_only=True)

    assert len(bundles) == 2
    assert {item.source_id for item in bundles} == {context.source_a, context.source_b}


def test_text_without_photos_is_preserved_as_text_only(context: Context) -> None:
    _send(context, "text", 10, BASE, text="наблюдение без фото")
    _sync(context)

    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=1))
    bundle = context.grouping.list_bundles(current_only=True)[0]

    assert bundle.grouping_status is GroupingStatus.TEXT_ONLY
    assert bundle.close_reason == "standalone_text"


def test_edited_revision_creates_new_bundle_and_preserves_lineage(context: Context) -> None:
    _send(context, "photo", 10, BASE, photos=1)
    _send(context, "text", 10, BASE + timedelta(minutes=1), text="ШУ1 60%")
    _sync(context)
    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    original = context.grouping.list_bundles(current_only=True)[0]

    _send(
        context,
        "text",
        10,
        BASE + timedelta(minutes=1),
        text="ШУ1 70%",
        update_type="message_edited",
    )
    _sync(context)
    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=3))
    all_bundles = context.grouping.list_bundles()
    current = [item for item in all_bundles if item.is_current][0]

    assert len(all_bundles) == 2
    assert not next(item for item in all_bundles if item.id == original.id).is_current
    assert current.supersedes_bundle_id == original.id


def test_deleted_closing_text_rebuilds_photo_bundle_as_needs_description(context: Context) -> None:
    _send(context, "photo", 10, BASE, photos=1)
    _send(context, "text", 10, BASE + timedelta(minutes=1), text="описание")
    _sync(context)
    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    original = context.grouping.list_bundles(current_only=True)[0]
    WorkBotSourceRepository(context.workbot).record_tombstone(
        "text", CHAT_A, BASE + timedelta(minutes=3), json.dumps({"message_removed": "text"})
    )

    transport_result = _sync(context)
    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=20))
    current = context.grouping.list_bundles(current_only=True)[0]

    assert transport_result.tombstone_imported_count == 1
    assert current.grouping_status is GroupingStatus.NEEDS_DESCRIPTION
    assert current.supersedes_bundle_id == original.id


def test_deleted_photo_rebuilds_bundle_with_remaining_messages(context: Context) -> None:
    _send(context, "photo-1", 10, BASE, photos=1)
    _send(context, "photo-2", 10, BASE + timedelta(seconds=1), photos=1)
    _send(context, "text", 10, BASE + timedelta(minutes=1), text="описание")
    _sync(context)
    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    WorkBotSourceRepository(context.workbot).record_tombstone(
        "photo-1", CHAT_A, BASE + timedelta(minutes=3), "{}"
    )

    _sync(context)
    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=4))
    current = context.grouping.list_bundles(current_only=True)[0]
    rows = context.grouping_repository.list_bundle_message_rows(current.id)

    assert [row["source_message_id"] for row in rows] == ["photo-2", "text"]


def test_rerun_is_idempotent_and_diagnostics_are_clean(context: Context) -> None:
    _send(context, "photo", 10, BASE, photos=1)
    _send(context, "text", 10, BASE + timedelta(minutes=1), text="описание")
    _sync(context)
    first = context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))
    second = context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=2))

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.unchanged_count == 1
    assert context.grouping.diagnostics(as_of_utc=BASE + timedelta(minutes=2)).is_healthy


def test_source_sequence_is_primary_tie_breaker_after_timestamp(context: Context) -> None:
    _send(context, "z-photo", 10, BASE, photos=1, sequence=1)
    _send(context, "a-text", 10, BASE, text="closing", sequence=2)
    _sync(context)

    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=1))
    bundle = context.grouping.list_bundles(current_only=True)[0]
    rows = context.grouping_repository.list_bundle_message_rows(bundle.id)

    assert [row["source_message_id"] for row in rows] == ["z-photo", "a-text"]


def test_p9_has_no_product_event_matching_or_attachment_materialization(context: Context) -> None:
    _send(context, "caption", 10, BASE, text="ШУ1 электромонтаж 70%", photos=1)
    _sync(context)
    before = _primary_counts(context.database)

    context.grouping.regroup(as_of_utc=BASE + timedelta(minutes=1))

    assert _primary_counts(context.database) == before
    root = Path(__file__).parents[1]
    paths = [
        root / "production" / "grouping_models.py",
        root / "production" / "grouping_repository.py",
        root / "production" / "grouping_service.py",
    ]
    combined = "\n".join(path.read_text("utf-8") for path in paths)
    assert "ProductionEvent" not in combined
    assert "ProductMatch" not in combined
    assert "AttachmentService" not in combined
    tree = ast.parse((root / "production" / "grouping_service.py").read_text("utf-8"))
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "PySide6" not in imports
    assert "workbot" not in imports


def _send(
    context: Context,
    message_id: str,
    sender_id: int,
    timestamp: datetime,
    *,
    text: str = "",
    photos: int = 0,
    chat_id: int = CHAT_A,
    sequence: int | None = None,
    update_type: str = "message_created",
) -> None:
    attachments = [
        {
            "type": "image",
            "payload": {
                "token": f"{message_id}-{index}",
                "url": f"https://media.test/{message_id}-{index}.jpg",
            },
            "filename": f"{message_id}-{index}.jpg",
        }
        for index in range(photos)
    ]
    context.workbot_service.handle_update(
        {
            "update_type": update_type,
            "timestamp": int(timestamp.timestamp() * 1000),
            "message": {
                "sender": {
                    "user_id": sender_id,
                    "first_name": "Sender",
                    "last_name": str(sender_id),
                    "username": f"sender{sender_id}",
                    "is_bot": False,
                },
                "recipient": {"chat_type": "chat", "chat_id": chat_id},
                "timestamp": int(timestamp.timestamp() * 1000),
                "body": {
                    "mid": message_id,
                    "seq": sequence if sequence is not None else int(timestamp.timestamp()),
                    "text": text,
                    "attachments": attachments,
                },
            },
        }
    )


def _sync(context: Context, source_id: int | None = None):
    return context.transport.sync_source(source_id or context.source_a, context.gateway)


def _downgrade_to_v5(database: Database) -> None:
    with database.connect(foreign_keys=False) as connection:
        for trigger in (
            "trg_production_match_runs_immutable",
            "trg_production_match_runs_no_delete",
            "trg_production_proposals_immutable_update",
            "trg_production_proposals_immutable_delete",
            "trg_production_product_candidates_immutable_update",
            "trg_production_product_candidates_immutable_delete",
            "trg_production_object_candidates_immutable_update",
            "trg_production_object_candidates_immutable_delete",
            "trg_production_stage_candidates_immutable_update",
            "trg_production_stage_candidates_immutable_delete",
            "trg_production_evidence_immutable_update",
            "trg_production_evidence_immutable_delete",
            "trg_production_issues_immutable_update",
            "trg_production_issues_immutable_delete",
            "trg_production_inbox_bundle_messages_immutable_delete",
            "trg_production_inbox_bundle_messages_immutable_update",
            "trg_production_inbox_tombstones_immutable_delete",
            "trg_production_inbox_tombstones_immutable_update",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in (
            "ProductionInboxProposalIssues",
            "ProductionInboxProposalEvidence",
            "ProductionInboxStageCandidates",
            "ProductionInboxObjectCandidates",
            "ProductionInboxProductCandidates",
            "ProductionInboxProposals",
            "ProductionInboxMatchRuns",
            "ProductionStageAliases",
            "ProductionInboxBundleMessages",
            "ProductionInboxBundles",
            "ProductionInboxTombstoneSyncState",
            "ProductionInboxSourceTombstones",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute(
            "DELETE FROM SchemaMigrations WHERE component = 'prolog' AND version >= 6"
        )


def _component_hashes(database: Database) -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in database.database_paths().items()
        if name != "prolog"
    }


def _migration_history(database: Database):
    with database.connect() as connection:
        return tuple(connection.execute("SELECT * FROM SchemaMigrations ORDER BY version"))


def _primary_counts(database: Database) -> tuple[int, int, int, int]:
    with database.connect() as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM WorkLogEntries").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM products_db.Products").fetchone()[0],
        )
