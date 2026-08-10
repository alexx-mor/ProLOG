"""SQLite persistence for P11 review decisions and manual bundle lineage."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from production.matching_models import MatchCandidate, ProposalEvidence, ProposalIssue
from production.models import ActorRef
from production.review_models import (
    InboxSourceAttachmentView,
    InboxSourceMessageView,
    ManualGroupingOperation,
    ProductionInboxReviewDetail,
    ProductionInboxReviewItem,
    RejectionCode,
    ReviewDecision,
    ReviewResult,
    ReviewStatus,
)


REVIEW_EFFECTIVE_BUNDLES_SQL = """
SELECT bundle.id
FROM ProductionInboxBundles bundle
WHERE bundle.is_current = 1
  AND NOT EXISTS (
      SELECT 1
      FROM ProductionInboxManualBundleSources replacement
      JOIN ProductionInboxBundles manual
        ON manual.id = replacement.manual_bundle_id
       AND manual.is_current = 1
      WHERE replacement.source_bundle_id = bundle.id
  )
"""


class StaleProductionInboxReviewError(RuntimeError):
    """The user is looking at a source or interpretation that is no longer current."""


class ProductionInboxReviewConflictError(RuntimeError):
    """A proposal already has a terminal decision with different semantics."""


class ProductionInboxReviewRepository:
    def __init__(self, database) -> None:
        self.database = database

    def list_items(self) -> list[ProductionInboxReviewItem]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                WITH effective_bundle AS ({REVIEW_EFFECTIVE_BUNDLES_SQL}),
                attachment_counts AS (
                    SELECT relation.bundle_id, COUNT(attachment.id) AS count
                    FROM ProductionInboxBundleMessages relation
                    JOIN ProductionInboxAttachments attachment
                      ON attachment.inbox_message_id = relation.inbox_message_id
                    GROUP BY relation.bundle_id
                )
                SELECT bundle.*, run.id AS run_id, run.matcher_rule_version,
                       run.directory_context_fingerprint, run.source_text,
                       run.has_media, proposal.id AS proposal_id,
                       proposal.proposal_order, proposal.product_id,
                       proposal.object_id, proposal.stage_id,
                       proposal.readiness_percent, proposal.description_text,
                       proposal.match_quality, proposal.requires_review,
                       COALESCE(proposal.issue_code, '') AS issue_code,
                       COALESCE(counts.count, 0) AS attachment_count,
                       review.id AS review_id, review.status AS stored_review_status,
                       review.production_event_id,
                       changed.id AS changed_review_id
                FROM effective_bundle effective
                JOIN ProductionInboxBundles bundle ON bundle.id = effective.id
                JOIN ProductionInboxMatchRuns run
                  ON run.bundle_id = bundle.id AND run.is_current = 1
                JOIN ProductionInboxProposals proposal ON proposal.match_run_id = run.id
                LEFT JOIN attachment_counts counts ON counts.bundle_id = bundle.id
                LEFT JOIN ProductionInboxReviews review
                  ON review.bundle_id = bundle.id
                 AND review.proposal_id = proposal.id
                 AND review.is_current = 1
                LEFT JOIN ProductionInboxReviews changed
                  ON changed.id = (
                      SELECT old_review.id
                      FROM ProductionInboxReviews old_review
                      WHERE old_review.status IN ('confirmed', 'rejected', 'kept_existing')
                        AND old_review.bundle_id = bundle.supersedes_bundle_id
                      ORDER BY old_review.id DESC LIMIT 1
                  )
                ORDER BY bundle.ended_at_utc DESC, bundle.id DESC,
                         proposal.proposal_order
                """
            ).fetchall()
        return [_item(row) for row in rows]

    def get_detail(self, bundle_id: int, proposal_id: int) -> ProductionInboxReviewDetail:
        item = next(
            (
                row for row in self.list_items()
                if row.bundle_id == bundle_id and row.proposal_id == proposal_id
            ),
            None,
        )
        if item is None:
            raise StaleProductionInboxReviewError(
                "Фотоотчет изменился или больше не является текущим"
            )
        with self.database.connect() as connection:
            messages = connection.execute(
                """
                SELECT message.*, relation.bundle_order, relation.message_role
                FROM ProductionInboxBundleMessages relation
                JOIN ProductionInboxMessages message
                  ON message.id = relation.inbox_message_id
                WHERE relation.bundle_id = ?
                ORDER BY relation.bundle_order
                """,
                (bundle_id,),
            ).fetchall()
            attachments = connection.execute(
                """
                SELECT attachment.*, relation.bundle_order,
                       message.source_message_id
                FROM ProductionInboxBundleMessages relation
                JOIN ProductionInboxMessages message
                  ON message.id = relation.inbox_message_id
                JOIN ProductionInboxAttachments attachment
                  ON attachment.inbox_message_id = message.id
                WHERE relation.bundle_id = ?
                ORDER BY relation.bundle_order, attachment.source_order, attachment.id
                """,
                (bundle_id,),
            ).fetchall()
            product_candidates = self._candidates(
                connection, "ProductionInboxProductCandidates", proposal_id, True
            )
            object_candidates = self._candidates(
                connection, "ProductionInboxObjectCandidates", proposal_id, False
            )
            stage_candidates = self._candidates(
                connection, "ProductionInboxStageCandidates", proposal_id, False
            )
            evidence = connection.execute(
                """
                SELECT * FROM ProductionInboxProposalEvidence
                WHERE proposal_id = ? ORDER BY field_name, evidence_order
                """,
                (proposal_id,),
            ).fetchall()
            issues = connection.execute(
                """
                SELECT * FROM ProductionInboxProposalIssues
                WHERE proposal_id = ? ORDER BY issue_order
                """,
                (proposal_id,),
            ).fetchall()
            binding = None
            if item.sender_max_user_id is not None:
                binding = connection.execute(
                    """
                    SELECT employee_id FROM MaxUserBindings
                    WHERE max_user_id = ? AND is_active = 1
                    """,
                    (item.sender_max_user_id,),
                ).fetchone()
            previous_source_text = ""
            if item.source_changed_from_review_id is not None:
                previous = connection.execute(
                    "SELECT source_text_snapshot FROM ProductionInboxReviews WHERE id = ?",
                    (item.source_changed_from_review_id,),
                ).fetchone()
                if previous is not None:
                    previous_source_text = str(previous[0])
        return ProductionInboxReviewDetail(
            item=item,
            messages=tuple(_message(row) for row in messages),
            attachments=tuple(_attachment(row) for row in attachments),
            product_candidates=product_candidates,
            stage_candidates=stage_candidates,
            object_candidates=object_candidates,
            evidence=tuple(
                ProposalEvidence(
                    str(row["field_name"]), str(row["match_method"]),
                    str(row["matched_text"]), str(row["explanation"]),
                )
                for row in evidence
            ),
            issues=tuple(
                ProposalIssue(
                    str(row["issue_code"]), str(row["message"]),
                    str(row["evidence_text"]),
                )
                for row in issues
            ),
            reported_by_employee_id=int(binding[0]) if binding is not None else None,
            previous_source_text=previous_source_text,
        )

    def begin_confirmation(self, decision: ReviewDecision) -> ReviewResult:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            self._validate_current(connection, decision)
            existing = connection.execute(
                """
                SELECT * FROM ProductionInboxReviews
                WHERE bundle_id = ? AND proposal_id = ? AND is_current = 1
                """,
                (decision.bundle_id, decision.proposal_id),
            ).fetchone()
            if existing is not None:
                self._require_same_decision(existing, decision)
                return _review_result(existing)
            review_uid = uuid4()
            cursor = connection.execute(
                """
                INSERT INTO ProductionInboxReviews (
                    uid, bundle_id, bundle_fingerprint, match_run_id, proposal_id,
                    matcher_rule_version, directory_context_fingerprint,
                    decision_kind, status, source_text_snapshot,
                    selected_product_id, selected_stage_id,
                    selected_readiness_percent, final_description,
                    observed_at_utc, reported_by_employee_id, event_type,
                    change_reason, correction_source_event_id,
                    decision_actor_type, decision_actor_uid,
                    decision_actor_local_user_id, decision_actor_external_ref,
                    decision_actor_display_name_snapshot,
                    created_at_utc, updated_at_utc
                )
                SELECT ?, bundle.id, bundle.source_fingerprint, run.id, proposal.id,
                       run.matcher_rule_version, run.directory_context_fingerprint,
                       ?, 'confirming', proposal.source_segment_text,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                FROM ProductionInboxBundles bundle
                JOIN ProductionInboxMatchRuns run ON run.id = ?
                JOIN ProductionInboxProposals proposal ON proposal.id = ?
                WHERE bundle.id = ?
                """,
                (
                    str(review_uid),
                    "correction" if decision.event_type.value == "correction" else "confirm",
                    decision.product_id, decision.stage_id, decision.readiness_percent,
                    decision.description.strip(), _iso(decision.observed_at_utc),
                    decision.reported_by_employee_id, decision.event_type.value,
                    decision.change_reason.strip(), decision.correction_source_event_id,
                    *_actor_values(decision.actor), now, now,
                    decision.match_run_id, decision.proposal_id, decision.bundle_id,
                ),
            )
            review_id = int(cursor.lastrowid)
            self._action(connection, review_id, "started", decision.actor, "")
            attachment_rows = connection.execute(
                """
                SELECT attachment.id, attachment.source_order,
                       message.source_message_id, attachment.source_attachment_id,
                       attachment.source_sha256
                FROM ProductionInboxBundleMessages relation
                JOIN ProductionInboxMessages message
                  ON message.id = relation.inbox_message_id
                JOIN ProductionInboxAttachments attachment
                  ON attachment.inbox_message_id = message.id
                WHERE relation.bundle_id = ?
                ORDER BY relation.bundle_order, attachment.source_order, attachment.id
                """,
                (decision.bundle_id,),
            ).fetchall()
            for order, row in enumerate(attachment_rows):
                connection.execute(
                    """
                    INSERT INTO ProductionInboxReviewAttachmentPromotions (
                        review_id, inbox_attachment_id, source_order,
                        source_message_id, source_attachment_id, source_sha256,
                        status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'intended')
                    """,
                    (
                        review_id, int(row["id"]), order,
                        str(row["source_message_id"]),
                        str(row["source_attachment_id"]),
                        str(row["source_sha256"]),
                    ),
                )
        return ReviewResult(review_id, review_uid, ReviewStatus.CONFIRMING, None)

    def review_row(self, review_id: int):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM ProductionInboxReviews WHERE id = ?", (review_id,)
            ).fetchone()

    def promotion_rows(self, review_id: int):
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT promotion.*, attachment.mime_type, attachment.original_name,
                       attachment.source_storage_key, attachment.media_state,
                       attachment.source_download_status, attachment.source_size
                FROM ProductionInboxReviewAttachmentPromotions promotion
                JOIN ProductionInboxAttachments attachment
                  ON attachment.id = promotion.inbox_attachment_id
                WHERE promotion.review_id = ? ORDER BY promotion.source_order
                """,
                (review_id,),
            ).fetchall()

    def mark_promotion_materialized(
        self, review_id: int, inbox_attachment_id: int, production_attachment_id: int
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ProductionInboxReviewAttachmentPromotions
                SET production_attachment_id = ?, status = 'materialized',
                    error_message = '', materialized_at_utc = ?
                WHERE review_id = ? AND inbox_attachment_id = ?
                """,
                (production_attachment_id, _iso(_utc_now()), review_id, inbox_attachment_id),
            )

    def mark_promotion_failed(
        self, review_id: int, inbox_attachment_id: int, message: str
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ProductionInboxReviewAttachmentPromotions
                SET status = 'failed', error_message = ?
                WHERE review_id = ? AND inbox_attachment_id = ?
                """,
                (message[:1000], review_id, inbox_attachment_id),
            )

    def finish_confirmation(
        self, review_id: int, production_event_id: int, actor: ActorRef, *, recovered: bool = False
    ) -> ReviewResult:
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ProductionInboxReviews WHERE id = ?", (review_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Review decision не найден")
            if str(row["status"]) == "confirmed":
                if int(row["production_event_id"]) != production_event_id:
                    raise ProductionInboxReviewConflictError(
                        "Review уже связан с другим production event"
                    )
                return _review_result(row, recovered=recovered)
            connection.execute(
                """
                UPDATE ProductionInboxReviews
                SET status = 'confirmed', production_event_id = ?,
                    decided_at_utc = ?, updated_at_utc = ?
                WHERE id = ? AND status IN ('confirming', 'failed')
                """,
                (production_event_id, now, now, review_id),
            )
            self._action(
                connection, review_id, "recovered" if recovered else "confirmed", actor, ""
            )
            row = connection.execute(
                "SELECT * FROM ProductionInboxReviews WHERE id = ?", (review_id,)
            ).fetchone()
        return _review_result(row, recovered=recovered)

    def mark_failed(self, review_id: int, actor: ActorRef, message: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE ProductionInboxReviews SET status = 'failed', updated_at_utc = ? WHERE id = ?",
                (_iso(_utc_now()), review_id),
            )
            self._action(connection, review_id, "failed", actor, message[:1000])

    def reject(
        self,
        bundle_id: int,
        bundle_fingerprint: str,
        match_run_id: int,
        proposal_id: int,
        actor: ActorRef,
        code: RejectionCode,
        comment: str = "",
    ) -> ReviewResult:
        decision = self._current_identity(
            bundle_id, bundle_fingerprint, match_run_id, proposal_id, actor
        )
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            self._validate_current(connection, decision)
            existing = connection.execute(
                "SELECT * FROM ProductionInboxReviews WHERE bundle_id = ? AND proposal_id = ? AND is_current = 1",
                (bundle_id, proposal_id),
            ).fetchone()
            if existing is not None:
                if str(existing["status"]) != "rejected":
                    raise ProductionInboxReviewConflictError("Фотоотчет уже получил другое решение")
                return _review_result(existing)
            run = connection.execute(
                "SELECT * FROM ProductionInboxMatchRuns WHERE id = ?", (match_run_id,)
            ).fetchone()
            proposal = connection.execute(
                "SELECT * FROM ProductionInboxProposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            uid = uuid4()
            cursor = connection.execute(
                """
                INSERT INTO ProductionInboxReviews (
                    uid, bundle_id, bundle_fingerprint, match_run_id, proposal_id,
                    matcher_rule_version, directory_context_fingerprint,
                    decision_kind, status, source_text_snapshot, rejection_code,
                    rejection_comment, decision_actor_type, decision_actor_uid,
                    decision_actor_local_user_id, decision_actor_external_ref,
                    decision_actor_display_name_snapshot, created_at_utc,
                    decided_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reject', 'rejected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uid), bundle_id, bundle_fingerprint, match_run_id, proposal_id,
                    str(run["matcher_rule_version"]), str(run["directory_context_fingerprint"]),
                    str(proposal["source_segment_text"]), code.value, comment.strip(),
                    *_actor_values(actor), now, now, now,
                ),
            )
            review_id = int(cursor.lastrowid)
            self._action(connection, review_id, "rejected", actor, comment)
        return ReviewResult(review_id, uid, ReviewStatus.REJECTED, None)

    def source_changed_event_id(self, item: ProductionInboxReviewItem) -> int | None:
        if item.source_changed_from_review_id is None:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT production_event_id FROM ProductionInboxReviews WHERE id = ?",
                (item.source_changed_from_review_id,),
            ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None

    def keep_existing(
        self,
        item: ProductionInboxReviewItem,
        actor: ActorRef,
    ) -> ReviewResult:
        event_id = self.source_changed_event_id(item)
        if event_id is None:
            raise ValueError("Подтвержденное событие исходной версии не найдено")
        now = _iso(_utc_now())
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM ProductionInboxReviews WHERE bundle_id = ? AND proposal_id = ? AND is_current = 1",
                (item.bundle_id, item.proposal_id),
            ).fetchone()
            if existing is not None:
                return _review_result(existing)
            cursor = connection.execute(
                """
                INSERT INTO ProductionInboxReviews (
                    uid, bundle_id, bundle_fingerprint, match_run_id, proposal_id,
                    matcher_rule_version, directory_context_fingerprint,
                    decision_kind, status, source_text_snapshot,
                    production_event_id, decision_actor_type, decision_actor_uid,
                    decision_actor_local_user_id, decision_actor_external_ref,
                    decision_actor_display_name_snapshot, created_at_utc,
                    decided_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'keep_existing', 'kept_existing',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), item.bundle_id, item.bundle_fingerprint,
                    item.match_run_id, item.proposal_id, item.matcher_rule_version,
                    item.directory_context_fingerprint, item.source_text, event_id,
                    *_actor_values(actor), now, now, now,
                ),
            )
            review_id = int(cursor.lastrowid)
            self._action(connection, review_id, "kept_existing", actor, "")
            row = connection.execute(
                "SELECT * FROM ProductionInboxReviews WHERE id = ?", (review_id,)
            ).fetchone()
        return _review_result(row)

    def create_manual_bundles(
        self,
        source_bundle_ids: tuple[int, ...],
        message_groups: tuple[tuple[int, ...], ...],
        operation: ManualGroupingOperation,
        actor: ActorRef,
        *,
        allow_mixed_senders: bool = False,
    ) -> tuple[int, ...]:
        if not source_bundle_ids or not message_groups or any(not group for group in message_groups):
            raise ValueError("Выберите исходные пакеты и сообщения")
        now = _iso(_utc_now())
        created: list[int] = []
        with self.database.connect() as connection:
            bundles = connection.execute(
                f"SELECT * FROM ProductionInboxBundles WHERE id IN ({','.join('?' for _ in source_bundle_ids)}) AND is_current = 1",
                source_bundle_ids,
            ).fetchall()
            if len(bundles) != len(set(source_bundle_ids)):
                raise StaleProductionInboxReviewError("Один из пакетов уже изменился")
            if len({(row["source_id"], row["chat_id"]) for row in bundles}) != 1:
                raise ValueError("Нельзя объединять сообщения разных production source/chat")
            allowed = {
                int(row[0])
                for row in connection.execute(
                    f"SELECT inbox_message_id FROM ProductionInboxBundleMessages WHERE bundle_id IN ({','.join('?' for _ in source_bundle_ids)})",
                    source_bundle_ids,
                )
            }
            flat = [value for group in message_groups for value in group]
            if len(flat) != len(set(flat)) or not set(flat).issubset(allowed):
                raise ValueError("Сообщение отсутствует в выбранных исходных пакетах или повторено")
            for group in message_groups:
                rows = connection.execute(
                    f"SELECT * FROM ProductionInboxMessages WHERE id IN ({','.join('?' for _ in group)}) ORDER BY message_timestamp_utc, COALESCE(source_sequence, 9223372036854775807), source_revision_id, id",
                    group,
                ).fetchall()
                senders = {row["sender_max_user_id"] for row in rows}
                if len(senders) > 1 and not allow_mixed_senders:
                    raise ValueError("Для объединения разных отправителей требуется явное подтверждение")
                source_id, chat_id = int(rows[0]["source_id"]), rows[0]["chat_id"]
                identities = [
                    (int(row["id"]), int(row["source_revision_id"]), str(row["content_hash"]))
                    for row in rows
                ]
                fingerprint = hashlib.sha256(json.dumps(
                    {"origin": "manual", "operation": operation.value, "messages": identities},
                    separators=(",", ":"), sort_keys=True,
                ).encode()).hexdigest()
                has_text = any(str(row["source_text"] or "").strip() for row in rows)
                media_by_message = {
                    int(row["inbox_message_id"]): int(row["count"])
                    for row in connection.execute(
                        f"SELECT inbox_message_id, COUNT(*) count FROM ProductionInboxAttachments WHERE inbox_message_id IN ({','.join('?' for _ in group)}) GROUP BY inbox_message_id",
                        group,
                    )
                }
                has_media = any(media_by_message.get(int(row["id"]), 0) for row in rows)
                status = "complete" if has_text and has_media else "text_only" if has_text else "needs_description"
                cursor = connection.execute(
                    """
                    INSERT INTO ProductionInboxBundles (
                        uid, source_id, chat_id, sender_max_user_id,
                        sender_display_snapshot, started_at_utc, ended_at_utc,
                        grouping_status, close_reason, origin,
                        grouping_rule_version, grouping_window_seconds,
                        day_boundary_utc_offset_minutes, source_fingerprint,
                        is_current, created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual',
                              'manual-v1', 1, 180, ?, 1, ?, ?)
                    """,
                    (
                        str(uuid4()), source_id, chat_id,
                        rows[0]["sender_max_user_id"] if len(senders) == 1 else None,
                        str(rows[0]["sender_display_snapshot"]) if len(senders) == 1 else "Несколько отправителей",
                        str(rows[0]["message_timestamp_utc"]), str(rows[-1]["message_timestamp_utc"]),
                        status, f"manual_{operation.value}", fingerprint, now, now,
                    ),
                )
                manual_id = int(cursor.lastrowid)
                created.append(manual_id)
                for order, row in enumerate(rows):
                    text = str(row["source_text"] or "").strip()
                    media = media_by_message.get(int(row["id"]), 0) > 0
                    role = "captioned_media" if text and media else "text_only" if text else "photo_source" if media else "source_only"
                    connection.execute(
                        "INSERT INTO ProductionInboxBundleMessages(bundle_id, inbox_message_id, bundle_order, message_role) VALUES (?, ?, ?, ?)",
                        (manual_id, int(row["id"]), order, role),
                    )
                for source_bundle_id in source_bundle_ids:
                    connection.execute(
                        """
                        INSERT INTO ProductionInboxManualBundleSources (
                            manual_bundle_id, source_bundle_id, operation,
                            actor_type, actor_uid, actor_local_user_id,
                            actor_external_ref, actor_display_name_snapshot,
                            created_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (manual_id, source_bundle_id, operation.value, *_actor_values(actor), now),
                    )
        return tuple(created)

    def raw_diagnostics(self) -> dict[str, object]:
        with self.database.connect() as connection:
            return {
                "reviews": connection.execute("SELECT * FROM ProductionInboxReviews").fetchall(),
                "promotions": connection.execute("SELECT * FROM ProductionInboxReviewAttachmentPromotions").fetchall(),
                "manual_lineage": connection.execute(
                    """
                    SELECT relation.*, manual.id AS manual_exists, source.id AS source_exists,
                           manual.is_current AS manual_current
                    FROM ProductionInboxManualBundleSources relation
                    LEFT JOIN ProductionInboxBundles manual ON manual.id = relation.manual_bundle_id
                    LEFT JOIN ProductionInboxBundles source ON source.id = relation.source_bundle_id
                    """
                ).fetchall(),
                "events": connection.execute("SELECT id, source_ref FROM ProductionEvents").fetchall(),
            }

    @staticmethod
    def _candidates(connection, table: str, proposal_id: int, product: bool):
        rows = connection.execute(
            f"SELECT * FROM {table} WHERE proposal_id = ? ORDER BY rank", (proposal_id,)
        ).fetchall()
        return tuple(
            MatchCandidate(
                int(row[1]), int(row[2]), int(row[3]), str(row[4]), str(row[5]),
                str(row[6]), bool(row[7]), int(row[8]) if product else None,
            )
            for row in rows
        )

    @staticmethod
    def _validate_current(connection, decision: ReviewDecision) -> None:
        row = connection.execute(
            """
            SELECT bundle.is_current, bundle.source_fingerprint,
                   run.is_current AS run_current, run.bundle_id AS run_bundle_id,
                   proposal.match_run_id AS proposal_run_id
            FROM ProductionInboxBundles bundle
            JOIN ProductionInboxMatchRuns run ON run.id = ?
            JOIN ProductionInboxProposals proposal ON proposal.id = ?
            WHERE bundle.id = ?
            """,
            (decision.match_run_id, decision.proposal_id, decision.bundle_id),
        ).fetchone()
        if (
            row is None or not bool(row["is_current"]) or not bool(row["run_current"])
            or str(row["source_fingerprint"]) != decision.bundle_fingerprint
            or int(row["run_bundle_id"]) != decision.bundle_id
            or int(row["proposal_run_id"]) != decision.match_run_id
        ):
            raise StaleProductionInboxReviewError(
                "Источник изменился. Обновите фотоотчет перед подтверждением."
            )

    @staticmethod
    def _require_same_decision(row, decision: ReviewDecision) -> None:
        comparable = (
            int(row["selected_product_id"]), row["selected_stage_id"],
            row["selected_readiness_percent"], str(row["final_description"]),
            str(row["observed_at_utc"]), row["reported_by_employee_id"],
            str(row["event_type"]), str(row["change_reason"]),
            row["correction_source_event_id"],
        )
        candidate = (
            decision.product_id, decision.stage_id, decision.readiness_percent,
            decision.description.strip(), _iso(decision.observed_at_utc),
            decision.reported_by_employee_id, decision.event_type.value,
            decision.change_reason.strip(), decision.correction_source_event_id,
        )
        if comparable != candidate:
            raise ProductionInboxReviewConflictError(
                "Эта версия фотоотчета уже подтверждается с другими значениями"
            )

    def _current_identity(self, bundle_id, fingerprint, run_id, proposal_id, actor):
        with self.database.connect() as connection:
            proposal = connection.execute(
                "SELECT * FROM ProductionInboxProposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if proposal is None:
            raise StaleProductionInboxReviewError("Proposal больше не найден")
        return ReviewDecision(
            bundle_id, fingerprint, run_id, proposal_id,
            int(proposal["product_id"] or 1), proposal["stage_id"],
            proposal["readiness_percent"], str(proposal["description_text"]),
            _utc_now(), None, actor,
        )

    @staticmethod
    def _action(connection, review_id, action_type, actor, message):
        connection.execute(
            """
            INSERT INTO ProductionInboxReviewActions (
                uid, review_id, action_type, actor_type, actor_uid,
                actor_local_user_id, actor_external_ref,
                actor_display_name_snapshot, message, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), review_id, action_type, *_actor_values(actor), message, _iso(_utc_now())),
        )


def _item(row) -> ProductionInboxReviewItem:
    stored = str(row["stored_review_status"] or "")
    changed_id = int(row["changed_review_id"]) if row["changed_review_id"] else None
    if changed_id is not None:
        status = ReviewStatus.SOURCE_CHANGED
    elif stored:
        status = ReviewStatus(stored)
    else:
        status = ReviewStatus.REQUIRES_REVIEW
    return ProductionInboxReviewItem(
        bundle_id=int(row["id"]), bundle_uid=UUID(str(row["uid"])),
        bundle_fingerprint=str(row["source_fingerprint"]),
        grouping_status=str(row["grouping_status"]), origin=str(row["origin"]),
        source_id=int(row["source_id"]),
        chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
        sender_max_user_id=int(row["sender_max_user_id"]) if row["sender_max_user_id"] is not None else None,
        sender_display_snapshot=str(row["sender_display_snapshot"]),
        observed_at_utc=_datetime(row["ended_at_utc"]), match_run_id=int(row["run_id"]),
        matcher_rule_version=str(row["matcher_rule_version"]),
        directory_context_fingerprint=str(row["directory_context_fingerprint"]),
        proposal_id=int(row["proposal_id"]), proposal_order=int(row["proposal_order"]),
        source_text=str(row["source_text"]),
        product_id=int(row["product_id"]) if row["product_id"] is not None else None,
        object_id=int(row["object_id"]) if row["object_id"] is not None else None,
        stage_id=int(row["stage_id"]) if row["stage_id"] is not None else None,
        readiness_percent=int(row["readiness_percent"]) if row["readiness_percent"] is not None else None,
        description_text=str(row["description_text"]), match_quality=str(row["match_quality"]),
        requires_review=bool(row["requires_review"]), issue_code=str(row["issue_code"]),
        has_media=bool(row["has_media"]), attachment_count=int(row["attachment_count"]),
        review_id=int(row["review_id"]) if row["review_id"] is not None else None,
        review_status=status,
        production_event_id=int(row["production_event_id"]) if row["production_event_id"] is not None else None,
        source_changed_from_review_id=changed_id,
    )


def _message(row) -> InboxSourceMessageView:
    return InboxSourceMessageView(
        int(row["id"]), int(row["bundle_order"]), str(row["message_role"]),
        str(row["source_message_id"]), int(row["source_revision_id"]),
        int(row["source_revision_number"]), str(row["source_text"] or ""),
        _datetime(row["message_timestamp_utc"]),
        int(row["sender_max_user_id"]) if row["sender_max_user_id"] is not None else None,
        str(row["sender_display_snapshot"]),
    )


def _attachment(row) -> InboxSourceAttachmentView:
    return InboxSourceAttachmentView(
        int(row["id"]), int(row["inbox_message_id"]), int(row["bundle_order"]),
        int(row["source_order"]), str(row["source_message_id"]),
        str(row["source_attachment_id"]), str(row["original_name"]),
        str(row["mime_type"]), str(row["source_sha256"]),
        str(row["source_storage_key"]), str(row["media_state"]),
        str(row["source_download_status"]),
    )


def _review_result(row, *, recovered=False) -> ReviewResult:
    return ReviewResult(
        int(row["id"]), UUID(str(row["uid"])), ReviewStatus(str(row["status"])),
        int(row["production_event_id"]) if row["production_event_id"] is not None else None,
        recovered,
    )


def _actor_values(actor: ActorRef) -> tuple[object, ...]:
    return (
        actor.actor_type.value, str(actor.uid), actor.local_user_id,
        "", actor.display_name or "",
    )


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
