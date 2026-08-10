"""P8 tests for one-to-one WorkBot to production inbox transport."""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from database import Database
from integrations.workbot.production_source_gateway import WorkBotProductionSourceGateway
from production.source_transport_models import InboxChangeKind
from production.source_transport_repository import ProductionSourceTransportRepository
from production.source_transport_service import ProductionSourceTransportService
from workbot.config import WorkBotConfig
from workbot.service import WorkBotService
from workbot.source_models import DownloadedMedia, SourceRevisionInput
from workbot.source_repository import WorkBotSourceRepository
from workbot.storage import WorkBotStorage


PRODUCTION_CHAT = -77703766302910
REPORT_CHAT = -70408493648395
UTC_MS = 1786374000000
REPORT_TEXT = """Дата: 10.08.2026
Виды работ: Монтаж шкафа
Затраченное время: 8
Объект: Цех № 1
Местонахождение: Производство"""


class FakeClient:
    def __init__(self) -> None:
        self.payloads = {
            f"https://media.test/{index}.jpg": f"photo-{index}".encode()
            for index in range(1, 10)
        }

    def download_media(self, source_url, _attachment_type, _source_token):
        return DownloadedMedia(self.payloads[source_url], "image/jpeg", Path(source_url).name)

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
    repository: ProductionSourceTransportRepository
    transport: ProductionSourceTransportService
    gateway: WorkBotProductionSourceGateway
    source_id: int
    media_root: Path


@pytest.fixture
def context(tmp_path: Path) -> Context:
    database = Database(tmp_path / "core" / "prolog.sqlite3")
    database.initialize()
    workbot_path = tmp_path / "workbot" / "workbot.sqlite3"
    media_root = tmp_path / "workbot" / "workbot_media"
    workbot = WorkBotStorage(workbot_path)
    workbot.initialize()
    client = FakeClient()
    config = WorkBotConfig(
        token="test",
        owner_ids=frozenset({1}),
        database_path=workbot_path,
        media_root=media_root,
        export_dir=tmp_path,
    )
    workbot_service = WorkBotService(config, workbot, client)
    repository = ProductionSourceTransportRepository(database)
    transport = ProductionSourceTransportService(repository)
    source = transport.register_max_chat(
        "Фотоотчеты Электроцех",
        PRODUCTION_CHAT,
        web_url="https://web.max.ru/-77703766302910",
    )
    assert source.id is not None
    return Context(
        database,
        workbot,
        workbot_service,
        repository,
        transport,
        WorkBotProductionSourceGateway(workbot_path, media_root),
        source.id,
        media_root,
    )


def test_core_v4_to_v5_is_additive_idempotent_and_preserves_components(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute("INSERT INTO Settings(key, value) VALUES ('p8-legacy', 'keep')")
    before = _component_hashes(database)
    _downgrade_core_to_v4(database)

    database.initialize()
    first = _migration_history(database)
    database.initialize()

    assert _migration_history(database) == first
    assert _component_hashes(database) == before
    assert {item.component: item.current_version for item in database.schema_versions()} == {
        "prolog": 6,
        "employees": 1,
        "objects": 1,
        "products": 1,
        "aliases": 1,
    }
    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "ProductionInboxSources",
        "ProductionInboxMessages",
        "ProductionInboxAttachments",
        "ProductionInboxSyncState",
        "ProductionInboxSyncRuns",
        "ProductionInboxSyncIssues",
    } <= tables


def test_core_v5_migration_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_core_to_v4(database)

    def fail(connection):
        connection.execute("CREATE TABLE P8ShouldRollback(id INTEGER)")
        raise RuntimeError("p8 migration failure")

    monkeypatch.setattr("database.apply_production_source_transport_migration", fail)
    with pytest.raises(RuntimeError, match="p8 migration failure"):
        database.initialize()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'P8ShouldRollback'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations WHERE component = 'prolog'"
        ).fetchone()[0] == 4


def test_only_configured_chat_is_transported_and_unknown_sender_is_allowed(context: Context) -> None:
    context.workbot_service.handle_update(_update("production", 99999, PRODUCTION_CHAT, "ШУ1 70%"))
    context.workbot_service.handle_update(_update("daily", 99999, REPORT_CHAT, REPORT_TEXT))

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.imported_count == 1
    rows = context.repository.list_messages(context.source_id)
    assert len(rows) == 1
    assert rows[0]["source_message_id"] == "production"
    assert rows[0]["sender_max_user_id"] == 99999
    assert len(context.workbot.reports()) == 1
    assert context.workbot.reports()[0].hours == 8


