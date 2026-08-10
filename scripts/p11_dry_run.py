"""Exercise P7-P11 on a disposable copy of the working deployment."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auth import AuthSession, ROLE_ADMIN
from database import Database
from integrations.workbot.production_source_gateway import WorkBotProductionSourceGateway
from production.actor_adapter import actor_from_auth_session
from production.attachment_export import AttachmentExportRequest
from production.models import ProductionEventStatus, ProductionEventType
from production.module import build_production_module
from production.review_models import ReviewDecision, ReviewFilter, ReviewStatus
from workbot.config import WorkBotConfig
from workbot.service import WorkBotService
from workbot.source_models import DownloadedMedia
from workbot.source_repository import WorkBotSourceRepository
from workbot.storage import WorkBotStorage


CHAT_ID = -77703766302910
UTC_MS = 1786374000000
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeClient:
    def __init__(self) -> None:
        self.payloads = {
            f"https://p11.test/{number}.png": PNG + bytes((number,))
            for number in range(1, 10)
        }

    def download_media(self, source_url, _attachment_type, _source_token):
        return DownloadedMedia(self.payloads[source_url], "image/png", Path(source_url).name)

    def send_message(self, *_args, **_kwargs):
        return {}

    def send_file(self, *_args, **_kwargs):
        return {}

    def answer_callback(self, *_args, **_kwargs):
        return {}


def _database(data: Path) -> Database:
    return Database(
        data / "prolog.sqlite3",
        employees_path=data / "employees.sqlite3",
        objects_path=data / "objects.sqlite3",
        products_path=data / "products.sqlite3",
        aliases_path=data / "aliases.sqlite3",
    )


def _update(mid: str, text: str, attachments: list[dict], sequence: int, *, edited=False):
    return {
        "update_type": "message_edited" if edited else "message_created",
        "timestamp": UTC_MS + sequence * 1000,
        "message": {
            "sender": {
                "user_id": 900011,
                "first_name": "P11",
                "last_name": "Dry-run",
                "username": "p11-dry",
                "is_bot": False,
            },
            "recipient": {"chat_type": "chat", "chat_id": CHAT_ID},
            "timestamp": UTC_MS + sequence * 1000,
            "body": {
                "mid": mid,
                "seq": 900000 + sequence,
                "text": text,
                "attachments": attachments,
            },
        },
    }


def _image(number: int) -> dict:
    return {
        "type": "image",
        "payload": {
            "token": f"p11-photo-{number}",
            "url": f"https://p11.test/{number}.png",
        },
        "filename": f"p11-photo-{number}.png",
    }


def _decision(item, actor, *, readiness: int, event_type=ProductionEventType.OBSERVATION,
              correction_source_event_id=None, reason="") -> ReviewDecision:
    if item.product_id is None:
        raise RuntimeError("Dry-run matcher did not resolve serial 3075")
    return ReviewDecision(
        bundle_id=item.bundle_id,
        bundle_fingerprint=item.bundle_fingerprint,
        match_run_id=item.match_run_id,
        proposal_id=item.proposal_id,
        product_id=item.product_id,
        stage_id=item.stage_id,
        readiness_percent=readiness,
        description=item.description_text,
        observed_at_utc=item.observed_at_utc,
        reported_by_employee_id=None,
        actor=actor,
        event_type=event_type,
        change_reason=reason,
        correction_source_event_id=correction_source_event_id,
    )


def _find_item(module, source_text: str):
    return next(
        item for item in module.review.list_items(ReviewFilter.ALL)
        if source_text in item.source_text
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--workbot-db", type=Path, required=True)
    parser.add_argument("--workbot-media", type=Path, required=True)
    parser.add_argument("--attachment-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    database = _database(args.data)
    database.initialize()
    module = build_production_module(database, args.attachment_root)
    source = module.source_transport.register_max_chat(
        "Фотоотчеты Электроцех", CHAT_ID,
        web_url="https://web.max.ru/-77703766302910",
    )
    if source.id is None:
        raise RuntimeError("Production source has no id")

    storage = WorkBotStorage(args.workbot_db)
    storage.initialize()
    client = FakeClient()
    service = WorkBotService(
        WorkBotConfig(
            token="dry-run",
            owner_ids=frozenset({1}),
            database_path=args.workbot_db,
            media_root=args.workbot_media,
            export_dir=args.output.parent,
        ),
        storage,
        client,
    )
    gateway = WorkBotProductionSourceGateway(args.workbot_db, args.workbot_media)
    module.review.set_source_media_reader(gateway)
    actor = actor_from_auth_session(AuthSession(
        "P11 dry-run", ROLE_ADMIN, "Dry-run", "Production", "P11 dry-run"
    ))

    before = _counts(database)
    for number in range(1, 4):
        service.handle_update(_update(
            f"p11-dry-photo-{number}", "", [_image(number)], number
        ))
    service.handle_update(_update(
        "p11-dry-text", "3075 электромонтаж 70%", [], 4
    ))
    transport = module.source_transport.sync_source(source.id, gateway)
    grouping = module.grouping.regroup(as_of_utc=datetime(2026, 8, 10, 15, 10, tzinfo=timezone.utc))
    module.matching.match_all_current()
    item = _find_item(module, "3075 электромонтаж 70%")
    first = module.review.confirm(_decision(item, actor, readiness=70))
    first_event = module.events.get_event(first.production_event_id or 0)
    relations = module.events.events.list_attachments(first_event.id or 0)
    exported = module.exports.export_batch([
        AttachmentExportRequest(
            relation.attachment_id,
            first_event.observed_at_utc,
            "3075",
            "Электромонтаж",
            relation.sort_order,
        )
        for relation in relations
    ], args.output.parent / "exports")
    exported_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in exported.exported_paths]

    service.handle_update(_update(
        "p11-dry-text", "3075 электромонтаж 80%", [], 5, edited=True
    ))
    module.source_transport.sync_source(source.id, gateway)
    module.grouping.regroup(as_of_utc=datetime(2026, 8, 10, 15, 11, tzinfo=timezone.utc))
    module.matching.match_all_current()
    changed = _find_item(module, "3075 электромонтаж 80%")
    if changed.review_status is not ReviewStatus.SOURCE_CHANGED:
        raise RuntimeError("Edited source was not surfaced as changed")
    corrected = module.review.confirm(_decision(
        changed,
        actor,
        readiness=80,
        event_type=ProductionEventType.CORRECTION,
        correction_source_event_id=first_event.id,
        reason="Исправлен текст MAX в dry-run",
    ))

    service.handle_update(_update("p11-dry-split-photo-a", "", [_image(4)], 10))
    service.handle_update(_update("p11-dry-split-photo-b", "", [_image(5)], 11))
    service.handle_update(_update("p11-dry-split-text", "3075, ШУ2 70%", [], 12))
    module.source_transport.sync_source(source.id, gateway)
    module.grouping.regroup(as_of_utc=datetime(2026, 8, 10, 15, 20, tzinfo=timezone.utc))
    module.matching.match_all_current()
    split_item = _find_item(module, "3075, ШУ2 70%")
    split_detail = module.review.detail(split_item.bundle_id, split_item.proposal_id)
    manual = module.review.manual_split(
        split_item.bundle_id,
        ((split_detail.messages[0].id,), tuple(message.id for message in split_detail.messages[1:])),
        actor,
    )

    corrected_event = module.events.get_event(corrected.production_event_id or 0)
    timeline = module.projections.get_product_timeline(item.product_id or 0)
    review_diagnostics = module.review.diagnostics()
    attachment_diagnostics = module.attachments.diagnostics()
    references = database.check_references()
    with database.connect() as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        audit_chain = int(connection.execute(
            """
            SELECT COUNT(*) FROM ProductionEvents event
            JOIN ProductionInboxReviews review ON review.production_event_id=event.id
            JOIN ProductionInboxProposals proposal ON proposal.id=review.proposal_id
            JOIN ProductionInboxMatchRuns run ON run.id=review.match_run_id
            JOIN ProductionInboxBundles bundle ON bundle.id=review.bundle_id
            JOIN ProductionInboxBundleMessages relation ON relation.bundle_id=bundle.id
            JOIN ProductionInboxMessages message ON message.id=relation.inbox_message_id
            WHERE event.id IN (?, ?)
            """,
            (first_event.id, corrected_event.id),
        ).fetchone()[0])
        versions = {
            str(row["component"]): int(row["version"])
            for row in connection.execute(
                "SELECT component,MAX(version) AS version FROM SchemaMigrations GROUP BY component"
            )
        }

    payload = {
        "schema_versions": versions,
        "before_counts": before,
        "after_counts": _counts(database),
        "transport_imported": transport.imported_count,
        "grouping_created": grouping.created_count,
        "matched_product_id": item.product_id,
        "matched_stage_id": item.stage_id,
        "first_event_id": first_event.id,
        "first_event_status_after_correction": module.events.get_event(first_event.id or 0).status.value,
        "corrected_event_id": corrected_event.id,
        "corrected_event_status": corrected_event.status.value,
        "corrected_readiness": corrected_event.readiness_percent,
        "attachment_count": len(relations),
        "attachment_order": [relation.sort_order for relation in relations],
        "source_bytes_preserved": exported_hashes == [
            hashlib.sha256(client.payloads[f"https://p11.test/{number}.png"]).hexdigest()
            for number in range(1, 4)
        ],
        "export_failures": len(exported.failures),
        "timeline_event_count": len(timeline),
        "manual_split_bundle_ids": list(manual),
        "audit_chain_rows": audit_chain,
        "integrity_check": integrity,
        "foreign_key_issue_count": foreign_keys,
        "cross_database_healthy": references.is_valid,
        "attachment_diagnostics_healthy": attachment_diagnostics.is_healthy,
        "review_diagnostics_healthy": review_diagnostics.is_healthy,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _counts(database: Database) -> dict[str, int]:
    tables = (
        "ProductionEvents", "Attachments", "WorkLogEntries",
        "ProductionInboxMessages", "ProductionInboxBundles",
        "ProductionInboxMatchRuns", "ProductionInboxReviews",
    )
    with database.connect() as connection:
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in tables
        }


if __name__ == "__main__":
    main()
