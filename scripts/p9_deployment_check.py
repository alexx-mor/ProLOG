"""Read-only deployment snapshot for the P9 production-inbox grouping stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


TRACKED_TABLES = (
    "WorkLogEntries",
    "ProductionEvents",
    "Attachments",
    "ProductionInboxMessages",
    "ProductionInboxAttachments",
    "ProductionInboxSources",
    "ProductionInboxBundles",
    "ProductionInboxBundleMessages",
)

WORKBOT_TABLES = (
    "messages",
    "message_revisions",
    "message_attachments",
    "WorkBotImportRows",
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_signature(connection: sqlite3.Connection, table: str) -> dict[str, object]:
    columns = [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    ]
    rows = connection.execute(
        f'SELECT * FROM "{table}" ORDER BY rowid'
    ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(
                dict(zip(columns, row, strict=True)),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return {"count": len(rows), "rows_sha256": digest.hexdigest()}


def inspect_database(path: Path, tables_to_track: tuple[str, ...]) -> dict[str, object]:
    connection = sqlite3.connect(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        versions = []
        if "SchemaMigrations" in tables:
            versions = [
                {"component": str(row[0]), "version": int(row[1])}
                for row in connection.execute(
                    "SELECT component, version FROM SchemaMigrations "
                    "ORDER BY component, version"
                )
            ]
        return {
            "path": str(path.resolve()),
            "file_sha256": _file_sha256(path),
            "integrity_check": str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            ),
            "schema_versions": versions,
            "tables": {
                table: _table_signature(connection, table)
                for table in tables_to_track
                if table in tables
            },
        }
    finally:
        connection.close()


def build_snapshot(prolog_data: Path, workbot_database: Path) -> dict[str, object]:
    component_names = (
        "prolog.sqlite3",
        "employees.sqlite3",
        "objects.sqlite3",
        "products.sqlite3",
        "aliases.sqlite3",
    )
    result: dict[str, object] = {
        "prolog_components": {},
        "workbot": inspect_database(workbot_database, WORKBOT_TABLES),
    }
    components = result["prolog_components"]
    assert isinstance(components, dict)
    for filename in component_names:
        path = prolog_data / filename
        components[filename] = inspect_database(
            path,
            TRACKED_TABLES if filename == "prolog.sqlite3" else (),
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prolog-data", type=Path, required=True)
    parser.add_argument("--workbot-db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    snapshot = build_snapshot(args.prolog_data, args.workbot_db)
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
