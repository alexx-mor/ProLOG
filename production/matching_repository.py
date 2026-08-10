"""SQLite persistence and read-only directory snapshots for P10 matching."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from production.matching_models import (
    MATCHER_RULE_VERSION,
    BundleMatchingInput,
    MatchAnalysis,
    MatchCandidate,
    MatchingAlias,
    MatchingContext,
    MatchingObject,
    MatchingProduct,
    MatchingResult,
    MatchingStage,
    MatchQuality,
    MatchRunStatus,
    PersistedProposal,
    ProductionInboxMatchRun,
    ProposalDraft,
    ProposalEvidence,
    ProposalIssue,
)
from production.review_repository import REVIEW_EFFECTIVE_BUNDLES_SQL


class ProductionInboxMatchingRepository:
    def __init__(self, database) -> None:
        self.database = database

    def current_bundle_ids(self) -> tuple[int, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT id FROM ({REVIEW_EFFECTIVE_BUNDLES_SQL}) ORDER BY id"
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def load_bundle_input(self, bundle_id: int) -> BundleMatchingInput | None:
        with self.database.connect() as connection:
            bundle = connection.execute(
                "SELECT * FROM ProductionInboxBundles WHERE id = ?", (bundle_id,)
            ).fetchone()
            if bundle is None:
                return None
            rows = connection.execute(
                """
                SELECT relation.bundle_order, relation.message_role,
                       COALESCE(message.source_text, '') AS source_text
                FROM ProductionInboxBundleMessages relation
                JOIN ProductionInboxMessages message
                  ON message.id = relation.inbox_message_id
                WHERE relation.bundle_id = ?
                ORDER BY CASE relation.message_role
                    WHEN 'closing_text' THEN 0
                    WHEN 'captioned_media' THEN 1
                    WHEN 'text_only' THEN 2
                    ELSE 3 END,
                    relation.bundle_order
                """,
                (bundle_id,),
            ).fetchall()
            media_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM ProductionInboxBundleMessages relation
                    JOIN ProductionInboxAttachments attachment
                      ON attachment.inbox_message_id = relation.inbox_message_id
                    WHERE relation.bundle_id = ?
                    """,
                    (bundle_id,),
                ).fetchone()[0]
            )
        source_text = "\n".join(
            str(row["source_text"]).strip()
            for row in rows
            if str(row["source_text"]).strip()
        )
        return BundleMatchingInput(
            bundle_id=int(bundle["id"]),
            bundle_fingerprint=str(bundle["source_fingerprint"]),
            grouping_status=str(bundle["grouping_status"]),
            source_text=source_text,
            has_media=media_count > 0,
        )

    def load_context(self) -> MatchingContext:
        with self.database.connect() as connection:
            objects = connection.execute(
                """
                SELECT id, name, project_number, contract_number, is_active
                FROM objects_db.Objects ORDER BY id
                """
            ).fetchall()
            products = connection.execute(
                """
                SELECT id, object_id, serial_number, name, code, is_active
                FROM products_db.Products ORDER BY id
                """
            ).fetchall()
            stages = connection.execute(
                "SELECT id, code, name, is_active FROM ProductionStages ORDER BY id"
            ).fetchall()
            object_aliases = connection.execute(
                """
                SELECT object_id AS target_id, original_alias, alias_normalized
                FROM aliases_db.ObjectAliases ORDER BY alias_normalized, object_id
                """
            ).fetchall()
            product_aliases = connection.execute(
                """
                SELECT product_id AS target_id, original_alias, alias_normalized
                FROM aliases_db.ProductAliases ORDER BY alias_normalized, product_id
                """
            ).fetchall()
            stage_aliases = connection.execute(
                """
                SELECT stage_id AS target_id, alias_text, normalized_alias, is_active
                FROM ProductionStageAliases ORDER BY normalized_alias, stage_id
                """
            ).fetchall()
        return MatchingContext(
            objects=tuple(
                MatchingObject(
                    int(row["id"]), str(row["name"]),
                    str(row["project_number"] or ""),
                    str(row["contract_number"] or ""), bool(row["is_active"]),
                )
                for row in objects
            ),
            products=tuple(
                MatchingProduct(
                    int(row["id"]), int(row["object_id"]),
                    str(row["serial_number"] or ""), str(row["name"]),
                    str(row["code"] or ""), bool(row["is_active"]),
                )
                for row in products
            ),
            stages=tuple(
                MatchingStage(
                    int(row["id"]), str(row["code"]), str(row["name"]),
                    bool(row["is_active"]),
                )
                for row in stages
            ),
            object_aliases=tuple(
                MatchingAlias(
                    int(row["target_id"]), str(row["original_alias"]),
                    str(row["alias_normalized"]), True,
                )
                for row in object_aliases
            ),
            product_aliases=tuple(
                MatchingAlias(
                    int(row["target_id"]), str(row["original_alias"]),
                    str(row["alias_normalized"]), True,
                )
                for row in product_aliases
            ),
            stage_aliases=tuple(
                MatchingAlias(
                    int(row["target_id"]), str(row["alias_text"]),
                    str(row["normalized_alias"]), bool(row["is_active"]),
                )
                for row in stage_aliases
            ),
        )

    def save_analysis(self, analysis: MatchAnalysis) -> MatchingResult:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            current = connection.execute(
                """
                SELECT * FROM ProductionInboxMatchRuns
                WHERE bundle_id = ? AND is_current = 1
                """,
                (analysis.bundle.bundle_id,),
            ).fetchone()
            if current is not None and _same_run(current, analysis):
                run_id = int(current["id"])
                created = False
            else:
                predecessor_id = int(current["id"]) if current is not None else None
                if predecessor_id is not None:
                    connection.execute(
                        """
                        UPDATE ProductionInboxMatchRuns
                        SET is_current = 0, superseded_at_utc = ?,
                            superseded_reason = 'matching_context_changed'
                        WHERE id = ?
                        """,
                        (now, predecessor_id),
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO ProductionInboxMatchRuns (
                        uid, bundle_id, bundle_fingerprint, matcher_rule_version,
                        directory_context_fingerprint, input_text_hash,
                        result_fingerprint, source_text, normalized_text,
                        has_media, status, is_current, supersedes_match_run_id,
                        created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        str(uuid4()), analysis.bundle.bundle_id,
                        analysis.bundle.bundle_fingerprint,
                        MATCHER_RULE_VERSION, analysis.context_fingerprint,
                        analysis.input_text_hash, analysis.result_fingerprint,
                        analysis.bundle.source_text, analysis.normalized_text,
                        int(analysis.bundle.has_media), analysis.status.value,
                        predecessor_id, now,
                    ),
                )
                run_id = int(cursor.lastrowid)
                self._insert_proposals(connection, run_id, analysis.proposals, now)
                created = True
        result = self.get_result(run_id)
        if result is None:
            raise RuntimeError("Сохраненный MatchRun не найден")
        return MatchingResult(result.run, result.proposals, created)

    def deactivate_runs_for_noncurrent_bundles(self) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ProductionInboxMatchRuns
                SET is_current = 0, superseded_at_utc = ?,
                    superseded_reason = 'source_bundle_changed'
                WHERE is_current = 1 AND bundle_id NOT IN (
                    SELECT id FROM (""" + REVIEW_EFFECTIVE_BUNDLES_SQL + """)
                )
                """,
                (now,),
            )
        return int(cursor.rowcount)

    def current_runs(self) -> list[ProductionInboxMatchRun]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ProductionInboxMatchRuns WHERE is_current = 1 ORDER BY id"
            ).fetchall()
        return [_run(row) for row in rows]

    def get_result(self, run_id: int) -> MatchingResult | None:
        with self.database.connect() as connection:
            run_row = connection.execute(
                "SELECT * FROM ProductionInboxMatchRuns WHERE id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                return None
            proposal_rows = connection.execute(
                "SELECT * FROM ProductionInboxProposals WHERE match_run_id = ? ORDER BY proposal_order",
                (run_id,),
            ).fetchall()
            proposals = tuple(
                PersistedProposal(
                    int(row["id"]), run_id,
                    self._load_draft(connection, row),
                )
                for row in proposal_rows
            )
        return MatchingResult(_run(run_row), proposals, False)

    def current_result_for_bundle(self, bundle_id: int) -> MatchingResult | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM ProductionInboxMatchRuns WHERE bundle_id = ? AND is_current = 1",
                (bundle_id,),
            ).fetchone()
        return self.get_result(int(row[0])) if row else None

    def diagnostics_rows(self) -> dict[str, object]:
        with self.database.connect() as connection:
            return {
                "current_bundles": connection.execute(
                    f"""
                    SELECT id, source_fingerprint FROM ProductionInboxBundles
                    WHERE id IN ({REVIEW_EFFECTIVE_BUNDLES_SQL})
                    """
                ).fetchall(),
                "current_runs": connection.execute(
                    """
                    SELECT run.*, bundle.source_fingerprint AS actual_bundle_fingerprint,
                           bundle.is_current AS bundle_is_current
                    FROM ProductionInboxMatchRuns run
                    JOIN ProductionInboxBundles bundle ON bundle.id = run.bundle_id
                    WHERE run.is_current = 1
                    """
                ).fetchall(),
                "proposals": connection.execute(
                    "SELECT * FROM ProductionInboxProposals"
                ).fetchall(),
                "candidate_ranks": connection.execute(
                    """
                    SELECT 'product' kind, proposal_id, rank FROM ProductionInboxProductCandidates
                    UNION ALL SELECT 'object', proposal_id, rank FROM ProductionInboxObjectCandidates
                    UNION ALL SELECT 'stage', proposal_id, rank FROM ProductionInboxStageCandidates
                    ORDER BY kind, proposal_id, rank
                    """
                ).fetchall(),
                "lineage": connection.execute(
                    """
                    SELECT child.id, child.supersedes_match_run_id, parent.id AS parent_id
                    FROM ProductionInboxMatchRuns child
                    LEFT JOIN ProductionInboxMatchRuns parent
                      ON parent.id = child.supersedes_match_run_id
                    WHERE child.supersedes_match_run_id IS NOT NULL
                    """
                ).fetchall(),
            }

    @staticmethod
    def _insert_proposals(connection, run_id, proposals, now) -> None:
        for proposal in proposals:
            cursor = connection.execute(
                """
                INSERT INTO ProductionInboxProposals (
                    uid, match_run_id, proposal_order, source_segment_text,
                    normalized_segment_text, source_segment_start,
                    source_segment_end, object_id, object_match_method,
                    product_id, product_match_method, stage_id,
                    stage_match_method, readiness_percent,
                    readiness_match_method, description_text,
                    normalized_description, match_quality, requires_review,
                    issue_code, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()), run_id, proposal.order,
                    proposal.source_segment_text,
                    proposal.normalized_segment_text,
                    proposal.source_segment_start, proposal.source_segment_end,
                    proposal.object_id, proposal.object_match_method,
                    proposal.product_id, proposal.product_match_method,
                    proposal.stage_id, proposal.stage_match_method,
                    proposal.readiness_percent, proposal.readiness_match_method,
                    proposal.description_text, proposal.normalized_description,
                    proposal.match_quality.value, int(proposal.requires_review),
                    proposal.issue_code, now,
                ),
            )
            proposal_id = int(cursor.lastrowid)
            _insert_candidates(connection, "Product", proposal_id, proposal.product_candidates)
            _insert_candidates(connection, "Object", proposal_id, proposal.object_candidates)
            _insert_candidates(connection, "Stage", proposal_id, proposal.stage_candidates)
            for order, evidence in enumerate(proposal.evidence):
                connection.execute(
                    """
                    INSERT INTO ProductionInboxProposalEvidence (
                        proposal_id, field_name, evidence_order, match_method,
                        matched_text, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id, evidence.field_name, order, evidence.method,
                        evidence.matched_text, evidence.explanation,
                    ),
                )
            for order, issue in enumerate(proposal.issues):
                connection.execute(
                    """
                    INSERT INTO ProductionInboxProposalIssues (
                        proposal_id, issue_order, issue_code, message, evidence_text
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (proposal_id, order, issue.code, issue.message, issue.evidence_text),
                )

    def _load_draft(self, connection, row) -> ProposalDraft:
        proposal_id = int(row["id"])
        return ProposalDraft(
            order=int(row["proposal_order"]),
            source_segment_text=str(row["source_segment_text"]),
            normalized_segment_text=str(row["normalized_segment_text"]),
            source_segment_start=row["source_segment_start"],
            source_segment_end=row["source_segment_end"],
            object_id=row["object_id"],
            object_match_method=row["object_match_method"],
            product_id=row["product_id"],
            product_match_method=row["product_match_method"],
            stage_id=row["stage_id"],
            stage_match_method=row["stage_match_method"],
            readiness_percent=row["readiness_percent"],
            readiness_match_method=row["readiness_match_method"],
            description_text=str(row["description_text"]),
            normalized_description=str(row["normalized_description"]),
            match_quality=MatchQuality(str(row["match_quality"])),
            requires_review=bool(row["requires_review"]),
            product_candidates=_load_candidates(connection, "Product", proposal_id),
            object_candidates=_load_candidates(connection, "Object", proposal_id),
            stage_candidates=_load_candidates(connection, "Stage", proposal_id),
            evidence=tuple(
                ProposalEvidence(
                    str(item["field_name"]), str(item["match_method"]),
                    str(item["matched_text"]), str(item["explanation"]),
                )
                for item in connection.execute(
                    "SELECT * FROM ProductionInboxProposalEvidence WHERE proposal_id = ? ORDER BY field_name, evidence_order",
                    (proposal_id,),
                )
            ),
            issues=tuple(
                ProposalIssue(
                    str(item["issue_code"]), str(item["message"]),
                    str(item["evidence_text"]),
                )
                for item in connection.execute(
                    "SELECT * FROM ProductionInboxProposalIssues WHERE proposal_id = ? ORDER BY issue_order",
                    (proposal_id,),
                )
            ),
        )


def _insert_candidates(connection, kind: str, proposal_id: int, candidates) -> None:
    table = f"ProductionInbox{kind}Candidates"
    target = kind.casefold() + "_id"
    for candidate in candidates:
        columns = (
            f"proposal_id, {target}, rank, deterministic_score, match_method, "
            "matched_text, evidence, is_active_snapshot"
        )
        values: tuple[object, ...] = (
            proposal_id, candidate.target_id, candidate.rank, candidate.score,
            candidate.method, candidate.matched_text, candidate.evidence,
            int(candidate.is_active),
        )
        if kind == "Product":
            columns += ", object_id_snapshot"
            values += (candidate.object_id,)
        placeholders = ", ".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values
        )


