"""SQLite migration helpers for the production-stage directory."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


PRODUCTION_STAGE_SEED: tuple[tuple[str, str], ...] = (
    ("PREPARATION", "Подготовка"),
    ("METALWORK", "Слесарные работы"),
    ("EQUIPMENT_INSTALLATION", "Установка оборудования"),
    ("ELECTRICAL_INSTALLATION", "Электромонтаж"),
    ("MARKING", "Маркировка"),
    ("PROGRAMMING", "Программирование"),
    ("TESTING", "Проверка"),
    ("QUALITY_CONTROL", "ОТК"),
    ("PACKAGING", "Упаковка"),
    ("COMPLETED", "Готово"),
)


PRODUCTION_STAGES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionStages (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
    is_active INTEGER NOT NULL CHECK(is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
)
"""


ATTACHMENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS Attachments (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    width INTEGER NULL CHECK(width IS NULL OR width > 0),
    height INTEGER NULL CHECK(height IS NULL OR height > 0),
    captured_at_utc TEXT NULL,
    received_at_utc TEXT NOT NULL,
    source_type TEXT NULL,
    source_message_id TEXT NULL,
    source_attachment_id TEXT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attachments_sha256
ON Attachments(sha256);

CREATE INDEX IF NOT EXISTS idx_attachments_storage_key
ON Attachments(storage_key);

CREATE INDEX IF NOT EXISTS idx_attachments_source_message
ON Attachments(source_type, source_message_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_attachments_source_identity
ON Attachments(source_type, source_message_id, source_attachment_id)
WHERE source_type IS NOT NULL AND TRIM(source_type) <> ''
  AND source_message_id IS NOT NULL AND TRIM(source_message_id) <> ''
  AND source_attachment_id IS NOT NULL AND TRIM(source_attachment_id) <> '';
"""


PRODUCTION_EVENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionEvents (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    product_id INTEGER NULL,
    object_id_snapshot INTEGER NULL,
    stage_id INTEGER NULL REFERENCES ProductionStages(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK(event_type IN ('observation', 'baseline', 'correction', 'rework')),
    readiness_percent INTEGER NULL CHECK(readiness_percent IS NULL OR readiness_percent BETWEEN 0 AND 100),
    description TEXT NOT NULL DEFAULT '',
    change_reason TEXT NOT NULL DEFAULT '',
    observed_at_utc TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('manual', 'integration', 'import', 'system')),
    source_ref TEXT NULL,
    reported_by_employee_id INTEGER NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'ready', 'confirmed', 'rejected', 'superseded')),
    supersedes_event_id INTEGER NULL REFERENCES ProductionEvents(id) ON DELETE RESTRICT,
    idempotency_key TEXT NULL,
    created_at_utc TEXT NOT NULL,
    created_actor_type TEXT NOT NULL CHECK(created_actor_type IN ('local_user', 'server_user', 'system_process', 'integration')),
    created_actor_uid TEXT NOT NULL,
    created_actor_local_user_id INTEGER NULL,
    created_actor_display_name_snapshot TEXT NOT NULL,
    confirmed_at_utc TEXT NULL,
    confirmed_actor_type TEXT NULL CHECK(confirmed_actor_type IS NULL OR confirmed_actor_type IN ('local_user', 'server_user', 'system_process', 'integration')),
    confirmed_actor_uid TEXT NULL,
    confirmed_actor_local_user_id INTEGER NULL,
    confirmed_actor_display_name_snapshot TEXT NULL,
    rejected_at_utc TEXT NULL,
    rejected_actor_type TEXT NULL CHECK(rejected_actor_type IS NULL OR rejected_actor_type IN ('local_user', 'server_user', 'system_process', 'integration')),
    rejected_actor_uid TEXT NULL,
    rejected_actor_local_user_id INTEGER NULL,
    rejected_actor_display_name_snapshot TEXT NULL,
    rejection_reason TEXT NOT NULL DEFAULT '',
    CHECK(supersedes_event_id IS NULL OR supersedes_event_id <> id),
    CHECK(
        (event_type = 'correction' AND supersedes_event_id IS NOT NULL)
        OR (event_type <> 'correction' AND supersedes_event_id IS NULL)
    ),
    CHECK(
        (
            status IN ('confirmed', 'superseded')
            AND product_id IS NOT NULL
            AND confirmed_at_utc IS NOT NULL
            AND confirmed_actor_type IS NOT NULL
            AND confirmed_actor_uid IS NOT NULL
            AND confirmed_actor_display_name_snapshot IS NOT NULL
        )
        OR (
            status NOT IN ('confirmed', 'superseded')
            AND confirmed_at_utc IS NULL
            AND confirmed_actor_type IS NULL
            AND confirmed_actor_uid IS NULL
            AND confirmed_actor_local_user_id IS NULL
            AND confirmed_actor_display_name_snapshot IS NULL
        )
    ),
    CHECK(
        (
            status = 'rejected'
            AND rejected_at_utc IS NOT NULL
            AND rejected_actor_type IS NOT NULL
            AND rejected_actor_uid IS NOT NULL
            AND rejected_actor_display_name_snapshot IS NOT NULL
        )
        OR (
            status <> 'rejected'
            AND rejected_at_utc IS NULL
            AND rejected_actor_type IS NULL
            AND rejected_actor_uid IS NULL
            AND rejected_actor_local_user_id IS NULL
            AND rejected_actor_display_name_snapshot IS NULL
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_production_events_idempotency_key
ON ProductionEvents(idempotency_key)
WHERE idempotency_key IS NOT NULL AND TRIM(idempotency_key) <> '';

CREATE INDEX IF NOT EXISTS idx_production_events_product_observed
ON ProductionEvents(product_id, observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_production_events_stage ON ProductionEvents(stage_id);
CREATE INDEX IF NOT EXISTS idx_production_events_status ON ProductionEvents(status);
CREATE INDEX IF NOT EXISTS idx_production_events_source ON ProductionEvents(source_type, source_ref);
CREATE INDEX IF NOT EXISTS idx_production_events_supersedes ON ProductionEvents(supersedes_event_id);

CREATE TABLE IF NOT EXISTS ProductionEventAttachments (
    production_event_id INTEGER NOT NULL REFERENCES ProductionEvents(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES Attachments(id) ON DELETE RESTRICT,
    sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
    PRIMARY KEY(production_event_id, attachment_id)
);
CREATE INDEX IF NOT EXISTS idx_production_event_attachments_attachment
ON ProductionEventAttachments(attachment_id);

CREATE TABLE IF NOT EXISTS ProductionEventWorkLogs (
    production_event_id INTEGER NOT NULL REFERENCES ProductionEvents(id) ON DELETE CASCADE,
    worklog_entry_id INTEGER NOT NULL REFERENCES WorkLogEntries(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('explicit', 'manual')),
    created_at_utc TEXT NOT NULL,
    created_actor_type TEXT NOT NULL CHECK(created_actor_type IN ('local_user', 'server_user', 'system_process', 'integration')),
    created_actor_uid TEXT NOT NULL,
    created_actor_local_user_id INTEGER NULL,
    created_actor_display_name_snapshot TEXT NOT NULL,
    PRIMARY KEY(production_event_id, worklog_entry_id)
);
CREATE INDEX IF NOT EXISTS idx_production_event_worklogs_worklog
ON ProductionEventWorkLogs(worklog_entry_id);

CREATE TRIGGER IF NOT EXISTS trg_production_events_lifecycle
BEFORE UPDATE OF status ON ProductionEvents
WHEN NOT (
    NEW.status = OLD.status
    OR (OLD.status = 'draft' AND NEW.status IN ('ready', 'rejected'))
    OR (OLD.status = 'ready' AND NEW.status IN ('confirmed', 'rejected'))
    OR (OLD.status = 'confirmed' AND NEW.status = 'superseded')
)
BEGIN
    SELECT RAISE(ABORT, 'invalid ProductionEvent lifecycle transition');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_events_supersede_requires_correction
BEFORE UPDATE OF status ON ProductionEvents
WHEN OLD.status = 'confirmed' AND NEW.status = 'superseded'
    AND NOT EXISTS (
        SELECT 1 FROM ProductionEvents correction
        WHERE correction.supersedes_event_id = OLD.id
          AND correction.event_type = 'correction'
          AND correction.status = 'confirmed'
    )
BEGIN
    SELECT RAISE(ABORT, 'superseded event requires confirmed correction');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_events_immutable_fact
BEFORE UPDATE OF
    uid, product_id, object_id_snapshot, stage_id, event_type,
    readiness_percent, description, change_reason, observed_at_utc,
    recorded_at_utc, source_type, source_ref, reported_by_employee_id,
    supersedes_event_id, idempotency_key, created_at_utc,
    created_actor_type, created_actor_uid, created_actor_local_user_id,
    created_actor_display_name_snapshot
ON ProductionEvents
WHEN OLD.status IN ('confirmed', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'confirmed ProductionEvent is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_production_events_no_delete_confirmed
BEFORE DELETE ON ProductionEvents
WHEN OLD.status IN ('confirmed', 'superseded')
BEGIN
    SELECT RAISE(ABORT, 'confirmed ProductionEvent cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_event_attachments_insert_mutable
BEFORE INSERT ON ProductionEventAttachments
WHEN (SELECT status FROM ProductionEvents WHERE id = NEW.production_event_id)
    NOT IN ('draft', 'ready')
BEGIN
    SELECT RAISE(ABORT, 'attachments of immutable ProductionEvent cannot be changed');
END;

CREATE TRIGGER IF NOT EXISTS trg_event_attachments_update_mutable
BEFORE UPDATE ON ProductionEventAttachments
WHEN (SELECT status FROM ProductionEvents WHERE id = OLD.production_event_id)
    NOT IN ('draft', 'ready')
BEGIN
    SELECT RAISE(ABORT, 'attachments of immutable ProductionEvent cannot be changed');
END;

CREATE TRIGGER IF NOT EXISTS trg_event_attachments_delete_mutable
BEFORE DELETE ON ProductionEventAttachments
WHEN (SELECT status FROM ProductionEvents WHERE id = OLD.production_event_id)
    NOT IN ('draft', 'ready')
BEGIN
    SELECT RAISE(ABORT, 'attachments of immutable ProductionEvent cannot be changed');
END;

CREATE TRIGGER IF NOT EXISTS trg_event_worklogs_insert_mutable
BEFORE INSERT ON ProductionEventWorkLogs
WHEN (SELECT status FROM ProductionEvents WHERE id = NEW.production_event_id)
    NOT IN ('draft', 'ready')
BEGIN
    SELECT RAISE(ABORT, 'work logs of immutable ProductionEvent cannot be changed');
END;

CREATE TRIGGER IF NOT EXISTS trg_event_worklogs_update_mutable
BEFORE UPDATE ON ProductionEventWorkLogs
WHEN (SELECT status FROM ProductionEvents WHERE id = OLD.production_event_id)
    NOT IN ('draft', 'ready')
BEGIN
    SELECT RAISE(ABORT, 'work logs of immutable ProductionEvent cannot be changed');
END;
"""


def apply_production_stages_migration(connection: sqlite3.Connection) -> None:
    """Create and seed the standalone production-stage directory."""

    connection.execute(PRODUCTION_STAGES_SCHEMA_SQL)
    seed_production_stages(connection)


def seed_production_stages(connection: sqlite3.Connection) -> None:
    """Add missing defaults without overwriting user-managed fields."""

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sort_order, (code, name) in enumerate(PRODUCTION_STAGE_SEED, start=1):
        connection.execute(
            """
            INSERT INTO ProductionStages (
                uid, code, name, sort_order, is_active, created_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(code) DO NOTHING
            """,
            (str(uuid4()), code, name, sort_order, now, now),
        )


def apply_attachments_migration(connection: sqlite3.Connection) -> None:
    """Create attachment metadata storage without touching physical files."""

    from schema_migrations import execute_sql_script

    execute_sql_script(connection, ATTACHMENTS_SCHEMA_SQL)


def apply_production_events_migration(connection: sqlite3.Connection) -> None:
    """Create persistent production events and explicit relation tables."""

    from schema_migrations import execute_sql_script

    execute_sql_script(connection, PRODUCTION_EVENTS_SCHEMA_SQL)
