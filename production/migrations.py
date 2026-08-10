"""SQLite migration helpers for the production-stage directory."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from matching_text import normalize_alias_text


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


PRODUCTION_SOURCE_TRANSPORT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionInboxSources (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL CHECK(TRIM(source_type) <> ''),
    source_ref TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL CHECK(TRIM(display_name) <> ''),
    chat_id INTEGER NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    web_url TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_production_inbox_sources_ref
ON ProductionInboxSources(source_type, source_ref)
WHERE TRIM(source_ref) <> '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_production_inbox_sources_max_chat
ON ProductionInboxSources(chat_id)
WHERE source_type = 'max_chat' AND chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_production_inbox_sources_enabled
ON ProductionInboxSources(enabled, source_type);

CREATE TABLE IF NOT EXISTS ProductionInboxSyncState (
    source_id INTEGER PRIMARY KEY
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    cursor_revision_id INTEGER NOT NULL DEFAULT 0 CHECK(cursor_revision_id >= 0),
    cursor_message_id TEXT NOT NULL DEFAULT '',
    cursor_revision_number INTEGER NOT NULL DEFAULT 0 CHECK(cursor_revision_number >= 0),
    cursor_content_hash TEXT NOT NULL DEFAULT '',
    last_sync_at_utc TEXT NULL,
    last_success_at_utc TEXT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ProductionInboxSyncRuns (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NULL,
    cursor_before INTEGER NOT NULL CHECK(cursor_before >= 0),
    cursor_after INTEGER NOT NULL CHECK(cursor_after >= 0),
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'partial', 'failed')),
    read_count INTEGER NOT NULL DEFAULT 0 CHECK(read_count >= 0),
    imported_count INTEGER NOT NULL DEFAULT 0 CHECK(imported_count >= 0),
    unchanged_count INTEGER NOT NULL DEFAULT 0 CHECK(unchanged_count >= 0),
    changed_count INTEGER NOT NULL DEFAULT 0 CHECK(changed_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK(skipped_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
    error_summary TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_sync_runs_source
ON ProductionInboxSyncRuns(source_id, started_at_utc);

CREATE TABLE IF NOT EXISTS ProductionInboxMessages (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    sync_run_id INTEGER NULL
        REFERENCES ProductionInboxSyncRuns(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    source_message_id TEXT NOT NULL,
    source_revision_id INTEGER NOT NULL CHECK(source_revision_id > 0),
    source_revision_number INTEGER NOT NULL CHECK(source_revision_number > 0),
    chat_id INTEGER NULL,
    sender_max_user_id INTEGER NULL,
    sender_display_snapshot TEXT NOT NULL DEFAULT '',
    message_timestamp_utc TEXT NOT NULL,
    edited_at_utc TEXT NULL,
    source_received_at_utc TEXT NOT NULL,
    transported_at_utc TEXT NOT NULL,
    source_sequence INTEGER NULL,
    source_text TEXT NULL,
    content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
    source_content_json TEXT NOT NULL,
    raw_envelope_json TEXT NOT NULL,
    change_kind TEXT NOT NULL CHECK(change_kind IN ('original', 'changed')),
    supersedes_inbox_message_id INTEGER NULL
        REFERENCES ProductionInboxMessages(id) ON DELETE RESTRICT,
    UNIQUE(source_id, source_revision_id),
    UNIQUE(source_id, source_message_id, source_revision_number),
    CHECK(supersedes_inbox_message_id IS NULL OR supersedes_inbox_message_id <> id)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_messages_source_order
ON ProductionInboxMessages(source_id, source_revision_id);
CREATE INDEX IF NOT EXISTS idx_production_inbox_messages_chat_time
ON ProductionInboxMessages(chat_id, message_timestamp_utc, source_sequence);
CREATE INDEX IF NOT EXISTS idx_production_inbox_messages_sender
ON ProductionInboxMessages(sender_max_user_id, message_timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_production_inbox_messages_source_message
ON ProductionInboxMessages(source_id, source_message_id, source_revision_number);
CREATE INDEX IF NOT EXISTS idx_production_inbox_messages_change
ON ProductionInboxMessages(source_id, change_kind);

CREATE TABLE IF NOT EXISTS ProductionInboxAttachments (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    inbox_message_id INTEGER NOT NULL
        REFERENCES ProductionInboxMessages(id) ON DELETE RESTRICT,
    source_attachment_row_id INTEGER NOT NULL CHECK(source_attachment_row_id > 0),
    source_attachment_id TEXT NOT NULL,
    identity_kind TEXT NOT NULL DEFAULT '',
    source_order INTEGER NOT NULL CHECK(source_order >= 0),
    attachment_type TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL DEFAULT '',
    source_size INTEGER NULL CHECK(source_size IS NULL OR source_size >= 0),
    source_download_status TEXT NOT NULL,
    source_sha256 TEXT NOT NULL DEFAULT '',
    source_storage_key TEXT NOT NULL DEFAULT '',
    source_downloaded_at_utc TEXT NULL,
    media_state TEXT NOT NULL
        CHECK(media_state IN ('available', 'pending', 'failed', 'unavailable', 'missing', 'corrupt', 'unsafe')),
    source_metadata_json TEXT NOT NULL,
    UNIQUE(inbox_message_id, source_attachment_id),
    UNIQUE(inbox_message_id, source_order)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_attachments_message_order
ON ProductionInboxAttachments(inbox_message_id, source_order);
CREATE INDEX IF NOT EXISTS idx_production_inbox_attachments_sha256
ON ProductionInboxAttachments(source_sha256)
WHERE source_sha256 <> '';

CREATE TABLE IF NOT EXISTS ProductionInboxSyncIssues (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    source_revision_id INTEGER NOT NULL CHECK(source_revision_id >= 0),
    source_message_id TEXT NOT NULL DEFAULT '',
    source_revision_number INTEGER NOT NULL DEFAULT 0 CHECK(source_revision_number >= 0),
    source_attachment_id TEXT NOT NULL DEFAULT '',
    issue_code TEXT NOT NULL,
    message TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1 CHECK(attempts > 0),
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    resolved_at_utc TEXT NULL,
    UNIQUE(source_id, source_revision_id, source_attachment_id, issue_code)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_sync_issues_unresolved
ON ProductionInboxSyncIssues(source_id, resolved_at_utc, source_revision_id);

CREATE TRIGGER IF NOT EXISTS trg_production_inbox_messages_immutable_update
BEFORE UPDATE ON ProductionInboxMessages
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxMessages snapshot is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_inbox_messages_immutable_delete
BEFORE DELETE ON ProductionInboxMessages
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxMessages snapshot cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_inbox_attachments_immutable_update
BEFORE UPDATE ON ProductionInboxAttachments
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxAttachments snapshot is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_inbox_attachments_immutable_delete
BEFORE DELETE ON ProductionInboxAttachments
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxAttachments snapshot cannot be deleted');
END;
"""


