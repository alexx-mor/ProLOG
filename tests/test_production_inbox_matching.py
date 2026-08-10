"""P10 deterministic and explainable Production Inbox matching tests."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from database import Database, DirectoryRepository
from matching_text import normalize_match_text
from models import AliasItem, ProductItem
from production.matching_models import MatchQuality, MatchRunStatus
from production.matching_repository import ProductionInboxMatchingRepository
from production.matching_service import ProductionInboxMatchingService


class Context:
    def __init__(self, tmp_path: Path) -> None:
        self.database = Database(tmp_path / "prolog.sqlite3")
        self.database.initialize()
        self.directories = DirectoryRepository(self.database)
        self.repository = ProductionInboxMatchingRepository(self.database)
        self.matcher = ProductionInboxMatchingService(self.repository)
        self.object_id = self.directories.upsert("objects", "Объект Альфа")
        self.product_id = self.directories.save_product(
            ProductItem(
                object_id=self.object_id,
                name="ШУ1",
                serial_number="3076",
                code="CODE-SHU1",
            )
        )
        self.counter = 0

    def bundle(self, text: str, *, media: bool = False, status: str | None = None) -> int:
        self.counter += 1
        return _create_bundle(
            self.database, f"message-{self.counter}", text,
            media=media, status=status,
        )


@pytest.fixture
def context(tmp_path: Path) -> Context:
    return Context(tmp_path)


def test_v6_to_v7_migration_is_additive_idempotent_and_preserves_p9(tmp_path: Path) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_to_v6(database)
    with database.connect() as connection:
        connection.execute("INSERT INTO Settings(key, value) VALUES ('p10-legacy', 'keep')")
    component_hashes = _component_hashes(database)

    database.initialize()
    history = _migration_history(database)
    database.initialize()

    assert _migration_history(database) == history
    assert _component_hashes(database) == component_hashes
    assert {item.component: item.current_version for item in database.schema_versions()} == {
        "prolog": 7, "employees": 1, "objects": 1, "products": 1, "aliases": 1,
    }
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM Settings WHERE key = 'p10-legacy'"
        ).fetchone()[0] == "keep"


def test_v7_migration_rolls_back_on_failure(tmp_path: Path, monkeypatch) -> None:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    _downgrade_to_v6(database)

    def fail(connection):
        connection.execute("CREATE TABLE P10ShouldRollback(id INTEGER)")
        raise RuntimeError("p10 migration failure")

    monkeypatch.setattr("database.apply_production_inbox_matching_migration", fail)
    with pytest.raises(RuntimeError, match="p10 migration failure"):
        database.initialize()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'P10ShouldRollback'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT MAX(version) FROM SchemaMigrations WHERE component = 'prolog'"
        ).fetchone()[0] == 6


def test_stage_alias_seed_is_conservative_and_idempotent(context: Context) -> None:
    with context.database.connect() as connection:
        before = tuple(connection.execute(
            "SELECT normalized_alias FROM ProductionStageAliases ORDER BY normalized_alias"
        ))
        assert "сборка" not in {row[0] for row in before}
        assert "готово" not in {row[0] for row in before}
    context.database.initialize()
    with context.database.connect() as connection:
        after = tuple(connection.execute(
            "SELECT normalized_alias FROM ProductionStageAliases ORDER BY normalized_alias"
        ))
    assert before == after


@pytest.mark.parametrize("text", ["ШУ1 электромонтаж 70%", "ШУ 1 электромонтаж 70 %", "ШУ-1 электромонтаж 70%"])
def test_short_product_variants_stage_and_readiness(context: Context, text: str) -> None:
    proposal = _proposal(context, text)

    assert proposal.product_id == context.product_id
    assert proposal.object_id == context.object_id
    assert proposal.object_match_method == "derived_from_product"
    assert proposal.stage_match_method == "exact_stage_name"
    assert proposal.readiness_percent == 70


def test_exact_serial_has_priority_and_token_boundary(context: Context) -> None:
    exact = _proposal(context, "шкаф заводской номер 3076, подготовка")
    boundary = _proposal(context, "номер 30765, подготовка")

    assert exact.product_id == context.product_id
    assert exact.product_match_method == "exact_serial"
    assert boundary.product_id is None


def test_duplicate_exact_serial_is_reported_as_identifier_ambiguity(
    context: Context,
) -> None:
    other_object = context.directories.upsert("objects", "Объект Бета")
    context.directories.save_product(
        ProductItem(object_id=other_object, name="ШУ9", serial_number="3076")
    )

    proposal = _proposal(context, "заводской номер 3076")

    assert proposal.product_id is None
    assert proposal.issue_code == "exact_identifier_ambiguous"
    assert len(proposal.product_candidates) == 2


def test_exact_code(context: Context) -> None:
    proposal = _proposal(context, "CODE-SHU1 маркировка")
    assert proposal.product_id == context.product_id
    assert proposal.product_match_method == "exact_code"


def test_confirmed_product_alias(context: Context) -> None:
    context.directories.save_alias(AliasItem("product", "главный шкаф", context.product_id))
    proposal = _proposal(context, "главный шкаф упаковка")
    assert proposal.product_id == context.product_id
    assert proposal.product_match_method == "confirmed_alias"


def test_same_short_name_on_different_objects_is_ambiguous(context: Context) -> None:
    other_object = context.directories.upsert("objects", "Объект Бета")
    context.directories.save_product(ProductItem(object_id=other_object, name="ШУ1"))
    proposal = _proposal(context, "ШУ1 70%")

    assert proposal.product_id is None
    assert len(proposal.product_candidates) == 2
    assert proposal.match_quality is MatchQuality.AMBIGUOUS
    assert proposal.issue_code == "split_ambiguous"


def test_shorter_name_inside_longer_product_reference_is_not_a_candidate(
    context: Context,
) -> None:
    context.directories.save_product(ProductItem(object_id=context.object_id, name="ШУ"))

    proposal = _proposal(context, "ШУ 1 электромонтаж 70%")

    assert proposal.product_id == context.product_id
    assert [item.target_id for item in proposal.product_candidates] == [context.product_id]


def test_separate_short_and_long_product_mentions_remain_candidates(
    context: Context,
) -> None:
    short_id = context.directories.save_product(
        ProductItem(object_id=context.object_id, name="ШУ")
    )

    proposal = _proposal(context, "ШУ и ШУ1")

    assert proposal.product_id is None
    assert {item.target_id for item in proposal.product_candidates} == {
        short_id, context.product_id,
    }


def test_object_scoped_name_resolves_duplicate_short_product(context: Context) -> None:
    other_object = context.directories.upsert("objects", "Объект Бета")
    context.directories.save_product(ProductItem(object_id=other_object, name="ШУ1"))
    proposal = _proposal(context, "Объект Альфа ШУ 1 электромонтаж")

    assert proposal.product_id == context.product_id
    assert proposal.product_match_method == "object_scoped_name"


def test_product_object_conflict_is_not_hidden(context: Context) -> None:
    other_object = context.directories.upsert("objects", "Объект Бета")
    proposal = _proposal(context, "Объект Бета 3076 электромонтаж")

    assert proposal.product_id == context.product_id
    assert proposal.object_id == context.object_id
    assert any(issue.code == "object_conflict" for issue in proposal.issues)
    assert any(candidate.target_id == other_object for candidate in proposal.object_candidates)


def test_inactive_product_is_returned_with_warning(context: Context) -> None:
    context.directories.set_product_active(context.product_id, False)
    proposal = _proposal(context, "3076 электромонтаж")
    assert proposal.product_id == context.product_id
    assert any(issue.code == "inactive_product_candidate" for issue in proposal.issues)


@pytest.mark.parametrize(
    ("text", "expected_code", "method"),
    [
        ("ШУ1 слесарка", "METALWORK", "confirmed_stage_alias"),
        ("ШУ1 ОТК", "QUALITY_CONTROL", "exact_stage_name"),
        ("ШУ1 PACKAGING", "PACKAGING", "exact_stage_code"),
    ],
)
def test_stage_matching(context: Context, text: str, expected_code: str, method: str) -> None:
    proposal = _proposal(context, text)
    with context.database.connect() as connection:
        stage_id = connection.execute(
            "SELECT id FROM ProductionStages WHERE code = ?", (expected_code,)
        ).fetchone()[0]
    assert proposal.stage_id == stage_id
    assert proposal.stage_match_method == method


def test_inactive_stage_is_candidate_not_selected(context: Context) -> None:
    with context.database.connect() as connection:
        stage_id = connection.execute(
            "SELECT id FROM ProductionStages WHERE code = 'METALWORK'"
        ).fetchone()[0]
        connection.execute("UPDATE ProductionStages SET is_active = 0 WHERE id = ?", (stage_id,))
    proposal = _proposal(context, "ШУ1 слесарка")
    assert proposal.stage_id is None
    assert proposal.stage_candidates[0].target_id == stage_id
    assert any(issue.code == "inactive_stage_candidate" for issue in proposal.issues)


def test_assembly_never_auto_matches_stage(context: Context) -> None:
    proposal = _proposal(context, "ШУ1 сборка 40%")
    assert proposal.stage_id is None
    assert proposal.readiness_percent == 40


@pytest.mark.parametrize(
    ("text", "expected"),
    [("ШУ1 70%", 70), ("ШУ1 70 %", 70), ("ШУ1 готовность 70", 70), ("ШУ1 0%", 0), ("ШУ1 100%", 100)],
)
def test_readiness_forms(context: Context, text: str, expected: int) -> None:
    assert _proposal(context, text).readiness_percent == expected


def test_bare_serial_is_not_readiness(context: Context) -> None:
    proposal = _proposal(context, "3076 электромонтаж")
    assert proposal.readiness_percent is None


@pytest.mark.parametrize("text", ["ШУ1 101%", "ШУ1 60%, было 50%", "ШУ1 70-80%"])
def test_invalid_or_ambiguous_readiness(context: Context, text: str) -> None:
    proposal = _proposal(context, text)
    assert proposal.readiness_percent is None
    assert proposal.issue_code in {"invalid_readiness", "readiness_ambiguous"} or any(
        issue.code in {"invalid_readiness", "readiness_ambiguous"}
        for issue in proposal.issues
    )


def test_text_only_is_matchable_and_records_no_media(context: Context) -> None:
    result = context.matcher.match_bundle(context.bundle("ШУ1 электромонтаж 70%"))
    assert not result.run.has_media
    assert result.proposals[0].draft.product_id == context.product_id


def test_photo_caption_records_media(context: Context) -> None:
    result = context.matcher.match_bundle(context.bundle("ШУ1 40%", media=True))
    assert result.run.has_media
    assert result.proposals[0].draft.readiness_percent == 40


def test_photo_only_needs_description_without_vision(context: Context) -> None:
    result = context.matcher.match_bundle(
        context.bundle("", media=True, status="needs_description")
    )
    proposal = result.proposals[0].draft
    assert result.run.status is MatchRunStatus.NO_TEXT
    assert proposal.product_id is None and proposal.stage_id is None
    assert proposal.readiness_percent is None
    assert proposal.issue_code == "missing_description"


def test_multiple_products_split_only_on_explicit_segments(context: Context) -> None:
    second = context.directories.save_product(
        ProductItem(object_id=context.object_id, name="ШУ2")
    )
    split = context.matcher.match_bundle(
        context.bundle("ШУ1 50%; ШУ2 70%")
    ).proposals
    ambiguous = _proposal(context, "ШУ1, ШУ2 70%")

    assert [item.draft.product_id for item in split] == [context.product_id, second]
    assert [item.draft.readiness_percent for item in split] == [50, 70]
    assert ambiguous.product_id is None
    assert ambiguous.issue_code == "split_ambiguous"


def test_stage_and_readiness_can_match_without_product(context: Context) -> None:
    proposal = _proposal(context, "электромонтаж 70%")
    assert proposal.product_id is None
    assert proposal.stage_id is not None
    assert proposal.readiness_percent == 70


def test_same_context_rerun_is_idempotent(context: Context) -> None:
    bundle_id = context.bundle("ШУ1 электромонтаж 70%")
    first = context.matcher.match_bundle(bundle_id)
    second = context.matcher.match_bundle(bundle_id)
    assert first.created
    assert not second.created
    assert first.run.id == second.run.id


def test_alias_change_creates_new_match_run_and_preserves_history(context: Context) -> None:
    bundle_id = context.bundle("главный шкаф электромонтаж 70%")
    first = context.matcher.match_bundle(bundle_id)
    context.directories.save_alias(AliasItem("product", "главный шкаф", context.product_id))
    second = context.matcher.match_bundle(bundle_id)

    assert second.created and second.run.id != first.run.id
    assert second.run.supersedes_match_run_id == first.run.id
    assert second.proposals[0].draft.product_id == context.product_id
    with context.database.connect() as connection:
        assert connection.execute(
            "SELECT is_current FROM ProductionInboxMatchRuns WHERE id = ?", (first.run.id,)
        ).fetchone()[0] == 0


def test_new_current_p9_bundle_deactivates_old_match_run(context: Context) -> None:
    old_bundle = context.bundle("ШУ1 40%")
    old_run = context.matcher.match_bundle(old_bundle).run
    with context.database.connect() as connection:
        connection.execute(
            "UPDATE ProductionInboxBundles SET is_current = 0, superseded_at_utc = '2026-08-10T12:00:00+00:00' WHERE id = ?",
            (old_bundle,),
        )
    new_bundle = context.bundle("ШУ1 50%")
    context.matcher.match_all_current()
    with context.database.connect() as connection:
        assert connection.execute(
            "SELECT is_current FROM ProductionInboxMatchRuns WHERE id = ?", (old_run.id,)
        ).fetchone()[0] == 0
    assert context.repository.current_result_for_bundle(new_bundle) is not None


def test_matching_diagnostics_are_clean_after_current_match(context: Context) -> None:
    context.matcher.match_bundle(context.bundle("ШУ1 электромонтаж 70%"))
    assert context.matcher.diagnostics().is_healthy


def test_p10_does_not_touch_primary_facts_or_import_forbidden_layers(context: Context) -> None:
    with context.database.connect() as connection:
        before = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
            "ProductionEvents", "Attachments", "WorkLogEntries", "WorkBotImportRows",
        ))
    context.matcher.match_bundle(context.bundle("ШУ1 электромонтаж 70%", media=True))
    with context.database.connect() as connection:
        after = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
            "ProductionEvents", "Attachments", "WorkLogEntries", "WorkBotImportRows",
        ))
    assert before == after
    root = Path(__file__).parents[1]
    files = [root / "production" / "matching_models.py", root / "production" / "matching_repository.py", root / "production" / "matching_service.py"]
    combined = "\n".join(path.read_text("utf-8") for path in files)
    for forbidden in ("ProductionEvent", "AttachmentService", "WorkBotImportRows", "PySide6", "openai"):
        assert forbidden not in combined
    imports = {
        node.module.split(".")[0]
        for path in files
        for node in ast.walk(ast.parse(path.read_text("utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not {"PySide6", "workbot"} & imports


def test_normalization_keeps_source_separate_and_unifies_variants() -> None:
    source = "  ШУ— 1   ГОТОВНОСТЬ: 70 %  "
    assert normalize_match_text(source) == "шу-1 готовность: 70%"
    assert source.startswith("  ")


def _proposal(context: Context, text: str):
    return context.matcher.match_bundle(context.bundle(text)).proposals[0].draft


def _create_bundle(database: Database, message_id: str, text: str, *, media: bool, status: str | None) -> int:
    digest = hashlib.sha256(f"{message_id}:{text}".encode()).hexdigest()
    bundle_digest = hashlib.sha256(f"bundle:{message_id}:{text}:{media}".encode()).hexdigest()
    now = "2026-08-10T12:00:00+00:00"
    with database.connect() as connection:
        source = connection.execute("SELECT id FROM ProductionInboxSources LIMIT 1").fetchone()
        if source is None:
            source_id = int(connection.execute(
                """
                INSERT INTO ProductionInboxSources (
                    uid, source_type, source_ref, display_name, chat_id,
                    enabled, web_url, created_at_utc, updated_at_utc
                ) VALUES ('00000000-0000-4000-8000-000000000001', 'max_chat',
                          'test-production', 'Test production', -77703766302910,
                          1, '', ?, ?)
                """, (now, now)
            ).lastrowid)
        else:
            source_id = int(source[0])
        revision_id = int(connection.execute(
            "SELECT COALESCE(MAX(source_revision_id), 0) + 1 FROM ProductionInboxMessages"
        ).fetchone()[0])
        inbox_message_id = int(connection.execute(
            """
            INSERT INTO ProductionInboxMessages (
                uid, source_id, source_type, source_ref, source_message_id,
                source_revision_id, source_revision_number, chat_id,
                sender_max_user_id, sender_display_snapshot,
                message_timestamp_utc, source_received_at_utc,
                transported_at_utc, source_sequence, source_text, content_hash,
                source_content_json, raw_envelope_json, change_kind
            ) VALUES (?, ?, 'max_chat', 'test-production', ?, ?, 1,
                      -77703766302910, 101, 'Test Master', ?, ?, ?, ?, ?, ?, '{}', '{}', 'original')
            """,
            (
                f"00000000-0000-4000-8000-{revision_id:012d}", source_id,
                message_id, revision_id, now, now, now, revision_id, text, digest,
            ),
        ).lastrowid)
        if media:
            connection.execute(
                """
                INSERT INTO ProductionInboxAttachments (
                    uid, inbox_message_id, source_attachment_row_id,
                    source_attachment_id, identity_kind, source_order,
                    attachment_type, mime_type, original_name, source_size,
                    source_download_status, source_sha256, source_storage_key,
                    media_state, source_metadata_json
                ) VALUES (?, ?, ?, ?, 'source_id', 0, 'image', 'image/jpeg',
                          'photo.jpg', 4, 'downloaded', ?, 'ab/cd/test.jpg',
                          'available', '{}')
                """,
                (
                    f"10000000-0000-4000-8000-{revision_id:012d}",
                    inbox_message_id, revision_id, f"photo-{revision_id}",
                    hashlib.sha256(b"test").hexdigest(),
                ),
            )
        bundle_status = status or ("complete" if media else "text_only")
        close_reason = "captioned_media" if media and text else (
            "timeout" if not text else "standalone_text"
        )
        bundle_id = int(connection.execute(
            """
            INSERT INTO ProductionInboxBundles (
                uid, source_id, chat_id, sender_max_user_id,
                sender_display_snapshot, started_at_utc, ended_at_utc,
                grouping_status, close_reason, origin, grouping_rule_version,
                grouping_window_seconds, day_boundary_utc_offset_minutes,
                source_fingerprint, is_current, created_at_utc, updated_at_utc
            ) VALUES (?, ?, -77703766302910, 101, 'Test Master', ?, ?, ?, ?,
                      'deterministic', 'deterministic-v1', 900, 180, ?, 1, ?, ?)
            """,
            (
                f"20000000-0000-4000-8000-{revision_id:012d}", source_id,
                now, now, bundle_status, close_reason, bundle_digest, now, now,
            ),
        ).lastrowid)
        role = "captioned_media" if media and text else ("photo_source" if media else "text_only")
        connection.execute(
            "INSERT INTO ProductionInboxBundleMessages(bundle_id, inbox_message_id, bundle_order, message_role) VALUES (?, ?, 0, ?)",
            (bundle_id, inbox_message_id, role),
        )
    return bundle_id


def _downgrade_to_v6(database: Database) -> None:
    triggers = (
        "trg_production_match_runs_immutable", "trg_production_match_runs_no_delete",
        "trg_production_proposals_immutable_update", "trg_production_proposals_immutable_delete",
        "trg_production_product_candidates_immutable_update", "trg_production_product_candidates_immutable_delete",
        "trg_production_object_candidates_immutable_update", "trg_production_object_candidates_immutable_delete",
        "trg_production_stage_candidates_immutable_update", "trg_production_stage_candidates_immutable_delete",
        "trg_production_evidence_immutable_update", "trg_production_evidence_immutable_delete",
        "trg_production_issues_immutable_update", "trg_production_issues_immutable_delete",
    )
    tables = (
        "ProductionInboxProposalIssues", "ProductionInboxProposalEvidence",
        "ProductionInboxStageCandidates", "ProductionInboxObjectCandidates",
        "ProductionInboxProductCandidates", "ProductionInboxProposals",
        "ProductionInboxMatchRuns", "ProductionStageAliases",
    )
    with database.connect(foreign_keys=False) as connection:
        for trigger in triggers:
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in tables:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute(
            "DELETE FROM SchemaMigrations WHERE component = 'prolog' AND version = 7"
        )


def _component_hashes(database: Database) -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in database.database_paths().items()
        if name != "prolog"
    }


def _migration_history(database: Database):
    with database.connect() as connection:
        return tuple(connection.execute("SELECT * FROM SchemaMigrations ORDER BY version"))
