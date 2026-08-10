"""Read-only deployment snapshot for the P10 matching stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from p9_deployment_check import inspect_database


CORE_TABLES = (
    "WorkLogEntries",
    "ProductionEvents",
    "Attachments",
    "ProductionInboxMessages",
    "ProductionInboxAttachments",
    "ProductionInboxSources",
    "ProductionInboxBundles",
    "ProductionInboxBundleMessages",
    "ProductionStageAliases",
    "ProductionInboxMatchRuns",
    "ProductionInboxProposals",
    "ProductionInboxProductCandidates",
    "ProductionInboxObjectCandidates",
    "ProductionInboxStageCandidates",
    "ProductionInboxProposalEvidence",
    "ProductionInboxProposalIssues",
)

WORKBOT_TABLES = (
    "messages",
    "message_revisions",
    "message_attachments",
    "WorkBotImportRows",
)


def build_snapshot(prolog_data: Path, workbot_database: Path) -> dict[str, object]:
    components = {}
    for filename in (
        "prolog.sqlite3",
        "employees.sqlite3",
        "objects.sqlite3",
        "products.sqlite3",
        "aliases.sqlite3",
    ):
        components[filename] = inspect_database(
            prolog_data / filename,
            CORE_TABLES if filename == "prolog.sqlite3" else (),
        )
    return {
        "prolog_components": components,
        "workbot": inspect_database(workbot_database, WORKBOT_TABLES),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prolog-data", type=Path, required=True)
    parser.add_argument("--workbot-db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(
        build_snapshot(args.prolog_data, args.workbot_db),
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
