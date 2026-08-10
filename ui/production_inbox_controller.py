"""Presentation boundary for the P11 production photo-report inbox."""

from __future__ import annotations

from datetime import date, datetime, timezone, tzinfo
from typing import Callable

from auth import AuthSession
from production.actor_adapter import actor_from_auth_session
from production.models import ProductionEventType
from production.review_models import (
    ProductionInboxReviewDetail,
    ProductionInboxReviewItem,
    RefreshSummary,
    RejectionCode,
    ReviewDecision,
    ReviewFilter,
    ReviewResult,
)
from production.review_service import ProductionInboxReviewService
from services import DirectoryService, EmployeeService


class ProductionInboxController:
    """Expose review use-cases without persistence or physical-path knowledge."""

    def __init__(
        self,
        review: ProductionInboxReviewService,
        directories: DirectoryService,
        employees: EmployeeService,
        stages,
        session_provider: Callable[[], AuthSession],
        refresh_pipeline: Callable[[], RefreshSummary],
        *,
        local_timezone: tzinfo | None = None,
    ) -> None:
        self.review = review
        self.directories = directories
        self.employees = employees
        self.stages = stages
        self.session_provider = session_provider
        self.refresh_pipeline = refresh_pipeline
        self.local_timezone = local_timezone or datetime.now().astimezone().tzinfo or timezone.utc

    def refresh_source(self) -> RefreshSummary:
        return self.refresh_pipeline()

    def list_items(self, selected: ReviewFilter = ReviewFilter.REQUIRES_REVIEW, **filters):
        return self.review.list_items(selected, **filters)

    def detail(self, item: ProductionInboxReviewItem) -> ProductionInboxReviewDetail:
        return self.review.detail(item.bundle_id, item.proposal_id)

    def products(self):
        return self.directories.list_products(active_only=True)

    def objects(self):
        return self.directories.list_all("objects")

    def active_stages(self):
        return self.stages.list_active()

    def employees_for_reporting(self):
        return self.employees.list()

    def source_attachment_bytes(self, item: ProductionInboxReviewItem, attachment_id: int) -> bytes:
        return self.review.source_attachment_bytes(item.bundle_id, item.proposal_id, attachment_id)

    def confirm(
        self,
        item: ProductionInboxReviewItem,
        *,
        product_id: int,
        stage_id: int | None,
        readiness_percent: int | None,
        description: str,
        observed_at_local: datetime,
        reported_by_employee_id: int | None,
        event_type: ProductionEventType,
        change_reason: str = "",
        correction_source_event_id: int | None = None,
    ) -> ReviewResult:
        observed = observed_at_local
        if observed.tzinfo is None or observed.utcoffset() is None:
            observed = observed.replace(tzinfo=self.local_timezone)
        actor = actor_from_auth_session(self.session_provider())
        return self.review.confirm(ReviewDecision(
            bundle_id=item.bundle_id,
            bundle_fingerprint=item.bundle_fingerprint,
            match_run_id=item.match_run_id,
            proposal_id=item.proposal_id,
            product_id=product_id,
            stage_id=stage_id,
            readiness_percent=readiness_percent,
            description=description,
            observed_at_utc=observed.astimezone(timezone.utc),
            reported_by_employee_id=reported_by_employee_id,
            actor=actor,
            event_type=event_type,
            change_reason=change_reason,
            correction_source_event_id=correction_source_event_id,
        ))

    def reject(
        self,
        item: ProductionInboxReviewItem,
        code: RejectionCode,
        comment: str,
    ) -> ReviewResult:
        return self.review.reject(
            item, actor_from_auth_session(self.session_provider()), code, comment
        )

    def keep_existing(self, item: ProductionInboxReviewItem) -> ReviewResult:
        return self.review.keep_existing(
            item, actor_from_auth_session(self.session_provider())
        )

    def source_changed_event_id(self, item: ProductionInboxReviewItem) -> int | None:
        return self.review.source_changed_event_id(item)

    def split(self, item: ProductionInboxReviewItem, groups: tuple[tuple[int, ...], ...]):
        return self.review.manual_split(
            item.bundle_id, groups, actor_from_auth_session(self.session_provider())
        )

    def merge(
        self,
        items: tuple[ProductionInboxReviewItem, ...],
        message_ids: tuple[int, ...],
        *,
        allow_mixed_senders: bool = False,
    ) -> int:
        return self.review.manual_merge(
            tuple(item.bundle_id for item in items), message_ids,
            actor_from_auth_session(self.session_provider()),
            allow_mixed_senders=allow_mixed_senders,
        )

    def remember_product_alias(self, alias_text: str, product_id: int) -> None:
        self.review.remember_product_alias(alias_text, product_id)

    def remember_stage_alias(self, alias_text: str, stage_id: int) -> None:
        self.review.remember_stage_alias(alias_text, stage_id)

    def local_observed_at(self, item: ProductionInboxReviewItem) -> datetime:
        return item.observed_at_utc.astimezone(self.local_timezone)

    def local_datetime(self, value: datetime) -> datetime:
        return value.astimezone(self.local_timezone)

    def current_readiness(self, product_id: int) -> int | None:
        return self.review.events.projections.get_product_state(product_id).readiness_percent

    @staticmethod
    def date_filters(date_from: date | None, date_to: date | None) -> dict[str, date | None]:
        return {"date_from": date_from, "date_to": date_to}
