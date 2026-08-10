"""P11 orchestration for human review and safe ProductionEvent confirmation."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Protocol

from matching_text import normalize_alias_text
from models import AliasItem
from production.attachment_service import AttachmentService
from production.commands import ConfirmProductionEvent, CorrectProductionEvent, CreateProductionEvent
from production.event_service import ProductionService
from production.matching_service import ProductionInboxMatchingService, directory_context_fingerprint
from production.models import ProductionEventStatus, ProductionSourceType
from production.review_models import (
    ManualGroupingOperation,
    ProductionInboxReviewDetail,
    ProductionInboxReviewItem,
    RefreshSummary,
    RejectionCode,
    ReviewDecision,
    ReviewDiagnosticIssue,
    ReviewDiagnosticKind,
    ReviewDiagnosticsReport,
    ReviewFilter,
    ReviewResult,
    ReviewStatus,
)
from production.review_repository import (
    ProductionInboxReviewConflictError,
    ProductionInboxReviewRepository,
    StaleProductionInboxReviewError,
)
from production.stage_alias_repository import ProductionStageAliasRepository


class SourceMediaReader(Protocol):
    def read_media(self, storage_key: str, expected_sha256: str) -> bytes: ...


class ProductionInboxReviewService:
    """Keep operator decisions separate from source, grouping and interpretation."""

    def __init__(
        self,
        repository: ProductionInboxReviewRepository,
        matching: ProductionInboxMatchingService,
        events: ProductionService,
        attachments: AttachmentService,
        directories,
        stage_aliases: ProductionStageAliasRepository,
    ) -> None:
        self.repository = repository
        self.matching = matching
        self.events = events
        self.attachments = attachments
        self.directories = directories
        self.stage_aliases = stage_aliases
        self.media_reader: SourceMediaReader | None = None

    def set_source_media_reader(self, reader: SourceMediaReader | None) -> None:
        self.media_reader = reader

    def list_items(
        self,
        review_filter: ReviewFilter = ReviewFilter.REQUIRES_REVIEW,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        sender_max_user_id: int | None = None,
        object_id: int | None = None,
        product_id: int | None = None,
    ) -> list[ProductionInboxReviewItem]:
        rows = self.repository.list_items()
        return [
            row for row in rows
            if _matches_filter(row, review_filter)
            and (date_from is None or row.observed_at_utc.date() >= date_from)
            and (date_to is None or row.observed_at_utc.date() <= date_to)
            and (sender_max_user_id is None or row.sender_max_user_id == sender_max_user_id)
            and (object_id is None or row.object_id == object_id)
            and (product_id is None or row.product_id == product_id)
        ]

    def detail(self, bundle_id: int, proposal_id: int) -> ProductionInboxReviewDetail:
        return self.repository.get_detail(bundle_id, proposal_id)

    def source_attachment_bytes(self, bundle_id: int, proposal_id: int, attachment_id: int) -> bytes:
        detail = self.detail(bundle_id, proposal_id)
        attachment = next((item for item in detail.attachments if item.id == attachment_id), None)
        if attachment is None:
            raise FileNotFoundError("Source attachment не найден")
        if attachment.media_state != "available" or not attachment.source_storage_key:
            raise FileNotFoundError("Source attachment недоступен")
        if self.media_reader is None:
            raise RuntimeError("WorkBot media source не настроен")
        return self.media_reader.read_media(
            attachment.source_storage_key, attachment.source_sha256
        )

    def confirm(self, decision: ReviewDecision) -> ReviewResult:
        self._validate_directory_context(decision)
        result = self.repository.begin_confirmation(decision)
        row = self.repository.review_row(result.review_id)
        if row is None:
            raise RuntimeError("Review decision не найден после сохранения")
        idempotency_key = self._event_key(result.review_uid)
        existing = self.events.events.find_by_idempotency_key(idempotency_key)
        if existing is not None and existing.status is ProductionEventStatus.CONFIRMED:
            return self.repository.finish_confirmation(
                result.review_id, existing.id or 0, decision.actor, recovered=True
            )
        try:
            promoted = self._materialize_all(result.review_id, decision.observed_at_utc)
            event = existing or self._create_event(decision, result.review_uid, idempotency_key)
            if event.id is None:
                raise RuntimeError("ProductionEvent не получил локальный id")
            if event.status in {ProductionEventStatus.DRAFT, ProductionEventStatus.READY}:
                for sort_order, attachment_id in enumerate(promoted):
                    self.events.attach_existing_attachment(
                        event.id, attachment_id, sort_order=sort_order
                    )
                if event.status is ProductionEventStatus.DRAFT:
                    event = self.events.mark_ready(event.id)
                event = self.events.confirm_event(
                    ConfirmProductionEvent(event.id, decision.actor, datetime.now(timezone.utc))
                )
            return self.repository.finish_confirmation(
                result.review_id, event.id, decision.actor
            )
        except Exception as exc:
            confirmed = self.events.events.find_by_idempotency_key(idempotency_key)
            if confirmed is not None and confirmed.status is ProductionEventStatus.CONFIRMED:
                return self.repository.finish_confirmation(
                    result.review_id, confirmed.id or 0, decision.actor, recovered=True
                )
            self.repository.mark_failed(result.review_id, decision.actor, str(exc))
            raise

    def reject(
        self,
        item: ProductionInboxReviewItem,
        actor,
        code: RejectionCode,
        comment: str = "",
    ) -> ReviewResult:
        return self.repository.reject(
            item.bundle_id, item.bundle_fingerprint, item.match_run_id,
            item.proposal_id, actor, code, comment,
        )

    def source_changed_event_id(self, item: ProductionInboxReviewItem) -> int | None:
        return self.repository.source_changed_event_id(item)

    def keep_existing(self, item: ProductionInboxReviewItem, actor) -> ReviewResult:
        return self.repository.keep_existing(item, actor)

    def manual_split(
        self,
        source_bundle_id: int,
        message_groups: tuple[tuple[int, ...], ...],
        actor,
    ) -> tuple[int, ...]:
        ids = self.repository.create_manual_bundles(
            (source_bundle_id,), message_groups, ManualGroupingOperation.SPLIT, actor
        )
        for bundle_id in ids:
            self.matching.match_bundle(bundle_id)
        return ids

    def manual_merge(
        self,
        source_bundle_ids: tuple[int, ...],
        message_ids: tuple[int, ...],
        actor,
        *,
        allow_mixed_senders: bool = False,
    ) -> int:
        ids = self.repository.create_manual_bundles(
            source_bundle_ids, (message_ids,), ManualGroupingOperation.MERGE, actor,
            allow_mixed_senders=allow_mixed_senders,
        )
        self.matching.match_bundle(ids[0])
        return ids[0]

    def remember_product_alias(self, alias_text: str, product_id: int) -> None:
        alias = _safe_alias(alias_text)
        self.directories.save_alias(AliasItem("product", alias, product_id))

    def remember_stage_alias(self, alias_text: str, stage_id: int) -> None:
        alias = _safe_alias(alias_text)
        self.stage_aliases.create(stage_id, alias)

    def diagnostics(self) -> ReviewDiagnosticsReport:
        raw = self.repository.raw_diagnostics()
        events = {int(row["id"]): str(row["source_ref"] or "") for row in raw["events"]}
        issues: list[ReviewDiagnosticIssue] = []
        confirmed = 0
        for row in raw["reviews"]:
            review_id = int(row["id"])
            bundle_id = int(row["bundle_id"])
            status = str(row["status"])
            if status == "confirmed":
                confirmed += 1
                event_id = row["production_event_id"]
                if event_id is None:
                    issues.append(ReviewDiagnosticIssue(
                        ReviewDiagnosticKind.CONFIRMED_WITHOUT_EVENT,
                        "Подтвержденное решение не связано с ProductionEvent",
                        review_id, bundle_id,
                    ))
                elif int(event_id) not in events:
                    issues.append(ReviewDiagnosticIssue(
                        ReviewDiagnosticKind.EVENT_MISSING,
                        "Связанный ProductionEvent отсутствует", review_id, bundle_id,
                    ))
                elif str(row["uid"]) not in events[int(event_id)]:
                    issues.append(ReviewDiagnosticIssue(
                        ReviewDiagnosticKind.SOURCE_REF_MISMATCH,
                        "ProductionEvent source_ref не содержит review UID",
                        review_id, bundle_id,
                    ))
        for row in raw["promotions"]:
            if str(row["status"]) != "materialized":
                issues.append(ReviewDiagnosticIssue(
                    ReviewDiagnosticKind.PROMOTION_INCOMPLETE,
                    "Продвижение source attachment не завершено",
                    int(row["review_id"]),
                ))
            if not str(row["source_message_id"]) or not str(row["source_attachment_id"]):
                issues.append(ReviewDiagnosticIssue(
                    ReviewDiagnosticKind.PROMOTION_PROVENANCE_MISSING,
                    "Для production Attachment потеряна source provenance",
                    int(row["review_id"]),
                ))
        for row in raw["manual_lineage"]:
            if row["manual_exists"] is None or row["source_exists"] is None:
                issues.append(ReviewDiagnosticIssue(
                    ReviewDiagnosticKind.BROKEN_MANUAL_LINEAGE,
                    "Нарушена lineage ручной группировки",
                    bundle_id=int(row["manual_bundle_id"]),
                ))
        pending = len(self.list_items(ReviewFilter.REQUIRES_REVIEW))
        return ReviewDiagnosticsReport(tuple(issues), len(raw["reviews"]), confirmed, pending)

    def _validate_directory_context(self, decision: ReviewDecision) -> None:
        detail = self.detail(decision.bundle_id, decision.proposal_id)
        if detail.item.bundle_fingerprint != decision.bundle_fingerprint:
            raise StaleProductionInboxReviewError(
                "Источник изменился. Обновите фотоотчет перед подтверждением."
            )
        current = directory_context_fingerprint(self.matching.repository.load_context())
        if current != detail.item.directory_context_fingerprint:
            self.matching.match_bundle(decision.bundle_id)
            raise StaleProductionInboxReviewError(
                "Справочники изменились. Предложение пересчитано; проверьте его снова."
            )

    def _materialize_all(self, review_id: int, received_at_utc: datetime) -> tuple[int, ...]:
        rows = self.repository.promotion_rows(review_id)
        if rows and self.media_reader is None:
            raise RuntimeError("WorkBot media source не настроен")
        promoted: list[int] = []
        for row in rows:
            if row["production_attachment_id"] is not None:
                promoted.append(int(row["production_attachment_id"]))
                continue
            attachment_id = int(row["inbox_attachment_id"])
            try:
                if str(row["media_state"]) != "available":
                    raise FileNotFoundError(
                        f"Source photo недоступно: {row['media_state']}"
                    )
                content = self.media_reader.read_media(
                    str(row["source_storage_key"]), str(row["source_sha256"])
                )
                if hashlib.sha256(content).hexdigest() != str(row["source_sha256"]):
                    raise ValueError("SHA-256 source photo не совпадает")
                production = self.attachments.store_bytes(
                    content,
                    original_name=str(row["original_name"] or "max-photo"),
                    received_at_utc=received_at_utc,
                    source_type="max_workbot",
                    source_message_id=str(row["source_message_id"]),
                    source_attachment_id=str(row["source_attachment_id"]),
                )
                if production.id is None:
                    raise RuntimeError("Production Attachment не получил id")
                self.repository.mark_promotion_materialized(
                    review_id, attachment_id, production.id
                )
                promoted.append(production.id)
            except Exception as exc:
                self.repository.mark_promotion_failed(review_id, attachment_id, str(exc))
                raise RuntimeError(
                    "Не удалось получить все фотографии фотоотчета"
                ) from exc
        return tuple(promoted)

    def _create_event(self, decision, review_uid, idempotency_key):
        common = dict(
            actor=decision.actor,
            observed_at_utc=decision.observed_at_utc,
            source_type=ProductionSourceType.INTEGRATION,
            product_id=decision.product_id,
            stage_id=decision.stage_id,
            readiness_percent=decision.readiness_percent,
            description=decision.description,
            source_ref=f"production-inbox-review:{review_uid}",
            reported_by_employee_id=decision.reported_by_employee_id,
            idempotency_key=idempotency_key,
        )
        if decision.event_type.value == "correction":
            return self.events.correct_event(CorrectProductionEvent(
                **common,
                source_event_id=decision.correction_source_event_id,
                reason=decision.change_reason,
            ))
        return self.events.create_event(CreateProductionEvent(
            **common,
            event_type=decision.event_type,
            change_reason=decision.change_reason,
        ))

    @staticmethod
    def _event_key(review_uid) -> str:
        return f"p11-review:{review_uid}"


def _matches_filter(item: ProductionInboxReviewItem, selected: ReviewFilter) -> bool:
    if selected is ReviewFilter.ALL:
        return True
    if selected is ReviewFilter.NEEDS_DESCRIPTION:
        return item.grouping_status == "needs_description"
    if selected is ReviewFilter.TEXT_ONLY:
        return item.grouping_status == "text_only"
    return item.review_status.value == selected.value


def _safe_alias(value: str) -> str:
    alias = value.strip()
    normalized = normalize_alias_text(alias)
    if not normalized or len(alias) > 80 or "%" in alias or normalized == "сборка":
        raise ValueError("Это выражение нельзя безопасно сохранить как алиас")
    if len(normalized.split()) > 5:
        raise ValueError("Для алиаса выберите короткое однозначное выражение")
    return alias
