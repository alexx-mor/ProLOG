"""Migration, storage and service tests for production attachments."""

from __future__ import annotations

import ast
import hashlib
import io
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from database import Database
from config import attachment_root_path
from constants import ATTACHMENTS_DIR
from models import AppSettings
from production.attachment_repository import AttachmentRepository
from production.attachment_service import AttachmentService
from production.attachment_types import (
    AttachmentDiagnosticKind,
    AttachmentStorageReference,
)
from production.errors import (
    AttachmentIntegrityError,
    AttachmentPathError,
    AttachmentRootUnavailableError,
)
from production.local_attachment_store import LocalAttachmentStore
from production.migrations import apply_attachments_migration
from schema_migrations import Migration, MigrationComponent, MigrationRunner


UTC_NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _image_bytes(image_format: str, size: tuple[int, int] = (13, 7)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (24, 96, 160)).save(output, format=image_format)
    return output.getvalue()


def _jpeg_with_utc_offset() -> bytes:
    output = io.BytesIO()
    exif = Image.Exif()
    exif[36867] = "2026:08:09 12:30:00"
    exif[36881] = "+03:00"
    Image.new("RGB", (8, 6), (20, 80, 140)).save(output, format="JPEG", exif=exif)
    return output.getvalue()


def _service(database: Database, root: Path) -> AttachmentService:
    return AttachmentService(
        AttachmentRepository(database),
        LocalAttachmentStore(root),
    )


def _downgrade_core_to_v2(database: Database) -> None:
    with database.connect() as connection:
        connection.execute("DROP TABLE Attachments")
        connection.execute(
            "DELETE FROM SchemaMigrations WHERE component = 'prolog' AND version = 3"
        )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migration_v2_to_v3_has_exact_attachment_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_core_to_v2(database)

    database.initialize()

    versions = {item.component: item.current_version for item in database.schema_versions()}
    assert versions == {
        "prolog": 3,
        "employees": 1,
        "objects": 1,
        "products": 1,
        "aliases": 1,
    }
    with database.connect() as connection:
        columns = [
            (row["name"], row["type"], bool(row["notnull"]), bool(row["pk"]))
            for row in connection.execute("PRAGMA table_info(Attachments)")
        ]
        indexes = {
            row["name"]: bool(row["unique"])
            for row in connection.execute("PRAGMA index_list(Attachments)")
        }
    assert columns == [
        ("id", "INTEGER", False, True),
        ("uid", "TEXT", True, False),
        ("storage_key", "TEXT", True, False),
        ("sha256", "TEXT", True, False),
        ("original_name", "TEXT", True, False),
        ("mime_type", "TEXT", True, False),
        ("size_bytes", "INTEGER", True, False),
        ("width", "INTEGER", False, False),
        ("height", "INTEGER", False, False),
        ("captured_at_utc", "TEXT", False, False),
        ("received_at_utc", "TEXT", True, False),
        ("source_type", "TEXT", False, False),
        ("source_message_id", "TEXT", False, False),
        ("source_attachment_id", "TEXT", False, False),
        ("created_at_utc", "TEXT", True, False),
    ]
    assert indexes == {
        "ux_attachments_source_identity": True,
        "idx_attachments_source_message": False,
        "idx_attachments_storage_key": False,
        "idx_attachments_sha256": False,
        "sqlite_autoindex_Attachments_1": True,
    }


def test_v3_migration_is_idempotent_and_preserves_legacy_rows(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO Settings(key, value) VALUES ('p3-test', 'preserve-me')"
        )
        before_stages = [
            tuple(row)
            for row in connection.execute("SELECT * FROM ProductionStages ORDER BY id")
        ]
    _downgrade_core_to_v2(database)

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM Settings WHERE key = 'p3-test'"
        ).fetchone()[0] == "preserve-me"
        assert [
            tuple(row)
            for row in connection.execute("SELECT * FROM ProductionStages ORDER BY id")
        ] == before_stages
        assert connection.execute(
            "SELECT COUNT(*) FROM SchemaMigrations WHERE component = 'prolog' AND version = 3"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0] == 0


