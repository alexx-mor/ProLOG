"""WorkBot schema migrations beyond the stable v1 baseline."""

from __future__ import annotations

import sqlite3

from schema_migrations import execute_sql_script


WORKBOT_SOURCE_MEDIA_SCHEMA = """
CREATE TABLE source_messages (
    source_message_id TEXT PRIMARY KEY,
    chat_id INTEGER NULL,
    sender_max_user_id INTEGER NOT NULL,
    sender_display_snapshot TEXT NOT NULL DEFAULT '',
    message_timestamp_utc TEXT NOT NULL,
    source_sequence INTEGER NULL,
    first_received_at_utc TEXT NOT NULL,
    last_received_at_utc TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0, 1)),
    deleted_at_utc TEXT NULL,
    FOREIGN KEY(sender_max_user_id) REFERENCES users(max_user_id)
);

CREATE TABLE source_message_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
    source_sequence INTEGER NULL,
    source_text TEXT NULL,
    content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
    content_json TEXT NOT NULL,
    raw_envelope_json TEXT NOT NULL,
    message_timestamp_utc TEXT NOT NULL,
    edited_at_utc TEXT NULL,
    received_at_utc TEXT NOT NULL,
    FOREIGN KEY(source_message_id) REFERENCES source_messages(source_message_id)
        ON DELETE RESTRICT,
    UNIQUE(source_message_id, revision_number),
    UNIQUE(source_message_id, content_hash)
);

CREATE TABLE source_message_tombstones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL,
    chat_id INTEGER NULL,
    deleted_at_utc TEXT NOT NULL,
    raw_update_json TEXT NOT NULL,
    UNIQUE(source_message_id, deleted_at_utc)
);

CREATE TABLE source_message_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id INTEGER NOT NULL,
    source_attachment_id TEXT NOT NULL,
    identity_kind TEXT NOT NULL,
    source_order INTEGER NOT NULL CHECK(source_order >= 0),
    attachment_type TEXT NOT NULL,
    mime_type TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL DEFAULT '',
    source_size INTEGER NULL CHECK(source_size IS NULL OR source_size >= 0),
    source_url TEXT NULL,
    source_token TEXT NULL,
    source_payload_json TEXT NOT NULL,
    download_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(download_status IN ('pending', 'downloading', 'downloaded', 'failed', 'unavailable')),
    sha256 TEXT NOT NULL DEFAULT '',
    storage_key TEXT NOT NULL DEFAULT '',
    download_attempts INTEGER NOT NULL DEFAULT 0 CHECK(download_attempts >= 0),
    last_error TEXT NOT NULL DEFAULT '',
    received_at_utc TEXT NOT NULL,
    last_attempt_at_utc TEXT NULL,
    next_retry_at_utc TEXT NULL,
    downloaded_at_utc TEXT NULL,
    FOREIGN KEY(revision_id) REFERENCES source_message_revisions(id)
        ON DELETE RESTRICT,
    UNIQUE(revision_id, source_attachment_id),
    UNIQUE(revision_id, source_order),
    CHECK(
        download_status <> 'downloaded'
        OR (length(sha256) = 64 AND storage_key <> '' AND downloaded_at_utc IS NOT NULL)
    )
);

CREATE INDEX idx_source_messages_chat_time
    ON source_messages(chat_id, message_timestamp_utc, source_sequence);
CREATE INDEX idx_source_messages_sender_time
    ON source_messages(sender_max_user_id, message_timestamp_utc);
CREATE INDEX idx_source_revisions_message
    ON source_message_revisions(source_message_id, revision_number);
CREATE INDEX idx_source_attachments_revision_order
    ON source_message_attachments(revision_id, source_order);
CREATE INDEX idx_source_attachments_download
    ON source_message_attachments(download_status, next_retry_at_utc);
CREATE INDEX idx_source_attachments_sha256
    ON source_message_attachments(sha256) WHERE sha256 <> '';
CREATE INDEX idx_source_attachments_storage_key
    ON source_message_attachments(storage_key) WHERE storage_key <> '';
"""


def apply_workbot_source_media_migration(connection: sqlite3.Connection) -> None:
    execute_sql_script(connection, WORKBOT_SOURCE_MEDIA_SCHEMA)