def test_photo_text_caption_and_source_order_are_snapshotted_without_copy(context: Context) -> None:
    context.workbot_service.handle_update(
        _update("photos", 10, PRODUCTION_CHAT, "ШУ1 70%", [_image(1), _image(2), _image(3)])
    )
    context.workbot_service.handle_update(
        _update("photo-only", 11, PRODUCTION_CHAT, "", [_image(4)])
    )
    context.workbot_service.handle_update(
        _update("text-only", 12, PRODUCTION_CHAT, "ВРУ слесарка")
    )

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.imported_count == 3
    messages = context.repository.list_messages(context.source_id)
    assert len(messages) == 3
    attachments = context.repository.list_attachments(messages[0]["id"])
    assert [row["source_order"] for row in attachments] == [0, 1, 2]
    assert all(row["media_state"] == "available" for row in attachments)
    assert all("/" in row["source_storage_key"] for row in attachments)
    assert all(not Path(row["source_storage_key"]).is_absolute() for row in attachments)
    with context.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0] == 0


def test_six_sequential_messages_remain_six_sources_without_grouping(context: Context) -> None:
    sequence = [
        (7001, "", [_image(1)]),
        (7001, "", [_image(2)]),
        (7001, "ШУ1 электромонтаж 70%", []),
        (7002, "", [_image(3)]),
        (7002, "", [_image(4)]),
        (7002, "ВРУ слесарка", []),
    ]
    for index, (sender, text, attachments) in enumerate(sequence, start=1):
        context.workbot_service.handle_update(
            _update(f"sequence-{index}", sender, PRODUCTION_CHAT, text, attachments)
        )
    before = _primary_counts(context.database)

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.imported_count == 6
    assert len(context.repository.list_messages(context.source_id)) == 6
    assert _primary_counts(context.database) == before


def test_resync_is_idempotent_and_edit_creates_changed_snapshot(context: Context) -> None:
    original = _update("edited", 10, PRODUCTION_CHAT, "ШУ2 40%", [_image(1)])
    context.workbot_service.handle_update(original)
    first = context.transport.sync_source(context.source_id, context.gateway)
    second = context.transport.sync_source(context.source_id, context.gateway)
    context.workbot_service.handle_update(
        _update(
            "edited", 10, PRODUCTION_CHAT, "ШУ2 50%", [_image(1)],
            update_type="message_edited",
        )
    )
    third = context.transport.sync_source(context.source_id, context.gateway)

    assert first.imported_count == 1
    assert second.imported_count == 0
    assert third.imported_count == 1 and third.changed_count == 1
    rows = context.repository.list_messages(context.source_id)
    assert len(rows) == 2
    assert rows[0]["change_kind"] == InboxChangeKind.ORIGINAL.value
    assert rows[1]["change_kind"] == InboxChangeKind.CHANGED.value
    assert rows[1]["supersedes_inbox_message_id"] == rows[0]["id"]
    assert rows[0]["source_text"] == "ШУ2 40%"
    assert rows[1]["source_text"] == "ШУ2 50%"


def test_bot_self_revision_is_skipped_by_transport(context: Context) -> None:
    context.workbot.upsert_user(615018254294, "WorkBot", "", "bot")
    content = json.dumps({"text": "service", "attachments": [], "link": None})
    WorkBotSourceRepository(context.workbot).record_revision(
        SourceRevisionInput(
            "bot-self", PRODUCTION_CHAT, 615018254294, "WorkBot",
            datetime.now(timezone.utc), None, 1, "service",
            hashlib.sha256(content.encode()).hexdigest(), content,
            json.dumps({"message": {"sender": {"is_bot": True}}}),
            datetime.now(timezone.utc), (),
        )
    )

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.skipped_count == 1
    assert context.repository.list_messages(context.source_id) == []


def test_missing_media_does_not_block_other_messages(context: Context) -> None:
    context.workbot_service.handle_update(
        _update("missing", 10, PRODUCTION_CHAT, "", [_image(1)])
    )
    context.workbot_service.handle_update(
        _update("healthy", 11, PRODUCTION_CHAT, "текст")
    )
    with context.workbot.connect() as connection:
        key = connection.execute(
            "SELECT storage_key FROM source_message_attachments"
        ).fetchone()[0]
    (context.media_root / Path(key)).unlink()

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.imported_count == 2
    assert result.error_count == 1
    assert len(context.repository.list_messages(context.source_id)) == 2
    assert context.repository.list_attachments()[0]["media_state"] == "missing"
    assert not context.transport.diagnostics().is_healthy


def test_broken_revision_does_not_block_following_message(context: Context) -> None:
    context.workbot_service.handle_update(_update("broken", 10, PRODUCTION_CHAT, "bad"))
    context.workbot_service.handle_update(_update("after-broken", 11, PRODUCTION_CHAT, "good"))
    with context.workbot.connect() as connection:
        connection.execute(
            """
            UPDATE source_message_revisions SET message_timestamp_utc = 'invalid'
            WHERE source_message_id = 'broken'
            """
        )

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.read_count == 2
    assert result.error_count == 1
    assert result.imported_count == 1
    assert context.repository.list_messages(context.source_id)[0]["source_message_id"] == "after-broken"


