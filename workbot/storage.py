"""Локальное хранилище WorkBot с защитой от повторной обработки сообщений."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from workbot.models import ParsedEmployeeReport, ParsedReport, StoredReport


class WorkBotStorage:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(_SCHEMA)
            self._migrate_users(connection)

    @staticmethod
    def _migrate_users(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(users)")
        }
        if "verified_phone" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN verified_phone TEXT NOT NULL DEFAULT ''"
            )
        if "phone_verified_at" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN phone_verified_at TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_verified_phone
            ON users(verified_phone)
            WHERE verified_phone <> ''
            """
        )

    def get_state(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO bot_state(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def upsert_chat(self, chat_id: int, title: str = "", status: str = "active") -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO chats(chat_id, title, status, discovered_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE chats.title END,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (chat_id, title.strip(), status, now, now),
            )

    def chats(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) AS messages_count,
                       (
                           SELECT COUNT(*) FROM reports r
                           JOIN messages m ON m.max_message_id = r.source_message_id
                           WHERE m.chat_id = c.chat_id
                       ) + (
                           SELECT COUNT(*) FROM historical_reports h
                           JOIN messages m ON m.max_message_id = h.source_message_id
                           WHERE m.chat_id = c.chat_id
                       ) AS reports_count
                FROM chats c
                ORDER BY c.title COLLATE NOCASE, c.chat_id
                """
            ).fetchall()

    def upsert_user(
        self,
        user_id: int,
        first_name: str,
        last_name: str,
        username: str,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO users(max_user_id, first_name, last_name, username, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(max_user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (user_id, first_name, last_name, username, now),
            )

    def bind_user(self, user_id: int, employee_name: str) -> None:
        cleaned = employee_name.strip()
        if not cleaned:
            raise ValueError("ФИО сотрудника не может быть пустым")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET employee_name = ?, updated_at = ? WHERE max_user_id = ?",
                (cleaned, datetime.now().isoformat(timespec="seconds"), user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Пользователь MAX {user_id} ещё не встречался в сообщениях")
            connection.execute(
                "UPDATE reports SET employee_name = ? WHERE sender_id = ?",
                (cleaned, user_id),
            )

    def save_verified_phone(self, user_id: int, phone: str) -> None:
        cleaned = phone.strip()
        if not cleaned:
            raise ValueError("Подтвержденный номер телефона не может быть пустым")
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            conflict = connection.execute(
                """
                SELECT max_user_id
                FROM users
                WHERE verified_phone = ? AND max_user_id <> ?
                """,
                (cleaned, user_id),
            ).fetchone()
            if conflict:
                raise ValueError(
                    "Этот номер уже подтвержден другим пользователем MAX. "
                    "Обратитесь к руководителю для проверки."
                )
            cursor = connection.execute(
                """
                UPDATE users
                SET verified_phone = ?, phone_verified_at = ?, updated_at = ?
                WHERE max_user_id = ?
                """,
                (cleaned, now, now, user_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Пользователь MAX еще не зарегистрирован в WorkBot")

    def employee_name_for(self, user_id: int, fallback: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT employee_name FROM users WHERE max_user_id = ?", (user_id,)
            ).fetchone()
        return str(row["employee_name"]).strip() if row and row["employee_name"] else fallback.strip()

    def record_message(
        self,
        message_id: str,
        chat_id: int | None,
        sender_id: int,
        received_at: datetime,
        raw_text: str,
        replace_existing: bool = False,
    ) -> bool:
        with self.connect() as connection:
            if replace_existing:
                connection.execute(
                    """
                    INSERT INTO messages(
                        max_message_id, chat_id, sender_id, received_at, raw_text, parse_status
                    ) VALUES (?, ?, ?, ?, ?, 'pending')
                    ON CONFLICT(max_message_id) DO UPDATE SET
                        chat_id = excluded.chat_id,
                        sender_id = excluded.sender_id,
                        received_at = excluded.received_at,
                        raw_text = excluded.raw_text,
                        parse_status = 'pending',
                        parse_error = ''
                    """,
                    (
                        message_id,
                        chat_id,
                        sender_id,
                        received_at.isoformat(timespec="seconds"),
                        raw_text,
                    ),
                )
                return True
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO messages(
                    max_message_id, chat_id, sender_id, received_at, raw_text, parse_status
                ) VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (message_id, chat_id, sender_id, received_at.isoformat(timespec="seconds"), raw_text),
            )
            return cursor.rowcount > 0

    def mark_message(self, message_id: str, status: str, error: str = "") -> None:
        with self.connect() as connection:
            if status != "parsed":
                connection.execute(
                    "DELETE FROM reports WHERE source_message_id = ?",
                    (message_id,),
                )
            if status != "parsed_legacy":
                connection.execute(
                    "DELETE FROM historical_reports WHERE source_message_id = ?",
                    (message_id,),
                )
            connection.execute(
                "UPDATE messages SET parse_status = ?, parse_error = ? WHERE max_message_id = ?",
                (status, error, message_id),
            )

    def save_report(
        self,
        message_id: str,
        sender_id: int,
        employee_name: str,
        report: ParsedReport,
    ) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM historical_reports WHERE source_message_id = ?",
                (message_id,),
            )
            cursor = connection.execute(
                """
                INSERT INTO reports(
                    source_message_id, sender_id, employee_name, work_date, work_types,
                    hours, object_name, location, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_message_id) DO UPDATE SET
                    sender_id = excluded.sender_id,
                    employee_name = excluded.employee_name,
                    work_date = excluded.work_date,
                    work_types = excluded.work_types,
                    hours = excluded.hours,
                    object_name = excluded.object_name,
                    location = excluded.location,
                    created_at = excluded.created_at
                """,
                (
                    message_id,
                    sender_id,
                    employee_name.strip(),
                    report.work_date.isoformat(),
                    report.work_types,
                    report.hours,
                    report.object_name,
                    report.location,
                    now,
                ),
            )
            connection.execute(
                "UPDATE messages SET parse_status = 'parsed', parse_error = '' WHERE max_message_id = ?",
                (message_id,),
            )
            return cursor.rowcount > 0

    def save_historical_reports(
        self,
        message_id: str,
        reports: list[ParsedEmployeeReport],
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute("DELETE FROM reports WHERE source_message_id = ?", (message_id,))
            connection.execute(
                "DELETE FROM historical_reports WHERE source_message_id = ?",
                (message_id,),
            )
            for source_index, item in enumerate(reports):
                report = item.report
                connection.execute(
                    """
                    INSERT INTO historical_reports(
                        source_message_id, source_index, employee_name, work_date,
                        work_types, hours, object_name, location, confidence,
                        source_fragment, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        source_index,
                        item.employee_name,
                        report.work_date.isoformat(),
                        report.work_types,
                        report.hours,
                        report.object_name,
                        report.location,
                        item.confidence,
                        item.source_fragment,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE messages SET parse_status = 'parsed_legacy', parse_error = '' "
                "WHERE max_message_id = ?",
                (message_id,),
            )
        return len(reports)

    def reports(self, date_from: date | None = None, date_to: date | None = None) -> list[StoredReport]:
        sql = """
            SELECT * FROM (
                SELECT id, source_message_id, sender_id, employee_name, work_date,
                       work_types, hours, object_name, location, created_at
                FROM reports
                UNION ALL
                SELECT -h.id AS id, h.source_message_id, m.sender_id, h.employee_name,
                       h.work_date, h.work_types, h.hours, h.object_name, h.location,
                       h.created_at
                FROM historical_reports h
                JOIN messages m ON m.max_message_id = h.source_message_id
            )
        """
        conditions: list[str] = []
        params: list[str] = []
        if date_from:
            conditions.append("work_date >= ?")
            params.append(date_from.isoformat())
        if date_to:
            conditions.append("work_date <= ?")
            params.append(date_to.isoformat())
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY employee_name COLLATE NOCASE, work_date, id"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            StoredReport(
                id=row["id"],
                source_message_id=row["source_message_id"],
                sender_id=row["sender_id"],
                employee_name=row["employee_name"],
                work_date=date.fromisoformat(row["work_date"]),
                work_types=row["work_types"],
                hours=float(row["hours"]),
                object_name=row["object_name"],
                location=row["location"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def users(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT u.*, COUNT(r.id) AS reports_count
                FROM users u
                LEFT JOIN reports r ON r.sender_id = u.max_user_id
                GROUP BY u.max_user_id
                ORDER BY COALESCE(NULLIF(u.employee_name, ''), u.last_name, u.first_name)
                """
            ).fetchall()

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                "users": int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
                "reports": int(
                    connection.execute(
                        "SELECT (SELECT COUNT(*) FROM reports) + "
                        "(SELECT COUNT(*) FROM historical_reports)"
                    ).fetchone()[0]
                ),
                "invalid": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM messages WHERE parse_status = 'invalid'"
                    ).fetchone()[0]
                ),
            }

    def chat_messages(self, chat_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT m.*, u.first_name, u.last_name, u.username, u.employee_name
                FROM messages m
                JOIN users u ON u.max_user_id = m.sender_id
                WHERE m.chat_id = ?
                ORDER BY m.received_at, m.max_message_id
                """,
                (chat_id,),
            ).fetchall()

    def recent_errors(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT m.received_at, m.sender_id, m.parse_error,
                       COALESCE(NULLIF(u.employee_name, ''), u.last_name || ' ' || u.first_name) AS employee
                FROM messages m
                LEFT JOIN users u ON u.max_user_id = m.sender_id
                WHERE m.parse_status = 'invalid'
                ORDER BY m.received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    max_user_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    employee_name TEXT NOT NULL DEFAULT '',
    verified_phone TEXT NOT NULL DEFAULT '',
    phone_verified_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    max_message_id TEXT PRIMARY KEY,
    chat_id INTEGER,
    sender_id INTEGER NOT NULL REFERENCES users(max_user_id),
    received_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    parse_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL UNIQUE REFERENCES messages(max_message_id),
    sender_id INTEGER NOT NULL REFERENCES users(max_user_id),
    employee_name TEXT NOT NULL,
    work_date TEXT NOT NULL,
    work_types TEXT NOT NULL,
    hours REAL NOT NULL,
    object_name TEXT NOT NULL,
    location TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historical_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL REFERENCES messages(max_message_id),
    source_index INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    work_date TEXT NOT NULL,
    work_types TEXT NOT NULL,
    hours REAL NOT NULL DEFAULT 0,
    object_name TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    source_fragment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(source_message_id, source_index)
);

CREATE INDEX IF NOT EXISTS idx_reports_employee_date ON reports(employee_name, work_date);
CREATE INDEX IF NOT EXISTS idx_historical_reports_employee_date
    ON historical_reports(employee_name, work_date);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(parse_status);
"""
