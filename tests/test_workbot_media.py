"""P7 tests for immutable MAX source revisions and WorkBot media."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from constants import APP_VERSION
from infrastructure.content_store import ContentPathError
from schema_migrations import Migration, MigrationComponent, MigrationRunner
from workbot.config import WorkBotConfig
from workbot.media_store import WorkBotMediaStore
from workbot.models import ParsedReport
from workbot.source_models import (
    DownloadedMedia,
    MediaDownloadStatus,
    MediaUnavailableError,
    WorkBotMediaDiagnosticKind,
)
from workbot.source_repository import WorkBotSourceRepository
from workbot.source_service import WorkBotSourceService
from workbot.storage import WorkBotStorage
from workbot.service import WorkBotService


REPORT_TEXT = """Дата: 30.07.2026
Виды работ: Монтаж шкафа
Затраченное время: 8
Объект: Цех № 1
Местонахождение: Производство"""


class FakeMediaClient:
    def __init__(self, payloads: dict[str, bytes] | None = None) -> None:
        self.payloads = payloads or {}
        self.failures: dict[str, int] = {}
        self.download_calls: list[str] = []
        self.messages: list[str] = []

    def download_media(self, source_url, attachment_type, source_token):
        identity = source_url or source_token or ""
        self.download_calls.append(identity)
        if self.failures.get(identity, 0) > 0:
            self.failures[identity] -= 1
            raise OSError("temporary network error")
        if identity not in self.payloads:
            raise MediaUnavailableError("media unavailable")
        suffix = Path(source_url or "photo.jpg").suffix or ".bin"
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        return DownloadedMedia(self.payloads[identity], mime, f"downloaded{suffix}")

    def send_message(self, text, **_kwargs):
        self.messages.append(text)
        return {}

    def send_file(self, *_args, **_kwargs):
        return {}

    def answer_callback(self, *_args, **_kwargs):
        return {}


@pytest.fixture
def context(tmp_path: Path):
    database = tmp_path / "workbot.sqlite3"
    media_root = tmp_path / "workbot_media"
    storage = WorkBotStorage(database)
    storage.initialize()
    client = FakeMediaClient(
        {
            "https://media.example/a.jpg": b"photo-a",
            "https://media.example/b.jpg": b"photo-b",
            "https://media.example/c.png": b"photo-c",
        }
    )
    config = WorkBotConfig(
        token="test",
        owner_ids=frozenset({1}),
        database_path=database,
        media_root=media_root,
        export_dir=tmp_path,
        media_retry_base_seconds=1,
    )
    service = WorkBotService(config, storage, client)
    return storage, client, service, media_root


def test_migration_v1_to_v2_preserves_legacy_rows(tmp_path: Path) -> None:
    storage = WorkBotStorage(tmp_path / "workbot.sqlite3")
    _initialize_workbot_v1(storage)
    storage.upsert_user(10, "Иван", "Иванов", "ivanov")
    received = datetime(2026, 8, 10, 9, 0)
    storage.record_message("legacy-1", 100, 10, received, REPORT_TEXT)
    storage.save_report(
        "legacy-1",
        10,
        "Иванов Иван",
        ParsedReport(received.date(), "Монтаж", 8, "Цех", "Производство"),
    )
    before = _legacy_signature(storage.path)

    storage.initialize()
    storage.initialize()

    assert storage.schema_versions()[0].current_version == 2
    assert _legacy_signature(storage.path) == before
    with storage.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "source_messages",
        "source_message_revisions",
        "source_message_attachments",
        "source_message_tombstones",
    } <= tables


def test_migration_v2_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    storage = WorkBotStorage(tmp_path / "workbot.sqlite3")
    _initialize_workbot_v1(storage)

    def fail(connection):
        connection.execute("CREATE TABLE should_rollback(id INTEGER)")
        raise RuntimeError("artificial migration failure")

    monkeypatch.setattr("workbot.storage.apply_workbot_source_media_migration", fail)
    with pytest.raises(RuntimeError, match="artificial"):
        storage.initialize()
    with storage.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'should_rollback'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations"
        ).fetchone()[0] == 1


def test_text_only_source_keeps_existing_parser_behavior(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(_update("text-1", 10, 100, REPORT_TEXT))

    assert len(storage.reports()) == 1
    assert storage.reports()[0].hours == 8
    assert _counts(storage) == (1, 1, 0)


def test_photo_only_and_unknown_employee_are_archived(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(
        _update("photo-only", 999, 321, "", [_image("a", "https://media.example/a.jpg")])
    )

    assert storage.reports() == []
    assert _counts(storage) == (1, 1, 1)
    attachment = _attachments(storage)[0]
    assert attachment["download_status"] == "downloaded"
    assert attachment["source_order"] == 0
    with storage.connect() as connection:
        row = connection.execute(
            "SELECT chat_id, sender_max_user_id FROM source_messages"
        ).fetchone()
    assert tuple(row) == (321, 999)


def test_multiple_photos_preserve_source_order_and_original_bytes(context) -> None:
    storage, client, service, root = context
    attachments = [
        _image("a", "https://media.example/a.jpg"),
        _image("b", "https://media.example/b.jpg"),
        _image("c", "https://media.example/c.png"),
    ]
    service.handle_update(_update("ordered", 10, -500, "", attachments))

    rows = _attachments(storage)
    assert [row["source_attachment_id"] for row in rows] == ["a", "b", "c"]
    assert [row["source_order"] for row in rows] == [0, 1, 2]
    assert client.download_calls == [
        "https://media.example/a.jpg",
        "https://media.example/b.jpg",
        "https://media.example/c.png",
    ]
    assert [
        WorkBotMediaStore(root).read(str(row["storage_key"])) for row in rows
    ] == [b"photo-a", b"photo-b", b"photo-c"]


def test_photo_caption_and_text_with_attachments_are_preserved(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(
        _update(
            "caption",
            10,
            100,
            "ШУ1 электромонтаж 70%",
            [_image("a", "https://media.example/a.jpg")],
        )
    )
    service.handle_update(
        _update(
            "report-with-photo",
            10,
            100,
            REPORT_TEXT,
            [_image("b", "https://media.example/b.jpg")],
        )
    )

    with storage.connect() as connection:
        texts = [
            row[0]
            for row in connection.execute(
                "SELECT source_text FROM source_message_revisions ORDER BY id"
            )
        ]
    assert texts == ["ШУ1 электромонтаж 70%", REPORT_TEXT]
    assert len(storage.reports()) == 1


def test_identical_redelivery_is_idempotent(context) -> None:
    storage, _client, service, root = context
    update = _update(
        "same",
        10,
        100,
        "ШУ1 70%",
        [_image("a", "https://media.example/a.jpg")],
    )
    service.handle_update(update)
    service.handle_update(update)

    assert _counts(storage) == (1, 1, 1)
    assert len([path for path in root.rglob("*") if path.is_file()]) == 1


def test_edited_caption_creates_revision_and_preserves_old_revision(context) -> None:
    storage, _client, service, _root = context
    first = _update(
        "edited",
        10,
        100,
        "ШУ1 60%",
        [_image("a", "https://media.example/a.jpg")],
    )
    second = _update(
        "edited",
        10,
        100,
        "ШУ1 70%",
        [_image("a", "https://media.example/a.jpg")],
        update_type="message_edited",
    )
    service.handle_update(first)
    service.handle_update(second)

    with storage.connect() as connection:
        revisions = connection.execute(
            """
            SELECT revision_number, source_text, edited_at_utc
            FROM source_message_revisions ORDER BY revision_number
            """
        ).fetchall()
        media = connection.execute(
            "SELECT revision_id, storage_key FROM source_message_attachments ORDER BY id"
        ).fetchall()
    assert [(row["revision_number"], row["source_text"]) for row in revisions] == [
        (1, "ШУ1 60%"),
        (2, "ШУ1 70%"),
    ]
    assert revisions[0]["edited_at_utc"] is None
    assert revisions[1]["edited_at_utc"] is not None
    assert media[0]["revision_id"] != media[1]["revision_id"]
    assert media[0]["storage_key"] == media[1]["storage_key"]


def test_download_failure_is_retryable_after_restart(context) -> None:
    storage, client, service, _root = context
    url = "https://media.example/a.jpg"
    client.failures[url] = 1
    service.handle_update(_update("retry", 10, 100, "", [_image("a", url)]))
    first = _attachments(storage)[0]
    assert first["download_status"] == "failed"
    with storage.connect() as connection:
        connection.execute(
            "UPDATE source_message_attachments SET next_retry_at_utc = NULL"
        )

    restarted = WorkBotService(service.config, storage, client)
    assert restarted.retry_source_media() == 1
    second = _attachments(storage)[0]
    assert second["download_status"] == "downloaded"
    assert second["download_attempts"] == 2


def test_no_locator_is_explicitly_unavailable(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(
        _update("unavailable", 10, 100, "", [{"type": "image", "payload": {}}])
    )
    row = _attachments(storage)[0]
    assert row["download_status"] == "unavailable"
    assert row["last_error"]


def test_db_failure_after_physical_write_becomes_orphan(context, monkeypatch) -> None:
    storage, _client, service, root = context

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("artificial DB failure")

    monkeypatch.setattr(service.source.repository, "complete_download", fail)
    service.handle_update(
        _update("orphan", 10, 100, "", [_image("a", "https://media.example/a.jpg")])
    )
    report = service.source.diagnostics()
    assert report.count(WorkBotMediaDiagnosticKind.ORPHAN_FILE) == 1
    assert len([path for path in root.rglob("*") if path.is_file()]) == 1


def test_diagnostics_find_missing_corrupt_temp_failed_and_stale(context) -> None:
    storage, client, service, root = context
    service.handle_update(
        _update("missing", 10, 100, "", [_image("a", "https://media.example/a.jpg")])
    )
    missing = _attachments(storage)[0]
    WorkBotMediaStore(root).resolve(str(missing["storage_key"])).unlink()
    service.handle_update(
        _update("corrupt", 10, 100, "", [_image("b", "https://media.example/b.jpg")])
    )
    corrupt = _attachments(storage)[1]
    WorkBotMediaStore(root).resolve(str(corrupt["storage_key"])).write_bytes(b"bad")
    client.failures["https://media.example/c.png"] = 1
    service.handle_update(
        _update("failed", 10, 100, "", [_image("c", "https://media.example/c.png")])
    )
    temp = root / "aa" / "bb" / ".tmp-interrupted.partial"
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_bytes(b"temp")
    with storage.connect() as connection:
        connection.execute(
            """
            UPDATE source_message_attachments
            SET download_status = 'pending',
                received_at_utc = '2020-01-01T00:00:00+00:00'
            WHERE source_attachment_id = 'c'
            """
        )

    report = service.source.diagnostics(pending_age=timedelta(minutes=1))
    assert report.count(WorkBotMediaDiagnosticKind.MISSING_FILE) == 1
    assert report.count(WorkBotMediaDiagnosticKind.HASH_MISMATCH) == 1
    assert report.count(WorkBotMediaDiagnosticKind.TEMP_FILE) == 1
    assert report.count(WorkBotMediaDiagnosticKind.STALE_PENDING) == 1


def test_storage_relocation_dedup_and_traversal(context, tmp_path: Path) -> None:
    storage, _client, service, root = context
    service.handle_update(
        _update("first", 10, 100, "", [_image("a", "https://media.example/a.jpg")])
    )
    service.handle_update(
        _update("second", 10, 100, "", [_image("different", "https://media.example/a.jpg")])
    )
    rows = _attachments(storage)
    assert rows[0]["sha256"] == rows[1]["sha256"]
    assert rows[0]["storage_key"] == rows[1]["storage_key"]
    assert len([path for path in root.rglob("*") if path.is_file()]) == 1
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    source_file = WorkBotMediaStore(root).resolve(str(rows[0]["storage_key"]))
    target_file = WorkBotMediaStore(relocated).resolve(str(rows[0]["storage_key"]))
    target_file.parent.mkdir(parents=True)
    target_file.write_bytes(source_file.read_bytes())
    assert WorkBotMediaStore(relocated).read(str(rows[0]["storage_key"])) == b"photo-a"
    with pytest.raises(ContentPathError):
        WorkBotMediaStore(root).resolve("../escape")


def test_message_removed_creates_real_tombstone_without_deleting_history(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(_update("removed", 10, 100, "before delete"))
    service.handle_update(
        {
            "update_type": "message_removed",
            "timestamp": 1786374000000,
            "chat_id": 100,
            "message_id": "removed",
        }
    )
    assert _counts(storage)[:2] == (1, 1)
    with storage.connect() as connection:
        message = connection.execute(
            "SELECT is_deleted, deleted_at_utc FROM source_messages"
        ).fetchone()
        tombstones = connection.execute(
            "SELECT COUNT(*) FROM source_message_tombstones"
        ).fetchone()[0]
    assert message["is_deleted"] == 1
    assert message["deleted_at_utc"]
    assert tombstones == 1


def test_message_removed_respects_allowed_chat_filter(context) -> None:
    storage, _client, service, _root = context
    service.config = replace(service.config, allowed_chat_ids=frozenset({100}))

    service.handle_update(
        {
            "update_type": "message_removed",
            "timestamp": 1786374000000,
            "chat_id": 999,
            "message_id": "outside-chat",
        }
    )

    assert _counts(storage) == (0, 0, 0)


def test_reverse_download_completion_does_not_change_source_order(context) -> None:
    storage, client, service, root = context
    client.failures.update(
        {
            "https://media.example/a.jpg": 1,
            "https://media.example/b.jpg": 1,
            "https://media.example/c.png": 1,
        }
    )
    service.handle_update(
        _update(
            "reverse-completion",
            10,
            100,
            "",
            [
                _image("a", "https://media.example/a.jpg"),
                _image("b", "https://media.example/b.jpg"),
                _image("c", "https://media.example/c.png"),
            ],
        )
    )
    repository = WorkBotSourceRepository(storage)
    store = WorkBotMediaStore(root)
    rows = _attachments(storage)
    for row, content in reversed(list(zip(rows, (b"photo-a", b"photo-b", b"photo-c")))):
        sha256 = hashlib.sha256(content).hexdigest()
        stored = store.put(content, sha256)
        repository.complete_download(
            int(row["id"]),
            sha256=sha256,
            storage_key=stored.storage_key,
            size_bytes=len(content),
            mime_type="image/jpeg",
            original_name="photo.jpg",
            downloaded_at_utc=datetime.now(timezone.utc),
        )
    ordered = repository.list_revision_attachments(int(rows[0]["revision_id"]))
    assert [item.source_attachment_id for item in ordered] == ["a", "b", "c"]


def test_revision_inconsistency_and_unsafe_key_are_diagnosed(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(
        _update("diagnostic", 10, 100, "", [_image("a", "https://media.example/a.jpg")])
    )
    with storage.connect() as connection:
        connection.execute(
            "UPDATE source_message_revisions SET content_json = '{\"changed\":true}'"
        )
        connection.execute(
            "UPDATE source_message_attachments SET storage_key = '../escape'"
        )
    report = service.source.diagnostics()
    assert report.count(WorkBotMediaDiagnosticKind.REVISION_CONTENT_INCONSISTENCY) == 1
    assert report.count(WorkBotMediaDiagnosticKind.UNSAFE_STORAGE_KEY) == 1


def test_unavailable_media_root_is_explicit(context) -> None:
    _storage, _client, service, root = context
    root.rmdir()
    root.write_text("occupied", encoding="utf-8")

    report = service.source.diagnostics()

    assert report.count(WorkBotMediaDiagnosticKind.ROOT_UNAVAILABLE) == 1


def test_explicit_historical_backfill_does_not_start_media_archive(context) -> None:
    storage, _client, service, _root = context
    service.handle_update(
        _update(
            "historical",
            10,
            100,
            REPORT_TEXT,
            [_image("a", "https://media.example/a.jpg")],
        ),
        historical=True,
    )

    assert _counts(storage) == (0, 0, 0)
    assert len(storage.reports()) == 1


def test_source_media_architecture_boundaries() -> None:
    root = Path(__file__).parents[1]
    files = [
        root / "workbot" / "source_models.py",
        root / "workbot" / "source_repository.py",
        root / "workbot" / "source_service.py",
        root / "workbot" / "media_store.py",
    ]
    imported: set[str] = set()
    combined = ""
    for path in files:
        source = path.read_text("utf-8")
        combined += source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert "production" not in imported
    assert "PySide6" not in imported
    assert "ProductionEvent" not in combined
    assert "WorkBotImportRows" not in combined


def _initialize_workbot_v1(storage: WorkBotStorage) -> None:
    baseline = Migration(
        version=1,
        name="WorkBot 0.5.8 baseline",
        fingerprint="workbot-schema-baseline-0.5.8-v1",
        apply=storage._apply_baseline_migration,
    )
    runner = MigrationRunner(
        (MigrationComponent("workbot", "main"),),
        (baseline,),
        app_version=APP_VERSION,
    )
    with storage.connect() as connection:
        runner.migrate(connection)


def _legacy_signature(path: Path) -> dict[str, tuple[tuple, ...]]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: tuple(connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
            for table in (
                "bot_state",
                "users",
                "chats",
                "messages",
                "reports",
                "historical_reports",
            )
        }
    finally:
        connection.close()


def _counts(storage: WorkBotStorage) -> tuple[int, int, int]:
    values = WorkBotSourceRepository(storage).counts()
    return values["messages"], values["revisions"], values["attachments"]


def _attachments(storage: WorkBotStorage):
    with storage.connect() as connection:
        return connection.execute(
            "SELECT * FROM source_message_attachments ORDER BY revision_id, source_order"
        ).fetchall()


def _image(identity: str, url: str) -> dict:
    return {
        "type": "image",
        "payload": {"token": identity, "url": url},
        "filename": Path(url).name,
    }


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
        "timestamp": 1786374000000,
        "message": {
            "sender": {
                "user_id": sender_id,
                "first_name": "Иван",
                "last_name": "Иванов",
                "username": f"user{sender_id}",
                "is_bot": False,
            },
            "recipient": {"chat_type": "chat", "chat_id": chat_id},
            "timestamp": 1786374000000,
            "body": {
                "mid": mid,
                "seq": 42,
                "text": text,
                "attachments": attachments or [],
            },
        },
    }