def test_late_edit_of_old_message_is_found_by_revision_cursor(context: Context) -> None:
    context.workbot_service.handle_update(_update("old", 10, PRODUCTION_CHAT, "ШУ1 20%"))
    context.workbot_service.handle_update(_update("new", 10, PRODUCTION_CHAT, "ШУ2 30%"))
    context.transport.sync_source(context.source_id, context.gateway)
    context.workbot_service.handle_update(
        _update("old", 10, PRODUCTION_CHAT, "ШУ1 25%", update_type="message_edited")
    )

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.changed_count == 1
    rows = context.repository.list_messages(context.source_id)
    assert [row["source_message_id"] for row in rows] == ["old", "new", "old"]


def test_cursor_identity_mismatch_rescans_without_duplicates(context: Context) -> None:
    context.workbot_service.handle_update(_update("cursor", 10, PRODUCTION_CHAT, "ШУ1 10%"))
    context.transport.sync_source(context.source_id, context.gateway)
    with context.database.connect() as connection:
        connection.execute(
            "UPDATE ProductionInboxSyncState SET cursor_content_hash = ? WHERE source_id = ?",
            ("0" * 64, context.source_id),
        )

    result = context.transport.sync_source(context.source_id, context.gateway)

    assert result.imported_count == 0
    assert result.unchanged_count >= 1
    assert len(context.repository.list_messages(context.source_id)) == 1


def test_inbox_snapshots_are_sql_immutable(context: Context) -> None:
    context.workbot_service.handle_update(_update("immutable", 10, PRODUCTION_CHAT, "ШУ1"))
    context.transport.sync_source(context.source_id, context.gateway)
    with context.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE ProductionInboxMessages SET source_text = 'changed'")


def test_transport_architecture_has_no_production_event_or_workbot_import_dependency() -> None:
    root = Path(__file__).parents[1]
    files = [
        root / "production" / "source_transport_models.py",
        root / "production" / "source_transport_repository.py",
        root / "production" / "source_transport_service.py",
        root / "integrations" / "workbot" / "production_source_gateway.py",
    ]
    combined = "\n".join(path.read_text("utf-8") for path in files)
    assert "ProductionEvent" not in combined
    assert "WorkBotImportRows" not in combined
    assert "ProductionInboxBundle" not in combined
    tree = ast.parse((root / "production" / "source_transport_service.py").read_text("utf-8"))
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "sqlite3" not in imports
    assert "PySide6" not in imports


def _update(
    mid: str,
    sender_id: int,
    chat_id: int,
    text: str,
    attachments: list[dict] | None = None,
    *,
    update_type: str = "message_created",
) -> dict:
    return {
        "update_type": update_type,
        "timestamp": UTC_MS,
        "message": {
            "sender": {
                "user_id": sender_id,
                "first_name": "Unknown",
                "last_name": str(sender_id),
                "username": f"user{sender_id}",
                "is_bot": False,
            },
            "recipient": {"chat_type": "chat", "chat_id": chat_id},
            "timestamp": UTC_MS,
            "body": {
                "mid": mid,
                "seq": abs(hash(mid)) % 1_000_000,
                "text": text,
                "attachments": attachments or [],
            },
        },
    }


def _image(index: int) -> dict:
    return {
        "type": "image",
        "payload": {
            "token": f"photo-{index}",
            "url": f"https://media.test/{index}.jpg",
        },
        "filename": f"photo-{index}.jpg",
    }


def _downgrade_core_to_v4(database: Database) -> None:
    with database.connect(foreign_keys=False) as connection:
        for trigger in (
            "trg_production_inbox_bundle_messages_immutable_delete",
            "trg_production_inbox_bundle_messages_immutable_update",
            "trg_production_inbox_tombstones_immutable_delete",
            "trg_production_inbox_tombstones_immutable_update",
            "trg_production_inbox_attachments_immutable_delete",
            "trg_production_inbox_attachments_immutable_update",
            "trg_production_inbox_messages_immutable_delete",
            "trg_production_inbox_messages_immutable_update",
        ):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in (
            "ProductionInboxBundleMessages",
            "ProductionInboxBundles",
            "ProductionInboxTombstoneSyncState",
            "ProductionInboxSourceTombstones",
            "ProductionInboxAttachments",
            "ProductionInboxMessages",
            "ProductionInboxSyncIssues",
            "ProductionInboxSyncRuns",
            "ProductionInboxSyncState",
            "ProductionInboxSources",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "DELETE FROM SchemaMigrations WHERE component = 'prolog' AND version >= 5"
        )


def _migration_history(database: Database):
    with database.connect() as connection:
        return tuple(connection.execute("SELECT * FROM SchemaMigrations ORDER BY version"))


def _component_hashes(database: Database) -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in database.database_paths().items()
        if name != "prolog"
    }


def _primary_counts(database: Database) -> tuple[int, int, int]:
    with database.connect() as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM ProductionEvents").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM WorkBotImportRows").fetchone()[0],
        )