PRODUCTION_INBOX_GROUPING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionInboxSourceTombstones (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    source_tombstone_id INTEGER NOT NULL CHECK(source_tombstone_id > 0),
    source_message_id TEXT NOT NULL,
    chat_id INTEGER NULL,
    deleted_at_utc TEXT NOT NULL,
    raw_update_json TEXT NOT NULL,
    transported_at_utc TEXT NOT NULL,
    UNIQUE(source_id, source_tombstone_id),
    UNIQUE(source_id, source_message_id, deleted_at_utc)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_tombstones_message
ON ProductionInboxSourceTombstones(source_id, source_message_id, deleted_at_utc);

CREATE TABLE IF NOT EXISTS ProductionInboxTombstoneSyncState (
    source_id INTEGER PRIMARY KEY
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    cursor_tombstone_id INTEGER NOT NULL DEFAULT 0 CHECK(cursor_tombstone_id >= 0),
    last_sync_at_utc TEXT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ProductionInboxBundles (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL
        REFERENCES ProductionInboxSources(id) ON DELETE RESTRICT,
    chat_id INTEGER NULL,
    sender_max_user_id INTEGER NULL,
    sender_display_snapshot TEXT NOT NULL DEFAULT '',
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT NOT NULL,
    grouping_status TEXT NOT NULL CHECK(grouping_status IN (
        'collecting', 'complete', 'needs_description', 'text_only', 'invalid'
    )),
    close_reason TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'deterministic'
        CHECK(origin IN ('deterministic', 'manual')),
    grouping_rule_version TEXT NOT NULL,
    grouping_window_seconds INTEGER NOT NULL CHECK(grouping_window_seconds > 0),
    day_boundary_utc_offset_minutes INTEGER NOT NULL
        CHECK(day_boundary_utc_offset_minutes BETWEEN -840 AND 840),
    source_fingerprint TEXT NOT NULL UNIQUE CHECK(length(source_fingerprint) = 64),
    supersedes_bundle_id INTEGER NULL
        REFERENCES ProductionInboxBundles(id) ON DELETE RESTRICT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    superseded_at_utc TEXT NULL,
    superseded_reason TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK(supersedes_bundle_id IS NULL OR supersedes_bundle_id <> id),
    CHECK(is_current = 1 OR superseded_at_utc IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_bundles_current
ON ProductionInboxBundles(source_id, is_current, started_at_utc);
CREATE INDEX IF NOT EXISTS idx_production_inbox_bundles_sender
ON ProductionInboxBundles(source_id, chat_id, sender_max_user_id, started_at_utc);
CREATE INDEX IF NOT EXISTS idx_production_inbox_bundles_lineage
ON ProductionInboxBundles(supersedes_bundle_id);

CREATE TABLE IF NOT EXISTS ProductionInboxBundleMessages (
    bundle_id INTEGER NOT NULL
        REFERENCES ProductionInboxBundles(id) ON DELETE RESTRICT,
    inbox_message_id INTEGER NOT NULL
        REFERENCES ProductionInboxMessages(id) ON DELETE RESTRICT,
    bundle_order INTEGER NOT NULL CHECK(bundle_order >= 0),
    message_role TEXT NOT NULL CHECK(message_role IN (
        'photo_source', 'closing_text', 'captioned_media', 'text_only', 'source_only'
    )),
    PRIMARY KEY(bundle_id, inbox_message_id),
    UNIQUE(bundle_id, bundle_order)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_bundle_messages_message
ON ProductionInboxBundleMessages(inbox_message_id, bundle_id);

CREATE TRIGGER IF NOT EXISTS trg_production_inbox_tombstones_immutable_update
BEFORE UPDATE ON ProductionInboxSourceTombstones
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxSourceTombstones snapshot is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_inbox_tombstones_immutable_delete
BEFORE DELETE ON ProductionInboxSourceTombstones
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxSourceTombstones snapshot cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_inbox_bundle_messages_immutable_update
BEFORE UPDATE ON ProductionInboxBundleMessages
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxBundleMessages relation is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_inbox_bundle_messages_immutable_delete
BEFORE DELETE ON ProductionInboxBundleMessages
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxBundleMessages relation cannot be deleted');
END;
"""


PRODUCTION_INBOX_MATCHING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionStageAliases (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    stage_id INTEGER NOT NULL
        REFERENCES ProductionStages(id) ON DELETE RESTRICT,
    alias_text TEXT NOT NULL,
    normalized_alias TEXT NOT NULL COLLATE NOCASE UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_production_stage_aliases_stage
ON ProductionStageAliases(stage_id, is_active);

CREATE TABLE IF NOT EXISTS ProductionInboxMatchRuns (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    bundle_id INTEGER NOT NULL
        REFERENCES ProductionInboxBundles(id) ON DELETE RESTRICT,
    bundle_fingerprint TEXT NOT NULL CHECK(length(bundle_fingerprint) = 64),
    matcher_rule_version TEXT NOT NULL,
    directory_context_fingerprint TEXT NOT NULL
        CHECK(length(directory_context_fingerprint) = 64),
    input_text_hash TEXT NOT NULL CHECK(length(input_text_hash) = 64),
    result_fingerprint TEXT NOT NULL CHECK(length(result_fingerprint) = 64),
    source_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    has_media INTEGER NOT NULL CHECK(has_media IN (0, 1)),
    status TEXT NOT NULL CHECK(status IN ('matched', 'needs_review', 'no_text')),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    supersedes_match_run_id INTEGER NULL
        REFERENCES ProductionInboxMatchRuns(id) ON DELETE RESTRICT,
    superseded_at_utc TEXT NULL,
    superseded_reason TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    CHECK(supersedes_match_run_id IS NULL OR supersedes_match_run_id <> id),
    CHECK(is_current = 1 OR superseded_at_utc IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_production_inbox_match_runs_current
ON ProductionInboxMatchRuns(bundle_id)
WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_production_inbox_match_runs_context
ON ProductionInboxMatchRuns(
    bundle_id, bundle_fingerprint, matcher_rule_version,
    directory_context_fingerprint, input_text_hash
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_match_runs_lineage
ON ProductionInboxMatchRuns(supersedes_match_run_id);

CREATE TABLE IF NOT EXISTS ProductionInboxProposals (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    match_run_id INTEGER NOT NULL
        REFERENCES ProductionInboxMatchRuns(id) ON DELETE RESTRICT,
    proposal_order INTEGER NOT NULL CHECK(proposal_order >= 0),
    source_segment_text TEXT NOT NULL,
    normalized_segment_text TEXT NOT NULL,
    source_segment_start INTEGER NULL CHECK(source_segment_start IS NULL OR source_segment_start >= 0),
    source_segment_end INTEGER NULL CHECK(source_segment_end IS NULL OR source_segment_end >= 0),
    object_id INTEGER NULL,
    object_match_method TEXT NULL,
    product_id INTEGER NULL,
    product_match_method TEXT NULL,
    stage_id INTEGER NULL REFERENCES ProductionStages(id) ON DELETE RESTRICT,
    stage_match_method TEXT NULL,
    readiness_percent INTEGER NULL
        CHECK(readiness_percent IS NULL OR readiness_percent BETWEEN 0 AND 100),
    readiness_match_method TEXT NULL,
    description_text TEXT NOT NULL,
    normalized_description TEXT NOT NULL,
    match_quality TEXT NOT NULL CHECK(match_quality IN ('exact', 'strong', 'ambiguous', 'none')),
    requires_review INTEGER NOT NULL CHECK(requires_review IN (0, 1)),
    issue_code TEXT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(match_run_id, proposal_order),
    CHECK(
        source_segment_start IS NULL OR source_segment_end IS NULL
        OR source_segment_end >= source_segment_start
    )
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_proposals_product
ON ProductionInboxProposals(product_id);
CREATE INDEX IF NOT EXISTS idx_production_inbox_proposals_object
ON ProductionInboxProposals(object_id);
CREATE INDEX IF NOT EXISTS idx_production_inbox_proposals_stage
ON ProductionInboxProposals(stage_id);

CREATE TABLE IF NOT EXISTS ProductionInboxProductCandidates (
    proposal_id INTEGER NOT NULL
        REFERENCES ProductionInboxProposals(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL,
    rank INTEGER NOT NULL CHECK(rank > 0),
    deterministic_score INTEGER NOT NULL CHECK(deterministic_score BETWEEN 0 AND 100),
    match_method TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    evidence TEXT NOT NULL,
    is_active_snapshot INTEGER NOT NULL CHECK(is_active_snapshot IN (0, 1)),
    object_id_snapshot INTEGER NOT NULL,
    PRIMARY KEY(proposal_id, product_id),
    UNIQUE(proposal_id, rank)
);

CREATE TABLE IF NOT EXISTS ProductionInboxObjectCandidates (
    proposal_id INTEGER NOT NULL
        REFERENCES ProductionInboxProposals(id) ON DELETE RESTRICT,
    object_id INTEGER NOT NULL,
    rank INTEGER NOT NULL CHECK(rank > 0),
    deterministic_score INTEGER NOT NULL CHECK(deterministic_score BETWEEN 0 AND 100),
    match_method TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    evidence TEXT NOT NULL,
    is_active_snapshot INTEGER NOT NULL CHECK(is_active_snapshot IN (0, 1)),
    PRIMARY KEY(proposal_id, object_id),
    UNIQUE(proposal_id, rank)
);

CREATE TABLE IF NOT EXISTS ProductionInboxStageCandidates (
    proposal_id INTEGER NOT NULL
        REFERENCES ProductionInboxProposals(id) ON DELETE RESTRICT,
    stage_id INTEGER NOT NULL
        REFERENCES ProductionStages(id) ON DELETE RESTRICT,
    rank INTEGER NOT NULL CHECK(rank > 0),
    deterministic_score INTEGER NOT NULL CHECK(deterministic_score BETWEEN 0 AND 100),
    match_method TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    evidence TEXT NOT NULL,
    is_active_snapshot INTEGER NOT NULL CHECK(is_active_snapshot IN (0, 1)),
    PRIMARY KEY(proposal_id, stage_id),
    UNIQUE(proposal_id, rank)
);

CREATE TABLE IF NOT EXISTS ProductionInboxProposalEvidence (
    proposal_id INTEGER NOT NULL
        REFERENCES ProductionInboxProposals(id) ON DELETE RESTRICT,
    field_name TEXT NOT NULL CHECK(field_name IN ('segmentation', 'object', 'product', 'stage', 'readiness')),
    evidence_order INTEGER NOT NULL CHECK(evidence_order >= 0),
    match_method TEXT NOT NULL,
    matched_text TEXT NOT NULL,
    explanation TEXT NOT NULL,
    PRIMARY KEY(proposal_id, field_name, evidence_order)
);

CREATE TABLE IF NOT EXISTS ProductionInboxProposalIssues (
    proposal_id INTEGER NOT NULL
        REFERENCES ProductionInboxProposals(id) ON DELETE RESTRICT,
    issue_order INTEGER NOT NULL CHECK(issue_order >= 0),
    issue_code TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(proposal_id, issue_order)
);

CREATE TRIGGER IF NOT EXISTS trg_production_match_runs_immutable
BEFORE UPDATE OF
    uid, bundle_id, bundle_fingerprint, matcher_rule_version,
    directory_context_fingerprint, input_text_hash, result_fingerprint,
    source_text, normalized_text, has_media, status, created_at_utc
ON ProductionInboxMatchRuns
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxMatchRun interpretation is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_match_runs_no_delete
BEFORE DELETE ON ProductionInboxMatchRuns
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxMatchRun cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_proposals_immutable_update
BEFORE UPDATE ON ProductionInboxProposals
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProposal is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_proposals_immutable_delete
BEFORE DELETE ON ProductionInboxProposals
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProposal cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_product_candidates_immutable_update
BEFORE UPDATE ON ProductionInboxProductCandidates
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProductCandidate is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_product_candidates_immutable_delete
BEFORE DELETE ON ProductionInboxProductCandidates
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProductCandidate cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_object_candidates_immutable_update
BEFORE UPDATE ON ProductionInboxObjectCandidates
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxObjectCandidate is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_object_candidates_immutable_delete
BEFORE DELETE ON ProductionInboxObjectCandidates
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxObjectCandidate cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_stage_candidates_immutable_update
BEFORE UPDATE ON ProductionInboxStageCandidates
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxStageCandidate is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_stage_candidates_immutable_delete
BEFORE DELETE ON ProductionInboxStageCandidates
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxStageCandidate cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_evidence_immutable_update
BEFORE UPDATE ON ProductionInboxProposalEvidence
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProposalEvidence is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_evidence_immutable_delete
BEFORE DELETE ON ProductionInboxProposalEvidence
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProposalEvidence cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_issues_immutable_update
BEFORE UPDATE ON ProductionInboxProposalIssues
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProposalIssue is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_issues_immutable_delete
BEFORE DELETE ON ProductionInboxProposalIssues
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxProposalIssue cannot be deleted');
END;
"""


PRODUCTION_STAGE_ALIAS_SEED: tuple[tuple[str, str], ...] = (
    ("METALWORK", "слесарка"),
    ("METALWORK", "слесарные работы"),
    ("EQUIPMENT_INSTALLATION", "монтаж оборудования"),
    ("EQUIPMENT_INSTALLATION", "установка оборудования"),
    ("ELECTRICAL_INSTALLATION", "электромонтаж"),
    ("ELECTRICAL_INSTALLATION", "электромонтажные работы"),
    ("MARKING", "маркировка"),
    ("PROGRAMMING", "программирование"),
    ("TESTING", "проверка"),
    ("TESTING", "испытания"),
    ("QUALITY_CONTROL", "отк"),
    ("QUALITY_CONTROL", "контроль качества"),
    ("PACKAGING", "упаковка"),
    ("PREPARATION", "подготовка"),
)


PRODUCTION_INBOX_REVIEW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ProductionInboxReviews (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    bundle_id INTEGER NOT NULL
        REFERENCES ProductionInboxBundles(id) ON DELETE RESTRICT,
    bundle_fingerprint TEXT NOT NULL CHECK(length(bundle_fingerprint) = 64),
    match_run_id INTEGER NULL
        REFERENCES ProductionInboxMatchRuns(id) ON DELETE RESTRICT,
    proposal_id INTEGER NULL
        REFERENCES ProductionInboxProposals(id) ON DELETE RESTRICT,
    matcher_rule_version TEXT NOT NULL DEFAULT '',
    directory_context_fingerprint TEXT NOT NULL DEFAULT '',
    decision_kind TEXT NOT NULL CHECK(decision_kind IN (
        'confirm', 'reject', 'keep_existing', 'correction'
    )),
    status TEXT NOT NULL CHECK(status IN (
        'confirming', 'confirmed', 'rejected', 'kept_existing', 'failed'
    )),
    source_text_snapshot TEXT NOT NULL DEFAULT '',
    selected_product_id INTEGER NULL,
    selected_stage_id INTEGER NULL
        REFERENCES ProductionStages(id) ON DELETE RESTRICT,
    selected_readiness_percent INTEGER NULL CHECK(
        selected_readiness_percent IS NULL
        OR selected_readiness_percent BETWEEN 0 AND 100
    ),
    final_description TEXT NOT NULL DEFAULT '',
    observed_at_utc TEXT NULL,
    reported_by_employee_id INTEGER NULL,
    event_type TEXT NULL CHECK(event_type IS NULL OR event_type IN (
        'observation', 'baseline', 'correction', 'rework'
    )),
    change_reason TEXT NOT NULL DEFAULT '',
    correction_source_event_id INTEGER NULL
        REFERENCES ProductionEvents(id) ON DELETE RESTRICT,
    production_event_id INTEGER NULL
        REFERENCES ProductionEvents(id) ON DELETE RESTRICT,
    rejection_code TEXT NOT NULL DEFAULT '',
    rejection_comment TEXT NOT NULL DEFAULT '',
    decision_actor_type TEXT NOT NULL,
    decision_actor_uid TEXT NOT NULL,
    decision_actor_local_user_id INTEGER NULL,
    decision_actor_external_ref TEXT NOT NULL DEFAULT '',
    decision_actor_display_name_snapshot TEXT NOT NULL DEFAULT '',
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    previous_review_id INTEGER NULL
        REFERENCES ProductionInboxReviews(id) ON DELETE RESTRICT,
    created_at_utc TEXT NOT NULL,
    decided_at_utc TEXT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK(proposal_id IS NOT NULL OR decision_kind = 'keep_existing'),
    CHECK(previous_review_id IS NULL OR previous_review_id <> id),
    CHECK(status <> 'confirmed' OR production_event_id IS NOT NULL),
    CHECK(status <> 'rejected' OR rejection_code <> '')
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_production_inbox_reviews_current
ON ProductionInboxReviews(bundle_id, COALESCE(proposal_id, -1))
WHERE is_current = 1;
CREATE UNIQUE INDEX IF NOT EXISTS ux_production_inbox_reviews_event
ON ProductionInboxReviews(production_event_id)
WHERE production_event_id IS NOT NULL AND status = 'confirmed';
CREATE INDEX IF NOT EXISTS idx_production_inbox_reviews_status
ON ProductionInboxReviews(status, is_current, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_production_inbox_reviews_bundle
ON ProductionInboxReviews(bundle_id, proposal_id, is_current);

CREATE TABLE IF NOT EXISTS ProductionInboxReviewActions (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    review_id INTEGER NOT NULL
        REFERENCES ProductionInboxReviews(id) ON DELETE RESTRICT,
    action_type TEXT NOT NULL CHECK(action_type IN (
        'started', 'confirmed', 'rejected', 'failed', 'recovered',
        'kept_existing', 'alias_created'
    )),
    actor_type TEXT NOT NULL,
    actor_uid TEXT NOT NULL,
    actor_local_user_id INTEGER NULL,
    actor_external_ref TEXT NOT NULL DEFAULT '',
    actor_display_name_snapshot TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_review_actions_review
ON ProductionInboxReviewActions(review_id, created_at_utc, id);

CREATE TABLE IF NOT EXISTS ProductionInboxReviewAttachmentPromotions (
    review_id INTEGER NOT NULL
        REFERENCES ProductionInboxReviews(id) ON DELETE RESTRICT,
    inbox_attachment_id INTEGER NOT NULL
        REFERENCES ProductionInboxAttachments(id) ON DELETE RESTRICT,
    source_order INTEGER NOT NULL CHECK(source_order >= 0),
    source_message_id TEXT NOT NULL,
    source_attachment_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    production_attachment_id INTEGER NULL
        REFERENCES Attachments(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('intended', 'materialized', 'failed')),
    error_message TEXT NOT NULL DEFAULT '',
    materialized_at_utc TEXT NULL,
    PRIMARY KEY(review_id, inbox_attachment_id),
    UNIQUE(review_id, source_order)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_promotions_attachment
ON ProductionInboxReviewAttachmentPromotions(production_attachment_id)
WHERE production_attachment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ProductionInboxManualBundleSources (
    manual_bundle_id INTEGER NOT NULL
        REFERENCES ProductionInboxBundles(id) ON DELETE RESTRICT,
    source_bundle_id INTEGER NOT NULL
        REFERENCES ProductionInboxBundles(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL CHECK(operation IN ('split', 'merge')),
    actor_type TEXT NOT NULL,
    actor_uid TEXT NOT NULL,
    actor_local_user_id INTEGER NULL,
    actor_external_ref TEXT NOT NULL DEFAULT '',
    actor_display_name_snapshot TEXT NOT NULL DEFAULT '',
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(manual_bundle_id, source_bundle_id),
    CHECK(manual_bundle_id <> source_bundle_id)
);
CREATE INDEX IF NOT EXISTS idx_production_inbox_manual_sources_original
ON ProductionInboxManualBundleSources(source_bundle_id, manual_bundle_id);

CREATE TRIGGER IF NOT EXISTS trg_production_review_identity_immutable
BEFORE UPDATE OF
    uid, bundle_id, bundle_fingerprint, match_run_id, proposal_id,
    matcher_rule_version, directory_context_fingerprint, decision_kind,
    source_text_snapshot, selected_product_id, selected_stage_id,
    selected_readiness_percent, final_description, observed_at_utc,
    reported_by_employee_id, event_type, change_reason,
    correction_source_event_id, decision_actor_type, decision_actor_uid,
    decision_actor_local_user_id, decision_actor_external_ref,
    decision_actor_display_name_snapshot, created_at_utc
ON ProductionInboxReviews
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxReview decision snapshot is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_review_no_delete
BEFORE DELETE ON ProductionInboxReviews
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxReview cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_review_actions_immutable_update
BEFORE UPDATE ON ProductionInboxReviewActions
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxReviewAction is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_review_actions_immutable_delete
BEFORE DELETE ON ProductionInboxReviewActions
BEGIN
    SELECT RAISE(ABORT, 'ProductionInboxReviewAction cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_manual_lineage_immutable_update
BEFORE UPDATE ON ProductionInboxManualBundleSources
BEGIN
    SELECT RAISE(ABORT, 'ProductionInbox manual lineage is immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_production_manual_lineage_immutable_delete
BEFORE DELETE ON ProductionInboxManualBundleSources
BEGIN
    SELECT RAISE(ABORT, 'ProductionInbox manual lineage cannot be deleted');
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


def apply_production_source_transport_migration(
    connection: sqlite3.Connection,
) -> None:
    """Create the immutable P8 source transport without logical grouping."""

    from schema_migrations import execute_sql_script

    execute_sql_script(connection, PRODUCTION_SOURCE_TRANSPORT_SCHEMA_SQL)


def apply_production_inbox_grouping_migration(
    connection: sqlite3.Connection,
) -> None:
    """Create P9 deterministic grouping and tombstone transport tables."""

    from schema_migrations import execute_sql_script

    execute_sql_script(connection, PRODUCTION_INBOX_GROUPING_SCHEMA_SQL)


def apply_production_inbox_matching_migration(
    connection: sqlite3.Connection,
) -> None:
    """Create P10 matching persistence and conservative stage aliases."""

    from schema_migrations import execute_sql_script

    execute_sql_script(connection, PRODUCTION_INBOX_MATCHING_SCHEMA_SQL)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for stage_code, alias_text in PRODUCTION_STAGE_ALIAS_SEED:
        stage = connection.execute(
            "SELECT id FROM ProductionStages WHERE code = ? COLLATE NOCASE",
            (stage_code,),
        ).fetchone()
        if stage is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO ProductionStageAliases (
                uid, stage_id, alias_text, normalized_alias, is_active,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (
                str(uuid4()), int(stage[0]), alias_text,
                normalize_alias_text(alias_text), now, now,
            ),
        )


def apply_production_inbox_review_migration(connection: sqlite3.Connection) -> None:
    """Create P11 review, audit, media-promotion and manual-lineage storage."""

    from schema_migrations import execute_sql_script

    execute_sql_script(connection, PRODUCTION_INBOX_REVIEW_SCHEMA_SQL)
