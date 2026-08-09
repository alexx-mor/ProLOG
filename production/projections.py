"""Read-only projections rebuilt from ProductionEvent history."""

from __future__ import annotations

from database import DirectoryRepository, EmployeeRepository, WorkLogRepository
from production.attachment_repository import AttachmentRepository
from production.errors import ProductionReferenceNotFoundError
from production.event_repository import ProductionEventRepository
from production.models import ProductionEvent, ProductionEventStatus
from production.projection_models import (
    ProductLaborInterval,
    ProductProductionState,
    ProductionTimelineItem,
    ProjectionDiagnosticIssue,
    ProjectionDiagnosticKind,
    ProjectionDiagnosticsReport,
    ProjectionDiagnosticSeverity,
    ReadinessSource,
    TimelineAttachment,
    TimelineWorkLog,
)
from production.repository import ProductionStageRepository


class ProductionProjectionService:
    """Assemble current state and history without owning production facts."""

    def __init__(
        self,
        event_repository: ProductionEventRepository,
        stage_repository: ProductionStageRepository,
        attachment_repository: AttachmentRepository,
        product_repository: DirectoryRepository,
        employee_repository: EmployeeRepository,
        worklog_repository: WorkLogRepository,
    ) -> None:
        self.events = event_repository
        self.stages = stage_repository
        self.attachments = attachment_repository
        self.products = product_repository
        self.employees = employee_repository
        self.worklogs = worklog_repository

    def get_effective_events(self, product_id: int) -> list[ProductionEvent]:
        self._required_product(product_id)
        return self.events.list_confirmed_by_product(product_id)

    def get_product_state(self, product_id: int) -> ProductProductionState:
        product = self._required_product(product_id)
        events = self.events.list_confirmed_by_product(product_id)
        latest = events[-1] if events else None
        stage_event = next(
            (event for event in reversed(events) if event.stage_id is not None),
            None,
        )
        readiness_event = next(
            (
                event
                for event in reversed(events)
                if event.readiness_percent is not None
            ),
            None,
        )
        stage = (
            self.stages.get_by_id(stage_event.stage_id)
            if stage_event is not None and stage_event.stage_id is not None
            else None
        )
        attachment_count = sum(
            len(self.events.list_attachments(event.id or 0)) for event in events
        )
        if readiness_event is None:
            readiness = product.readiness_percent
            source = ReadinessSource.LEGACY_SNAPSHOT
        else:
            readiness = readiness_event.readiness_percent
            source = ReadinessSource.PRODUCTION_EVENT
        return ProductProductionState(
            product_id=product_id,
            object_id=product.object_id,
            current_stage_id=stage_event.stage_id if stage_event else None,
            current_stage_code=stage.code if stage else None,
            current_stage_name=stage.name if stage else None,
            readiness_percent=readiness,
            readiness_source=source,
            last_observed_at_utc=latest.observed_at_utc if latest else None,
            latest_effective_event_id=latest.id if latest else None,
            latest_effective_event_uid=latest.uid if latest else None,
            event_count=len(events),
            attachment_count=attachment_count,
            first_observed_at_utc=events[0].observed_at_utc if events else None,
        )

    def get_product_timeline(
        self,
        product_id: int,
        *,
        include_audit: bool = False,
    ) -> list[ProductionTimelineItem]:
        self._required_product(product_id)
        events = self.events.list_timeline_by_product(
            product_id,
            include_audit=include_audit,
        )
        superseding = {
            event.supersedes_event_id: event
            for event in events
            if event.status
            in {
                ProductionEventStatus.CONFIRMED,
                ProductionEventStatus.SUPERSEDED,
            }
            and event.supersedes_event_id is not None
        }
        items: list[ProductionTimelineItem] = []
        for event in events:
            attachment_items = []
            for relation in self.events.list_attachments(event.id or 0):
                attachment = self.attachments.get_by_id(relation.attachment_id)
                if attachment is not None:
                    attachment_items.append(
                        TimelineAttachment(attachment, relation.sort_order)
                    )
            worklog_items = []
            for relation in self.events.list_worklogs(event.id or 0):
                worklog = self.worklogs.get(relation.worklog_entry_id)
                if worklog is not None:
                    worklog_items.append(TimelineWorkLog(relation, worklog))
            employee = (
                self.employees.get(event.reported_by_employee_id)
                if event.reported_by_employee_id is not None
                else None
            )
            replacement = superseding.get(event.id)
            items.append(
                ProductionTimelineItem(
                    event=event,
                    stage=(
                        self.stages.get_by_id(event.stage_id)
                        if event.stage_id is not None
                        else None
                    ),
                    reported_employee_name=(
                        employee.full_name if employee is not None else None
                    ),
                    attachments=tuple(attachment_items),
                    worklogs=tuple(worklog_items),
                    is_effective=event.status is ProductionEventStatus.CONFIRMED,
                    superseded_by_event_id=replacement.id if replacement else None,
                    superseded_by_event_uid=replacement.uid if replacement else None,
                )
            )
        return items

    def get_labor_intervals(self, product_id: int) -> list[ProductLaborInterval]:
        events = self.get_effective_events(product_id)
        if len(events) < 2:
            return []
        first_date = events[0].observed_at_utc.date()
        last_date = events[-1].observed_at_utc.date()
        worklogs = self.worklogs.list_entries(
            product_id=product_id,
            date_from=first_date,
            date_to=last_date,
        )
        intervals = []
        for previous, current in zip(events, events[1:]):
            previous_date = previous.observed_at_utc.date()
            current_date = current.observed_at_utc.date()
            ambiguous = previous_date == current_date
            matching = (
                []
                if ambiguous
                else [
                    entry
                    for entry in worklogs
                    if previous_date < entry.work_date <= current_date
                ]
            )
            hours = sum(float(entry.hours) for entry in matching)
            intervals.append(
                ProductLaborInterval(
                    product_id=product_id,
                    previous_event_id=previous.id or 0,
                    current_event_id=current.id or 0,
                    previous_observed_at_utc=previous.observed_at_utc,
                    current_observed_at_utc=current.observed_at_utc,
                    work_date_from_exclusive=previous_date,
                    work_date_to_inclusive=current_date,
                    worklog_ids=tuple(
                        entry.id for entry in matching if entry.id is not None
                    ),
                    worklog_count=len(matching),
                    employee_count=len({entry.employee_id for entry in matching}),
                    total_hours=hours,
                    person_hours=hours,
                    day_granularity_ambiguous=ambiguous,
                )
            )
        return intervals

    def reconcile_product_snapshot(
        self,
        product_id: int,
    ) -> ProductProductionState:
        state = self.get_product_state(product_id)
        product = self._required_product(product_id)
        if (
            state.readiness_source is ReadinessSource.PRODUCTION_EVENT
            and state.readiness_percent is not None
            and product.readiness_percent != state.readiness_percent
        ):
            self.products.update_product_readiness_snapshot(
                product_id,
                state.readiness_percent,
            )
        return state

    def diagnose_product_projection(
        self,
        product_id: int,
    ) -> ProjectionDiagnosticsReport:
        product = self.products.get_product(product_id)
        if product is None:
            return ProjectionDiagnosticsReport(
                (product_id,),
                (
                    ProjectionDiagnosticIssue(
                        ProjectionDiagnosticKind.PRODUCT_MISSING,
                        ProjectionDiagnosticSeverity.ERROR,
                        product_id,
                        "ProductionEvent ссылается на отсутствующее изделие",
                    ),
                ),
            )
        issues: list[ProjectionDiagnosticIssue] = []
        effective = self.events.list_confirmed_by_product(product_id)
        state = self.get_product_state(product_id)
        if (
            state.readiness_source is ReadinessSource.PRODUCTION_EVENT
            and product.readiness_percent != state.readiness_percent
        ):
            issues.append(
                ProjectionDiagnosticIssue(
                    ProjectionDiagnosticKind.SNAPSHOT_MISMATCH,
                    ProjectionDiagnosticSeverity.WARNING,
                    product_id,
                    "Compatibility snapshot готовности расходится с ProductionEvent",
                    expected_readiness=state.readiness_percent,
                    actual_readiness=product.readiness_percent,
                )
            )
        for event in effective:
            if event.stage_id is not None:
                stage = self.stages.get_by_id(event.stage_id)
                if stage is None:
                    issues.append(
                        ProjectionDiagnosticIssue(
                            ProjectionDiagnosticKind.STAGE_MISSING,
                            ProjectionDiagnosticSeverity.ERROR,
                            product_id,
                            "ProductionEvent ссылается на отсутствующий этап",
                            event_id=event.id,
                        )
                    )
                elif not stage.is_active:
                    issues.append(
                        ProjectionDiagnosticIssue(
                            ProjectionDiagnosticKind.STAGE_INACTIVE,
                            ProjectionDiagnosticSeverity.INFO,
                            product_id,
                            "Исторический этап отключен, но остается доступен в timeline",
                            event_id=event.id,
                        )
                    )
        issues.extend(self._diagnose_supersede_chain(product_id))
        return ProjectionDiagnosticsReport((product_id,), tuple(issues))

    def diagnose_all_product_snapshots(self) -> ProjectionDiagnosticsReport:
        existing_ids = {
            product.id
            for product in self.products.list_products(active_only=False)
            if product.id is not None
        }
        referenced_ids = set(self.events.list_referenced_product_ids())
        checked_ids = tuple(sorted(existing_ids | referenced_ids))
        issues = []
        for product_id in checked_ids:
            issues.extend(self.diagnose_product_projection(product_id).issues)
        return ProjectionDiagnosticsReport(checked_ids, tuple(issues))

    def _diagnose_supersede_chain(
        self,
        product_id: int,
    ) -> list[ProjectionDiagnosticIssue]:
        events = self.events.list_timeline_by_product(product_id, include_audit=True)
        by_id = {event.id: event for event in events if event.id is not None}
        corrections_by_source: dict[int, list[ProductionEvent]] = {}
        for event in events:
            if (
                event.status
                in {
                    ProductionEventStatus.CONFIRMED,
                    ProductionEventStatus.SUPERSEDED,
                }
                and event.supersedes_event_id is not None
            ):
                corrections_by_source.setdefault(event.supersedes_event_id, []).append(event)
        issues = []
        for event in events:
            if event.id is None:
                continue
            if event.status is ProductionEventStatus.SUPERSEDED:
                replacements = corrections_by_source.get(event.id, [])
                if len(replacements) != 1:
                    issues.append(self._chain_issue(product_id, event.id))
            if (
                event.status
                in {
                    ProductionEventStatus.CONFIRMED,
                    ProductionEventStatus.SUPERSEDED,
                }
                and event.supersedes_event_id is not None
            ):
                source = by_id.get(event.supersedes_event_id)
                if source is None or source.status is not ProductionEventStatus.SUPERSEDED:
                    issues.append(self._chain_issue(product_id, event.id))
        return issues

    @staticmethod
    def _chain_issue(product_id: int, event_id: int) -> ProjectionDiagnosticIssue:
        return ProjectionDiagnosticIssue(
            ProjectionDiagnosticKind.SUPERSEDE_CHAIN_INCONSISTENT,
            ProjectionDiagnosticSeverity.ERROR,
            product_id,
            "Цепочка correction/superseded несогласованна",
            event_id=event_id,
        )

    def _required_product(self, product_id: int):
        product = self.products.get_product(product_id)
        if product is None:
            raise ProductionReferenceNotFoundError("Изделие не найдено")
        return product