def test_v3_migration_rolls_back_on_artificial_failure(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "rollback-v3.sqlite3")
    connection.row_factory = sqlite3.Row
    component = MigrationComponent("prolog")
    baseline = Migration(1, "Baseline", "rollback-p3-v1", lambda _connection: None)
    stage = Migration(2, "Stages", "rollback-p3-v2", lambda _connection: None)
    MigrationRunner((component,), (baseline, stage), app_version="test").migrate(connection)

    def fail_after_schema(active: sqlite3.Connection) -> None:
        apply_attachments_migration(active)
        raise RuntimeError("artificial P3 failure")

    runner = MigrationRunner(
        (component,),
        (
            baseline,
            stage,
            Migration(3, "Attachments", "rollback-p3-v3", fail_after_schema),
        ),
        app_version="test",
    )
    with pytest.raises(RuntimeError, match="artificial P3 failure"):
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
    assert "Attachments" not in tables
    assert history == [1, 2]


def test_v3_migration_does_not_change_component_databases(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_core_to_v2(database)
    component_paths = {
        key: path for key, path in database.database_paths().items() if key != "prolog"
    }
    before = {key: _file_hash(path) for key, path in component_paths.items()}

    database.initialize()

    assert {key: _file_hash(path) for key, path in component_paths.items()} == before


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    (("JPEG", "image/jpeg"), ("PNG", "image/png")),
)
def test_store_preserves_image_bytes_and_metadata(
    tmp_path: Path,
    image_format: str,
    mime_type: str,
) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database, tmp_path / "attachments")
    content = _image_bytes(image_format)

    attachment = service.store_bytes(
        content,
        original_name=f"photo.{image_format.lower()}",
        received_at_utc=UTC_NOW,
    )

    assert attachment.id is not None
    assert isinstance(attachment.uid, UUID) and attachment.uid.version == 4
    assert attachment.sha256 == hashlib.sha256(content).hexdigest()
    assert attachment.mime_type == mime_type
    assert (attachment.width, attachment.height) == (13, 7)
    assert attachment.captured_at_utc is None
    assert service.store.read(attachment.storage_key) == content
    assert service.store.verify(attachment.storage_key, attachment.sha256).is_valid
    assert not Path(attachment.storage_key).is_absolute()
    assert "photo" not in attachment.storage_key


def test_storage_key_survives_attachment_root_move(tmp_path: Path) -> None:
    content = _image_bytes("PNG")
    sha256 = hashlib.sha256(content).hexdigest()
    first_root = tmp_path / "first"
    first_store = LocalAttachmentStore(first_root)
    stored = first_store.put(content, sha256)
    second_root = tmp_path / "second"
    shutil.copytree(first_root, second_root)

    moved_store = LocalAttachmentStore(second_root)

    assert moved_store.read(stored.storage_key) == content
    assert moved_store.verify(stored.storage_key, sha256).is_valid


def test_reliable_exif_capture_time_is_converted_to_utc(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database, tmp_path / "attachments")

    attachment = service.store_bytes(
        _jpeg_with_utc_offset(),
        original_name="with-exif.jpg",
        received_at_utc=UTC_NOW,
    )

    assert attachment.captured_at_utc == datetime(
        2026,
        8,
        9,
        9,
        30,
        tzinfo=timezone.utc,
    )


def test_unqualified_exif_time_is_not_assumed_to_be_utc(tmp_path: Path) -> None:
    output = io.BytesIO()
    exif = Image.Exif()
    exif[36867] = "2026:08:09 12:30:00"
    Image.new("RGB", (8, 6)).save(output, format="JPEG", exif=exif)
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()

    attachment = _service(database, tmp_path / "attachments").store_bytes(
        output.getvalue(),
        original_name="without-offset.jpg",
        received_at_utc=UTC_NOW,
    )

    assert attachment.captured_at_utc is None


