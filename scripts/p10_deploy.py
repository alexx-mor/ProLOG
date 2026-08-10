"""Apply P10 and build proposals for current bundles in a selected deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Database
from production.attachment_repository import AttachmentRepository
from production.attachment_service import AttachmentService
from production.local_attachment_store import LocalAttachmentStore
from production.matching_repository import ProductionInboxMatchingRepository
from production.matching_service import ProductionInboxMatchingService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--attachment-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = Database(
        args.data / "prolog.sqlite3",
        employees_path=args.data / "employees.sqlite3",
        objects_path=args.data / "objects.sqlite3",
        products_path=args.data / "products.sqlite3",
        aliases_path=args.data / "aliases.sqlite3",
    )
    database.initialize()
    repository = ProductionInboxMatchingRepository(database)
    matcher = ProductionInboxMatchingService(repository)
    results = matcher.match_all_current()
    matching_diagnostics = matcher.diagnostics()
    attachment_diagnostics = AttachmentService(
        AttachmentRepository(database), LocalAttachmentStore(args.attachment_root)
    ).diagnostics()
    references = database.check_references()
    with database.connect() as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_issues = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in (
                "ProductionEvents", "Attachments", "WorkLogEntries",
                "ProductionInboxMessages", "ProductionInboxBundles",
                "ProductionStageAliases", "ProductionInboxMatchRuns",
                "ProductionInboxProposals",
            )
        }
    payload = {
        "schema_versions": {
            item.component: item.current_version for item in database.schema_versions()
        },
        "integrity_check": integrity,
        "foreign_key_issue_count": foreign_key_issues,
        "cross_database_healthy": references.is_valid,
        "attachment_diagnostics_healthy": attachment_diagnostics.is_healthy,
        "matching_diagnostics_healthy": matching_diagnostics.is_healthy,
        "counts": counts,
        "match_results": [
            {
                "bundle_id": result.run.bundle_id,
                "source_text": result.run.source_text,
                "created": result.created,
                "status": result.run.status.value,
                "proposals": [
                    {
                        "product_id": proposal.draft.product_id,
                        "object_id": proposal.draft.object_id,
                        "stage_id": proposal.draft.stage_id,
                        "readiness": proposal.draft.readiness_percent,
                        "quality": proposal.draft.match_quality.value,
                        "requires_review": proposal.draft.requires_review,
                        "issues": [item.code for item in proposal.draft.issues],
                    }
                    for proposal in result.proposals
                ],
            }
            for result in results
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
