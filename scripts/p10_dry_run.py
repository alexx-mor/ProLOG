"""Migrate and exercise P10 on a disposable copy of deployment data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Database
from production.matching_repository import ProductionInboxMatchingRepository
from production.matching_service import ProductionInboxMatchingService


SYNTHETIC_TEXTS = (
    "ШУ1 электромонтаж 70%",
    "ШУ 1 электромонтаж 70 %",
    "ВРУ слесарка",
    "ШУ2 40%",
    "ШУ2 сборка 40%",
    "электромонтаж 70%",
    "ШУ1, ШУ2 70%",
    "ШУ1 50%; ШУ2 70%",
)


def _database(data_dir: Path) -> Database:
    return Database(
        data_dir / "prolog.sqlite3",
        employees_path=data_dir / "employees.sqlite3",
        objects_path=data_dir / "objects.sqlite3",
        products_path=data_dir / "products.sqlite3",
        aliases_path=data_dir / "aliases.sqlite3",
    )


def _create_bundle(database: Database, label: str, text: str, *, media: bool) -> int:
    now = "2026-08-10T12:00:00+00:00"
    with database.connect() as connection:
        source = connection.execute(
            "SELECT * FROM ProductionInboxSources ORDER BY id LIMIT 1"
        ).fetchone()
        if source is None:
            raise RuntimeError("ProductionInboxSource is required for dry-run")
        revision_id = int(connection.execute(
            "SELECT COALESCE(MAX(source_revision_id), 0) + 1 FROM ProductionInboxMessages"
        ).fetchone()[0])
        sequence = int(connection.execute(
            "SELECT COALESCE(MAX(source_sequence), 0) + 1 FROM ProductionInboxMessages"
        ).fetchone()[0])
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        message_id = f"p10-dry-{label}"
        message_row = connection.execute(
            """
            INSERT INTO ProductionInboxMessages (
                uid, source_id, source_type, source_ref, source_message_id,
                source_revision_id, source_revision_number, chat_id,
                sender_max_user_id, sender_display_snapshot,
                message_timestamp_utc, source_received_at_utc,
                transported_at_utc, source_sequence, source_text, content_hash,
                source_content_json, raw_envelope_json, change_kind
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 999001, 'P10 dry-run',
                      ?, ?, ?, ?, ?, ?, '{}', '{}', 'original')
            """,
            (
                str(uuid4()), int(source["id"]), str(source["source_type"]),
                str(source["source_ref"]), message_id, revision_id,
                int(source["chat_id"]), now, now, now, sequence, text, content_hash,
            ),
        )
        inbox_message_id = int(message_row.lastrowid)
        if media:
            connection.execute(
                """
                INSERT INTO ProductionInboxAttachments (
                    uid, inbox_message_id, source_attachment_row_id,
                    source_attachment_id, identity_kind, source_order,
                    attachment_type, mime_type, original_name, source_size,
                    source_download_status, source_sha256, source_storage_key,
                    media_state, source_metadata_json
                ) VALUES (?, ?, ?, ?, 'source_id', 0, 'image', 'image/jpeg',
                          'dry-run.jpg', 4, 'downloaded', ?, 'aa/bb/dry-run.jpg',
                          'available', '{}')
                """,
                (
                    str(uuid4()), inbox_message_id, revision_id,
                    f"p10-photo-{revision_id}", hashlib.sha256(b"test").hexdigest(),
                ),
            )
        bundle_fingerprint = hashlib.sha256(
            f"p10:{label}:{text}:{media}".encode("utf-8")
        ).hexdigest()
        grouping_status = "complete" if media and text else (
            "needs_description" if media else "text_only"
        )
        close_reason = "captioned_media" if media and text else (
            "timeout" if media else "standalone_text"
        )
        bundle_row = connection.execute(
            """
            INSERT INTO ProductionInboxBundles (
                uid, source_id, chat_id, sender_max_user_id,
                sender_display_snapshot, started_at_utc, ended_at_utc,
                grouping_status, close_reason, origin, grouping_rule_version,
                grouping_window_seconds, day_boundary_utc_offset_minutes,
                source_fingerprint, is_current, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, 999001, 'P10 dry-run', ?, ?, ?, ?,
                      'deterministic', 'deterministic-v1', 900, 180, ?, 1, ?, ?)
            """,
            (
                str(uuid4()), int(source["id"]), int(source["chat_id"]), now, now,
                grouping_status, close_reason, bundle_fingerprint, now, now,
            ),
        )
        bundle_id = int(bundle_row.lastrowid)
        role = "captioned_media" if media and text else (
            "photo_source" if media else "text_only"
        )
        connection.execute(
            """
            INSERT INTO ProductionInboxBundleMessages (
                bundle_id, inbox_message_id, bundle_order, message_role
            ) VALUES (?, ?, 0, ?)
            """,
            (bundle_id, inbox_message_id, role),
        )
    return bundle_id


def _result_payload(result, repository) -> dict[str, object]:
    context = repository.load_context()
    products = {item.id: item for item in context.products}
    objects = {item.id: item for item in context.objects}
    stages = {item.id: item for item in context.stages}
    return {
        "bundle_id": result.run.bundle_id,
        "source_text": result.run.source_text,
        "normalized_text": result.run.normalized_text,
        "has_media": result.run.has_media,
        "status": result.run.status.value,
        "created": result.created,
        "proposals": [
            {
                "segment": item.draft.source_segment_text,
                "object": (
                    objects[item.draft.object_id].name
                    if item.draft.object_id in objects else None
                ),
                "object_method": item.draft.object_match_method,
                "product": (
                    products[item.draft.product_id].name
                    if item.draft.product_id in products else None
                ),
                "product_method": item.draft.product_match_method,
                "product_candidates": [
                    {
                        "id": candidate.target_id,
                        "name": products[candidate.target_id].name,
                        "rank": candidate.rank,
                        "score": candidate.score,
                        "method": candidate.method,
                    }
                    for candidate in item.draft.product_candidates
                ],
                "stage": (
                    stages[item.draft.stage_id].name
                    if item.draft.stage_id in stages else None
                ),
                "stage_method": item.draft.stage_match_method,
                "readiness": item.draft.readiness_percent,
                "quality": item.draft.match_quality.value,
                "requires_review": item.draft.requires_review,
                "issues": [issue.code for issue in item.draft.issues],
            }
            for item in result.proposals
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = _database(args.data)
    database.initialize()
    repository = ProductionInboxMatchingRepository(database)
    matcher = ProductionInboxMatchingService(repository)

    original_bundle_ids = repository.current_bundle_ids()
    real = [_result_payload(matcher.match_bundle(item), repository) for item in original_bundle_ids]

    synthetic_ids = [
        _create_bundle(database, str(index), text, media=index in {1, 2})
        for index, text in enumerate(SYNTHETIC_TEXTS, 1)
    ]
    synthetic_ids.append(_create_bundle(database, "photo-only", "", media=True))
    with database.connect() as connection:
        serial_row = connection.execute(
            """
            SELECT serial_number FROM products_db.Products
            WHERE TRIM(COALESCE(serial_number, '')) <> '' ORDER BY id LIMIT 1
            """
        ).fetchone()
    if serial_row is not None:
        synthetic_ids.append(_create_bundle(
            database, "exact-serial", f"заводской номер {serial_row[0]}", media=False
        ))
    synthetic = [
        _result_payload(matcher.match_bundle(item), repository) for item in synthetic_ids
    ]
    rerun_created = sum(
        int(matcher.match_bundle(item).created)
        for item in (*original_bundle_ids, *synthetic_ids)
    )
    with database.connect() as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        versions = {
            str(row["component"]): int(row["version"])
            for row in connection.execute(
                """
                SELECT component, MAX(version) AS version
                FROM SchemaMigrations GROUP BY component
                """
            )
        }
    references = database.check_references()
    diagnostics = matcher.diagnostics()
    payload = {
        "schema_versions": versions,
        "integrity_check": integrity,
        "cross_database_healthy": references.is_valid,
        "real_bundles": real,
        "synthetic": synthetic,
        "rerun_created_count": rerun_created,
        "matching_diagnostics_healthy": diagnostics.is_healthy,
        "matching_diagnostic_kinds": [item.kind.value for item in diagnostics.issues],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