def test_attachment_root_is_configurable_without_changing_storage_keys(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "server-share" / "attachments"

    assert attachment_root_path(AppSettings()) == ATTACHMENTS_DIR
    assert attachment_root_path(
        AppSettings(attachment_root=str(configured))
    ) == configured


def test_content_is_deduplicated_but_attachment_history_is_not(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    root = tmp_path / "attachments"
    service = _service(database, root)
    content = _image_bytes("PNG")

    first = service.store_bytes(
        content,
        original_name="first.png",
        received_at_utc=UTC_NOW,
        source_type="manual",
        source_message_id="message-1",
        source_attachment_id="photo-1",
    )
    second = service.store_bytes(
        content,
        original_name="second.png",
        received_at_utc=UTC_NOW,
        source_type="import",
        source_message_id="message-2",
        source_attachment_id="photo-2",
    )

    assert first.id != second.id
    assert first.uid != second.uid
    assert first.sha256 == second.sha256
    assert first.storage_key == second.storage_key
    assert len(AttachmentRepository(database).find_by_sha256(first.sha256)) == 2
    assert len([path for path in root.rglob("*") if path.is_file()]) == 1


def test_same_complete_source_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database, tmp_path / "attachments")
    content = _image_bytes("JPEG")
    source = {
        "source_type": "future-integration",
        "source_message_id": "message-42",
        "source_attachment_id": "attachment-7",
    }

    first = service.store_bytes(
        content,
        original_name="first.jpg",
        received_at_utc=UTC_NOW,
        **source,
    )
    second = service.store_bytes(
        content,
        original_name="redelivery.jpg",
        received_at_utc=UTC_NOW,
        **source,
    )

    assert second == first
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0] == 1


def test_same_source_with_different_content_is_integrity_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database, tmp_path / "attachments")
    source = {
        "source_type": "future-integration",
        "source_message_id": "message-42",
        "source_attachment_id": "attachment-7",
    }
    service.store_bytes(
        _image_bytes("JPEG", (10, 10)),
        original_name="first.jpg",
        received_at_utc=UTC_NOW,
        **source,
    )

    with pytest.raises(AttachmentIntegrityError, match="различающимся"):
        service.store_bytes(
            _image_bytes("JPEG", (20, 20)),
            original_name="conflict.jpg",
            received_at_utc=UTC_NOW,
            **source,
        )


@pytest.mark.parametrize(
    "storage_key",
    (
        "../escape",
        "aa/../../escape",
        "/absolute/path",
        "C:/Windows/file",
        "C:\\Windows\\file",
        "\\\\server\\share\\file",
    ),
)
def test_storage_key_path_traversal_and_absolute_paths_are_rejected(
    tmp_path: Path,
    storage_key: str,
) -> None:
    with pytest.raises(AttachmentPathError):
        LocalAttachmentStore(tmp_path / "attachments").resolve(storage_key)


def test_unavailable_root_is_reported_explicitly(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("occupied", encoding="utf-8")
    store = LocalAttachmentStore(root)
    content = b"payload"
    sha256 = hashlib.sha256(content).hexdigest()

    with pytest.raises(AttachmentRootUnavailableError):
        store.put(content, sha256)
    report = store.diagnostics(())
    assert report.count(AttachmentDiagnosticKind.ROOT_UNAVAILABLE) == 1


def test_write_failure_does_not_commit_metadata(tmp_path: Path, monkeypatch) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database, tmp_path / "attachments")

    def fail_temp_file(*_args, **_kwargs):
        raise OSError("artificial write failure")

    monkeypatch.setattr(
        "production.local_attachment_store.tempfile.NamedTemporaryFile",
        fail_temp_file,
    )
    with pytest.raises(AttachmentRootUnavailableError):
        service.store_bytes(
            _image_bytes("PNG"),
            original_name="failed.png",
            received_at_utc=UTC_NOW,
        )
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM Attachments").fetchone()[0] == 0


