"""SQLite persistence and effective source queries for deterministic P9 grouping."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from database import Database
from production.grouping_models import (
    BundleCandidate,
    BundleOrigin,
    EffectiveInboxMessage,
    GroupingResult,
    GroupingStatus,
    ProductionInboxGroupedBundle,
)


class ProductionInboxGroupingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_effective_messages(
        self,
        source_id: int | None = None,
    ) -> list[EffectiveInboxMessage]:
        condition = "" if source_id is None else "AND message.source_id = ?"
        params: tuple[object, ...] = () if source_id is None else (source_id,)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                WITH latest AS (
                    SELECT source_id, source_message_id,
                           MAX(source_revision_number) AS revision_number
                    FROM ProductionInboxMessages
                    GROUP BY source_id, source_message_id
                )
                SELECT message.*
                FROM ProductionInboxMessages message
                JOIN latest
                  ON latest.source_id = message.source_id
                 AND latest.source_message_id = message.source_message_id
                 AND latest.revision_number = message.source_revision_number
                WHERE NOT EXISTS (
                    SELECT 1 FROM ProductionInboxSourceTombstones tombstone
                    WHERE tombstone.source_id = message.source_id
                      AND tombstone.source_message_id = message.source_message_id
                )
                {condition}
                ORDER BY message.source_id, message.message_timestamp_utc,
                         CASE WHEN message.source_sequence IS NULL THEN 1 ELSE 0 END,
                         message.source_sequence, message.source_message_id,
                         message.source_revision_number, message.source_revision_id
                """,
                params,
            ).fetchall()
            attachment_rows = connection.execute(
                """
                SELECT inbox_message_id, source_order
                FROM ProductionInboxAttachments
                ORDER BY inbox_message_id, source_order
                """
            ).fetchall()
        orders: dict[int, list[int]] = {}
        for row in attachment_rows:
            orders.setdefault(int(row["inbox_message_id"]), []).append(
                int(row["source_order"])
            )
        return [
            EffectiveInboxMessage(
                id=int(row["id"]),
                source_id=int(row["source_id"]),
                source_message_id=str(row["source_message_id"]),
                source_revision_id=int(row["source_revision_id"]),
                source_revision_number=int(row["source_revision_number"]),
                chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
                sender_max_user_id=(
                    int(row["sender_max_user_id"])
                    if row["sender_max_user_id"] is not None else None
                ),
                sender_display_snapshot=str(row["sender_display_snapshot"]),
                message_timestamp_utc=_datetime(row["message_timestamp_utc"]),
                source_sequence=(
                    int(row["source_sequence"])
                    if row["source_sequence"] is not None else None
                ),
                source_text=str(row["source_text"] or ""),
                content_hash=str(row["content_hash"]),
                attachment_orders=tuple(orders.get(int(row["id"]), ())),
            )
            for row in rows
        ]

    def grouping_source_ids(self) -> tuple[int, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id FROM ProductionInboxMessages
                UNION SELECT source_id FROM ProductionInboxBundles
                UNION SELECT source_id FROM ProductionInboxSourceTombstones
                ORDER BY source_id
                """
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def reconcile(
        self,
        candidates: tuple[BundleCandidate, ...],
        source_ids: tuple[int, ...],
        *,
        effective_message_count: int,
        now_utc: datetime,
    ) -> GroupingResult:
        if not source_ids:
            return GroupingResult(0, effective_message_count, len(candidates))
        placeholders = ",".join("?" for _ in source_ids)
        now = _iso(now_utc)
        created = unchanged = updated = superseded = 0
        with self.database.connect() as connection:
            current_rows = connection.execute(
                f"""
                SELECT * FROM ProductionInboxBundles
                WHERE origin = 'deterministic' AND is_current = 1
                  AND source_id IN ({placeholders})
                ORDER BY id
                """,
                source_ids,
            ).fetchall()
            relation_rows = connection.execute(
                f"""
                SELECT relation.bundle_id, message.source_message_id
                FROM ProductionInboxBundleMessages relation
                JOIN ProductionInboxMessages message
                  ON message.id = relation.inbox_message_id
                JOIN ProductionInboxBundles bundle ON bundle.id = relation.bundle_id
                WHERE bundle.origin = 'deterministic' AND bundle.is_current = 1
                  AND bundle.source_id IN ({placeholders})
                """,
                source_ids,
            ).fetchall()
            old_message_ids: dict[int, set[str]] = {}
            for row in relation_rows:
                old_message_ids.setdefault(int(row["bundle_id"]), set()).add(
                    str(row["source_message_id"])
                )
            by_fingerprint = {
                str(row["source_fingerprint"]): row for row in current_rows
            }
            kept_ids: set[int] = set()
            new_candidates: list[BundleCandidate] = []
            for candidate in candidates:
                existing = by_fingerprint.get(candidate.source_fingerprint)
                if existing is None:
                    new_candidates.append(candidate)
                    continue
                bundle_id = int(existing["id"])
                kept_ids.add(bundle_id)
                if (
                    str(existing["grouping_status"]) != candidate.grouping_status.value
                    or str(existing["close_reason"]) != candidate.close_reason
                    or str(existing["sender_display_snapshot"])
                    != candidate.sender_display_snapshot
                ):
                    connection.execute(
                        """
                        UPDATE ProductionInboxBundles
                        SET grouping_status = ?, close_reason = ?,
                            sender_display_snapshot = ?, ended_at_utc = ?,
                            updated_at_utc = ?
                        WHERE id = ? AND is_current = 1
                        """,
                        (
                            candidate.grouping_status.value,
                            candidate.close_reason,
                            candidate.sender_display_snapshot,
                            _iso(candidate.ended_at_utc),
                            now,
                            bundle_id,
                        ),
                    )
                    updated += 1
                else:
                    unchanged += 1

            available_old = [
                row for row in current_rows if int(row["id"]) not in kept_ids
            ]
            for candidate in new_candidates:
                predecessor_id = _best_predecessor(
                    candidate, available_old, old_message_ids
                )
                cursor = connection.execute(
                    """
                    INSERT INTO ProductionInboxBundles (
                        uid, source_id, chat_id, sender_max_user_id,
                        sender_display_snapshot, started_at_utc, ended_at_utc,
                        grouping_status, close_reason, origin,
                        grouping_rule_version, grouping_window_seconds,
                        day_boundary_utc_offset_minutes, source_fingerprint,
                        supersedes_bundle_id, is_current, created_at_utc,
                        updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'deterministic',
                              ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        str(uuid4()), candidate.source_id, candidate.chat_id,
                        candidate.sender_max_user_id,
                        candidate.sender_display_snapshot,
                        _iso(candidate.started_at_utc), _iso(candidate.ended_at_utc),
                        candidate.grouping_status.value, candidate.close_reason,
                        candidate.grouping_rule_version,
                        candidate.grouping_window_seconds,
                        candidate.day_boundary_utc_offset_minutes,
                        candidate.source_fingerprint, predecessor_id, now, now,
                    ),
                )
                bundle_id = int(cursor.lastrowid)
                for order, item in enumerate(candidate.messages):
                    connection.execute(
                        """
                        INSERT INTO ProductionInboxBundleMessages (
                            bundle_id, inbox_message_id, bundle_order, message_role
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (bundle_id, item.message.id, order, item.role.value),
                    )
                created += 1

            obsolete_ids = [
                int(row["id"])
                for row in current_rows
                if int(row["id"]) not in kept_ids
            ]
            if obsolete_ids:
                obsolete_placeholders = ",".join("?" for _ in obsolete_ids)
                connection.execute(
                    f"""
                    UPDATE ProductionInboxBundles
                    SET is_current = 0, superseded_at_utc = ?,
                        superseded_reason = 'effective_source_changed',
                        updated_at_utc = ?
                    WHERE id IN ({obsolete_placeholders}) AND is_current = 1
                    """,
                    (now, now, *obsolete_ids),
                )
                superseded = len(obsolete_ids)
        return GroupingResult(
            source_count=len(source_ids),
            effective_message_count=effective_message_count,
            candidate_count=len(candidates),
            created_count=created,
            unchanged_count=unchanged,
            updated_count=updated,
            superseded_count=superseded,
        )

    def list_bundles(
        self,
        *,
        source_id: int | None = None,
        current_only: bool = False,
    ) -> list[ProductionInboxGroupedBundle]:
        conditions: list[str] = []
        params: list[object] = []
        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)
        if current_only:
            conditions.append("is_current = 1")
        query = "SELECT * FROM ProductionInboxBundles"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at_utc, id"
        with self.database.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_bundle(row) for row in rows]

    def list_bundle_message_rows(self, bundle_id: int | None = None):
        query = """
            SELECT relation.*, message.source_id, message.chat_id,
                   message.sender_max_user_id, message.source_message_id,
                   message.source_revision_id, message.source_revision_number,
                   message.content_hash, message.message_timestamp_utc
            FROM ProductionInboxBundleMessages relation
            JOIN ProductionInboxMessages message
              ON message.id = relation.inbox_message_id
        """
        params: tuple[object, ...] = ()
        if bundle_id is not None:
            query += " WHERE relation.bundle_id = ?"
            params = (bundle_id,)
        query += " ORDER BY relation.bundle_id, relation.bundle_order"
        with self.database.connect() as connection:
            return connection.execute(query, params).fetchall()

    def raw_diagnostics(self) -> dict[str, object]:
        with self.database.connect() as connection:
            return {
                "current_bundles": connection.execute(
                    """
                    SELECT * FROM ProductionInboxBundles
                    WHERE is_current = 1 ORDER BY id
                    """
                ).fetchall(),
                "historical_count": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM ProductionInboxBundles WHERE is_current = 0"
                    ).fetchone()[0]
                ),
                "relations": connection.execute(
                    """
                    SELECT relation.*, bundle.source_id AS bundle_source_id,
                           bundle.chat_id AS bundle_chat_id,
                           bundle.sender_max_user_id AS bundle_sender_id,
                           bundle.is_current, message.source_id,
                           message.chat_id, message.sender_max_user_id,
                           message.source_message_id, message.source_revision_id,
                           message.source_revision_number, message.content_hash
                    FROM ProductionInboxBundleMessages relation
                    JOIN ProductionInboxBundles bundle ON bundle.id = relation.bundle_id
                    JOIN ProductionInboxMessages message
                      ON message.id = relation.inbox_message_id
                    ORDER BY relation.bundle_id, relation.bundle_order
                    """
                ).fetchall(),
                "attachment_orders": connection.execute(
                    """
                    SELECT inbox_message_id, source_order
                    FROM ProductionInboxAttachments
                    ORDER BY inbox_message_id, source_order
                    """
                ).fetchall(),
                "lineage": connection.execute(
                    """
                    SELECT child.id, child.supersedes_bundle_id, parent.id AS parent_id
                    FROM ProductionInboxBundles child
                    LEFT JOIN ProductionInboxBundles parent
                      ON parent.id = child.supersedes_bundle_id
                    WHERE child.supersedes_bundle_id IS NOT NULL
                    """
                ).fetchall(),
            }


def _best_predecessor(candidate, rows, old_message_ids) -> int | None:
    candidate_ids = {item.message.source_message_id for item in candidate.messages}
    best: tuple[int, int] | None = None
    for row in rows:
        if int(row["source_id"]) != candidate.source_id:
            continue
        if row["chat_id"] != candidate.chat_id:
            continue
        if row["sender_max_user_id"] != candidate.sender_max_user_id:
            continue
        row_id = int(row["id"])
        overlap = len(candidate_ids & old_message_ids.get(row_id, set()))
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, row_id)
    return best[1] if best else None


def _bundle(row) -> ProductionInboxGroupedBundle:
    return ProductionInboxGroupedBundle(
        id=int(row["id"]),
        uid=UUID(str(row["uid"])),
        source_id=int(row["source_id"]),
        chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
        sender_max_user_id=(
            int(row["sender_max_user_id"])
            if row["sender_max_user_id"] is not None else None
        ),
        sender_display_snapshot=str(row["sender_display_snapshot"]),
        started_at_utc=_datetime(row["started_at_utc"]),
        ended_at_utc=_datetime(row["ended_at_utc"]),
        grouping_status=GroupingStatus(str(row["grouping_status"])),
        close_reason=str(row["close_reason"]),
        origin=BundleOrigin(str(row["origin"])),
        grouping_rule_version=str(row["grouping_rule_version"]),
        grouping_window_seconds=int(row["grouping_window_seconds"]),
        day_boundary_utc_offset_minutes=int(
            row["day_boundary_utc_offset_minutes"]
        ),
        source_fingerprint=str(row["source_fingerprint"]),
        supersedes_bundle_id=(
            int(row["supersedes_bundle_id"])
            if row["supersedes_bundle_id"] is not None else None
        ),
        is_current=bool(row["is_current"]),
        created_at_utc=_datetime(row["created_at_utc"]),
        updated_at_utc=_datetime(row["updated_at_utc"]),
    )


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")
