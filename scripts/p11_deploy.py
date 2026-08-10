"""Apply only the P11 schema migration and run post-deployment diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import Database
from production.module import build_production_module


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
    module = build_production_module(database, args.attachment_root)
    references = database.check_references()
    attachments = module.attachments.diagnostics()
    reviews = module.review.diagnostics()
    with database.connect() as connection:
        payload = {
            "schema_versions": {
                item.component: item.current_version
                for item in database.schema_versions()
            },
            "integrity_check": str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            ),
            "foreign_key_issue_count": len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            ),
            "cross_database_healthy": references.is_valid,
            "attachment_diagnostics_healthy": attachments.is_healthy,
            "review_diagnostics_healthy": reviews.is_healthy,
            "counts": {
                table: int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                )
                for table in (
                    "ProductionEvents",
                    "Attachments",
                    "WorkLogEntries",
                    "ProductionInboxMessages",
                    "ProductionInboxBundles",
                    "ProductionInboxMatchRuns",
                    "ProductionInboxProposals",
                    "ProductionInboxReviews",
                    "ProductionInboxReviewActions",
                    "ProductionInboxReviewAttachmentPromotions",
                    "ProductionInboxManualBundleSources",
                )
            },
        }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