def test_database_failure_after_file_write_is_detected_as_orphan(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    root = tmp_path / "attachments"
    service = _service(database, root)
    with database.connect() as connection:
        connection.execute("DROP TABLE Attachments")

    with pytest.raises(sqlite3.OperationalError):
        service.store_bytes(
            _image_bytes("PNG"),
            original_name="orphan.png",
            received_at_utc=UTC_NOW,
        )

    report = LocalAttachmentStore(root).diagnostics(())
    assert report.count(AttachmentDiagnosticKind.ORPHAN_FILE) == 1


def test_diagnostics_detects_missing_corrupt_orphan_temp_and_invalid_key(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    root = tmp_path / "attachments"
    service = _service(database, root)
    first = service.store_bytes(
        _image_bytes("PNG", (9, 5)),
        original_name="missing.png",
        received_at_utc=UTC_NOW,
    )
    second = service.store_bytes(
        _image_bytes("JPEG", (11, 6)),
        original_name="corrupt.jpg",
        received_at_utc=UTC_NOW,
    )
    service.store.resolve(first.storage_key).unlink()
    service.store.resolve(second.storage_key).write_bytes(b"corrupt")
    orphan = root / "ff" / "ee" / ("f" * 64)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    temporary = root / "aa" / "bb" / ".tmp-left.partial"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(b"temporary")
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO Attachments (
                uid, storage_key, sha256, original_name, mime_type, size_bytes,
                received_at_utc, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "00000000-0000-4000-8000-000000000099",
                "../invalid",
                "0" * 64,
                "invalid.bin",
                "application/octet-stream",
                1,
                UTC_NOW.isoformat(),
                UTC_NOW.isoformat(),
            ),
        )

    report = service.diagnostics()

    assert report.count(AttachmentDiagnosticKind.MISSING_FILE) == 1
    assert report.count(AttachmentDiagnosticKind.HASH_MISMATCH) == 1
    assert report.count(AttachmentDiagnosticKind.ORPHAN_FILE) == 1
    assert report.count(AttachmentDiagnosticKind.TEMP_FILE) == 1
    assert report.count(AttachmentDiagnosticKind.INVALID_STORAGE_KEY) == 1


def test_unknown_file_is_stored_without_crashing_application(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    service = _service(database, tmp_path / "attachments")
    content = b"not a supported image\x00\x01"

    attachment = service.store_bytes(
        content,
        original_name="unknown.data",
        received_at_utc=UTC_NOW,
    )

    assert attachment.mime_type == "application/octet-stream"
    assert attachment.width is None and attachment.height is None
    assert service.store.read(attachment.storage_key) == content


def test_existing_content_with_wrong_hash_is_not_overwritten(tmp_path: Path) -> None:
    store = LocalAttachmentStore(tmp_path / "attachments")
    content = b"expected"
    sha256 = hashlib.sha256(content).hexdigest()
    target = store.resolve(store.storage_key_for_sha256(sha256))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"different")

    with pytest.raises(AttachmentIntegrityError, match="другой SHA-256"):
        store.put(content, sha256)
    assert target.read_bytes() == b"different"


def test_quarantine_moves_content_inside_managed_root(tmp_path: Path) -> None:
    store = LocalAttachmentStore(tmp_path / "attachments")
    content = b"quarantine-me"
    sha256 = hashlib.sha256(content).hexdigest()
    stored = store.put(content, sha256)

    quarantine_key = store.quarantine(stored.storage_key)

    assert not store.exists(stored.storage_key)
    assert quarantine_key.startswith("quarantine/")
    assert store.resolve(quarantine_key).read_bytes() == content


def test_attachment_infrastructure_boundaries() -> None:
    root = Path(__file__).parents[1]
    store_tree = ast.parse((root / "production" / "attachment_store.py").read_text("utf-8"))
    repository_tree = ast.parse(
        (root / "production" / "attachment_repository.py").read_text("utf-8")
    )
    service_tree = ast.parse(
        (root / "production" / "attachment_service.py").read_text("utf-8")
    )

    assert "PySide6" not in _imported_roots(store_tree)
    assert not ({"os", "pathlib", "shutil"} & _imported_roots(repository_tree))
    assert not ({"workbot", "PySide6"} & _imported_roots(service_tree))


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