def _load_candidates(connection, kind: str, proposal_id: int) -> tuple[MatchCandidate, ...]:
    table = f"ProductionInbox{kind}Candidates"
    target = kind.casefold() + "_id"
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE proposal_id = ? ORDER BY rank", (proposal_id,)
    ).fetchall()
    return tuple(
        MatchCandidate(
            target_id=int(row[target]), rank=int(row["rank"]),
            score=int(row["deterministic_score"]), method=str(row["match_method"]),
            matched_text=str(row["matched_text"]), evidence=str(row["evidence"]),
            is_active=bool(row["is_active_snapshot"]),
            object_id=(
                int(row["object_id_snapshot"])
                if kind == "Product" else None
            ),
        )
        for row in rows
    )


def _same_run(row, analysis: MatchAnalysis) -> bool:
    return (
        str(row["bundle_fingerprint"]) == analysis.bundle.bundle_fingerprint
        and str(row["matcher_rule_version"]) == MATCHER_RULE_VERSION
        and str(row["directory_context_fingerprint"])
        == analysis.context_fingerprint
        and str(row["input_text_hash"]) == analysis.input_text_hash
        and str(row["result_fingerprint"]) == analysis.result_fingerprint
    )


def _run(row) -> ProductionInboxMatchRun:
    return ProductionInboxMatchRun(
        id=int(row["id"]), uid=UUID(str(row["uid"])),
        bundle_id=int(row["bundle_id"]),
        bundle_fingerprint=str(row["bundle_fingerprint"]),
        matcher_rule_version=str(row["matcher_rule_version"]),
        directory_context_fingerprint=str(row["directory_context_fingerprint"]),
        input_text_hash=str(row["input_text_hash"]),
        result_fingerprint=str(row["result_fingerprint"]),
        source_text=str(row["source_text"]),
        normalized_text=str(row["normalized_text"]),
        has_media=bool(row["has_media"]),
        status=MatchRunStatus(str(row["status"])),
        is_current=bool(row["is_current"]),
        supersedes_match_run_id=(
            int(row["supersedes_match_run_id"])
            if row["supersedes_match_run_id"] is not None else None
        ),
        created_at_utc=datetime.fromisoformat(str(row["created_at_utc"])).astimezone(
            timezone.utc
        ),
    )
