"""Read-only access to the local WorkBot SQLite database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from hours import normalize_hours
from integrations.workbot.models import WorkBotCandidate, WorkBotSourceUser


class WorkBotSource:
    REQUIRED_TABLES = {"users", "messages", "reports", "historical_reports"}

    def read_candidates(self, path: Path) -> list[WorkBotCandidate]:
        if not path.is_file():
            raise ValueError("База WorkBot не найдена")
        connection = self._connect(path)
        try:
            self._validate_schema(connection)
            rows = connection.execute(SOURCE_QUERY).fetchall()
        except sqlite3.Error as exc:
            raise ValueError(f"Не удалось прочитать базу WorkBot: {exc}") from exc
        finally:
            connection.close()
        candidates = [self._map(row) for row in rows]
        self._assign_message_hashes(candidates)
        return candidates

    def read_users(self, path: Path) -> list[WorkBotSourceUser]:
        if not path.is_file():
            raise ValueError("База WorkBot не найдена")
        connection = self._connect(path)
        try:
            self._validate_schema(connection)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(users)")
            }
            verified_phone = "verified_phone" if "verified_phone" in columns else "''"
            verified_at = "phone_verified_at" if "phone_verified_at" in columns else "''"
            rows = connection.execute(
                f"""
                SELECT max_user_id, first_name, last_name, username, employee_name,
                       {verified_phone} AS verified_phone,
                       {verified_at} AS phone_verified_at
                FROM users
                ORDER BY COALESCE(NULLIF(employee_name, ''), last_name, first_name), max_user_id
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise ValueError(f"Не удалось прочитать пользователей WorkBot: {exc}") from exc
        finally:
            connection.close()
        return [
            WorkBotSourceUser(
                max_user_id=int(row["max_user_id"]),
                first_name=str(row["first_name"] or "").strip(),
                last_name=str(row["last_name"] or "").strip(),
                username=str(row["username"] or "").strip(),
                employee_text=str(row["employee_name"] or "").strip(),
                verified_phone=str(row["verified_phone"] or "").strip(),
                phone_verified_at=str(row["phone_verified_at"] or "").strip(),
            )
            for row in rows
        ]

    def _connect(self, path: Path) -> sqlite3.Connection:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        return connection

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = self.REQUIRED_TABLES - tables
        if missing:
            raise ValueError("Файл не является базой WorkBot. Нет таблиц: " + ", ".join(sorted(missing)))

    def _map(self, row: sqlite3.Row) -> WorkBotCandidate:
        try:
            work_date = date.fromisoformat(str(row["work_date"]))
        except ValueError as exc:
            raise ValueError(
                f"WorkBot содержит некорректную дату в сообщении {row['max_message_id']}"
            ) from exc
        return WorkBotCandidate(
            max_message_id=str(row["max_message_id"]),
            source_index=int(row["source_index"]),
            source_kind=str(row["source_kind"]),
            sender_id=int(row["sender_id"]),
            chat_id=row["chat_id"],
            received_at=str(row["received_at"] or ""),
            raw_text=str(row["raw_text"] or ""),
            employee_text=str(row["employee_name"] or "").strip(),
            work_date=work_date,
            work_types=str(row["work_types"] or "").strip(),
            hours=normalize_hours(row["hours"]),
            object_text=str(row["object_name"] or "").strip(),
            location_text=str(row["location"] or "").strip(),
            confidence=float(row["confidence"] or 0),
            source_fragment=str(row["source_fragment"] or row["raw_text"] or "").strip(),
            error_message=str(row["source_error"] or "").strip(),
        )

    def _assign_message_hashes(self, candidates: list[WorkBotCandidate]) -> None:
        grouped: dict[str, list[WorkBotCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate.max_message_id].append(candidate)
        for rows in grouped.values():
            payload = {
                "raw_text": rows[0].raw_text,
                "rows": [
                    {
                        "index": row.source_index,
                        "kind": row.source_kind,
                        "employee": row.employee_text,
                        "date": row.work_date.isoformat(),
                        "work_types": row.work_types,
                        "hours": row.hours,
                        "object": row.object_text,
                        "location": row.location_text,
                    }
                    for row in sorted(rows, key=lambda item: (item.source_kind, item.source_index))
                ],
            }
            content_hash = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            for row in rows:
                row.content_hash = content_hash


SOURCE_QUERY = """
SELECT
    m.max_message_id,
    0 AS source_index,
    'strict' AS source_kind,
    m.sender_id,
    m.chat_id,
    m.received_at,
    m.raw_text,
    r.employee_name,
    r.work_date,
    r.work_types,
    r.hours,
    r.object_name,
    r.location,
    1.0 AS confidence,
    m.raw_text AS source_fragment,
    '' AS source_error
FROM reports r
JOIN messages m ON m.max_message_id = r.source_message_id
UNION ALL
SELECT
    m.max_message_id,
    h.source_index,
    'historical' AS source_kind,
    m.sender_id,
    m.chat_id,
    m.received_at,
    m.raw_text,
    h.employee_name,
    h.work_date,
    h.work_types,
    h.hours,
    h.object_name,
    h.location,
    h.confidence,
    h.source_fragment,
    '' AS source_error
FROM historical_reports h
JOIN messages m ON m.max_message_id = h.source_message_id
UNION ALL
SELECT
    m.max_message_id,
    0 AS source_index,
    'unparsed' AS source_kind,
    m.sender_id,
    m.chat_id,
    m.received_at,
    m.raw_text,
    COALESCE(NULLIF(u.employee_name, ''), TRIM(u.last_name || ' ' || u.first_name)) AS employee_name,
    SUBSTR(m.received_at, 1, 10) AS work_date,
    '' AS work_types,
    0.0 AS hours,
    '' AS object_name,
    '' AS location,
    0.0 AS confidence,
    m.raw_text AS source_fragment,
    m.parse_error AS source_error
FROM messages m
LEFT JOIN users u ON u.max_user_id = m.sender_id
WHERE m.parse_status = 'invalid'
  AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.source_message_id = m.max_message_id)
  AND NOT EXISTS (SELECT 1 FROM historical_reports h WHERE h.source_message_id = m.max_message_id)
ORDER BY received_at, max_message_id, source_kind, source_index
"""
