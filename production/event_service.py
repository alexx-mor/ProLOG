"""Application use-cases for persistent production events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from database import DirectoryRepository, EmployeeRepository, WorkLogRepository
from models import ProductItem
from production.attachment_repository import AttachmentRepository
from production.commands import (
    ConfirmProductionEvent,
    CorrectProductionEvent,
    CreateProductionEvent,
    RejectProductionEvent,
)
from production.errors import (
    InvalidProductionCorrectionError,
    InvalidProductionTransitionError,
    ObjectSnapshotManagedError,
    ProductionEventIdempotencyConflictError,
    ProductionEventImmutableError,
    ProductionEventNotFoundError,
    ProductionReferenceNotFoundError,
    UnexplainedReadinessDecreaseError,
)
from production.event_repository import ProductionEventRepository
from production.models import (
    ActorRef,
    ProductionEvent,
    ProductionEventAttachment,
    ProductionEventStatus,
    ProductionEventType,
    ProductionEventWorkLog,
    WorkLogRelationType,
    utc_now,
)
from production.repository import ProductionStageRepository


class ProductionService:
    """Coordinate event use-cases without UI, WorkBot or filesystem access."""

    def __init__(
        self,
        event_repository: ProductionEventRepository,
        stage_repository: ProductionStageRepository,
        attachment_repository: AttachmentRepository,
        product_repository: DirectoryRepository,
        employee_repository: EmployeeRepository,
        worklog_repository: WorkLogRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.events = event_repository
        self.stages = stage_repository
        self.attachments = attachment_repository
        self.products = product_repository
        self.employees = employee_repository
        self.worklogs = worklog_repository
        self.clock = clock

    def create_event(self, command: CreateProductionEvent) -> ProductionEvent:
        if command.event_type is ProductionEventType.CORRECTION:
            raise InvalidProductionCorrectionError(
                "Correction создается только через correct_event"
            )
        self._reject_managed_snapshot(command.object_id_snapshot)
        self._validate_reference_ids(
            command.product_id,
            command.stage_id,
            command.reported_by_employee_id,
        )
        now = self.clock()
        event = ProductionEvent(
            event_type=command.event_type,
            observed_at_utc=command.observed_at_utc,
            recorded_at_utc=now,
            created_at_utc=now,
            source_type=command.source_type,
            source_ref=_optional_text(command.source_ref),
            created_by=command.actor,
            product_id=command.product_id,
            stage_id=command.stage_id,
            readiness_percent=command.readiness_percent,
            description=command.description.strip(),
            change_reason=command.change_reason.strip(),
            reported_by_employee_id=command.reported_by_employee_id,
            idempotency_key=_optional_text(command.idempotency_key),
        )
        return self._create_idempotently(event)

    def get_event(self, event_id: int) -> ProductionEvent:
        return self._required(event_id)

    def mark_ready(self, event_id: int) -> ProductionEvent:
        event = self._required(event_id)
        if event.status is ProductionEventStatus.READY:
            return event
        if event.status is not ProductionEventStatus.DRAFT:
            raise InvalidProductionTransitionError(
                "В ready можно перевести только draft production event"
            )
        return self.events.mark_ready(event_id)

    def confirm_event(self, command: ConfirmProductionEvent) -> ProductionEvent:
        event = self._required(command.event_id)
        if event.status is ProductionEventStatus.CONFIRMED:
            return event
        if event.status is not ProductionEventStatus.READY:
            raise InvalidProductionTransitionError(
                "Подтвердить можно только production event в статусе ready"
            )
        product = self._required_product(event.product_id)
        self._validate_reference_ids(
            event.product_id,
            event.stage_id,
            event.reported_by_employee_id,
        )
        for relation in self.events.list_attachments(command.event_id):
            if not self.attachments.exists(relation.attachment_id):
                raise ProductionReferenceNotFoundError(
                    f"Attachment {relation.attachment_id} не найден"
                )
        if event.event_type is ProductionEventType.CORRECTION:
            self._validate_correction_source(event)
        self._validate_readiness_change(event)
        return self.events.confirm(
            command.event_id,
            command.actor,
            command.confirmed_at_utc,
            product.object_id,
        )

    def reject_event(self, command: RejectProductionEvent) -> ProductionEvent:
        event = self._required(command.event_id)
        if event.status is ProductionEventStatus.REJECTED:
            return event
        if event.status not in {
            ProductionEventStatus.DRAFT,
            ProductionEventStatus.READY,
        }:
            raise InvalidProductionTransitionError(
                "Отклонить можно только draft или ready production event"
            )
        return self.events.reject(
            command.event_id,
            command.actor,
            command.rejected_at_utc,
            command.reason,
        )

    def correct_event(self, command: CorrectProductionEvent) -> ProductionEvent:
        self._reject_managed_snapshot(command.object_id_snapshot)
        source = self._required(int(command.source_event_id))
        self._validate_reference_ids(
            command.product_id,
            command.stage_id,
            command.reported_by_employee_id,
        )
        now = self.clock()
        correction = ProductionEvent(
            event_type=ProductionEventType.CORRECTION,
            observed_at_utc=command.observed_at_utc,
            recorded_at_utc=now,
            created_at_utc=now,
            source_type=command.source_type,
            source_ref=_optional_text(command.source_ref),
            created_by=command.actor,
            product_id=command.product_id,
            stage_id=command.stage_id,
            readiness_percent=command.readiness_percent,
            description=command.description.strip(),
            change_reason=command.reason.strip(),
            reported_by_employee_id=command.reported_by_employee_id,
            supersedes_event_id=source.id,
            idempotency_key=_optional_text(command.idempotency_key),
        )
        if correction.idempotency_key:
            existing = self.events.find_by_idempotency_key(correction.idempotency_key)
            if existing is not None:
                return self._require_same_payload(existing, correction)
        if source.status is not ProductionEventStatus.CONFIRMED:
            raise InvalidProductionCorrectionError(
                "Correction может исправлять только confirmed production event"
            )
        if self.events.find_superseding_event(source.id or 0) is not None:
            raise InvalidProductionCorrectionError(
                "Production event уже заменен подтвержденной корректировкой"
            )
        self._require_acyclic_source(source)
        return self._create_idempotently(correction)

    def attach_existing_attachment(
        self,
        event_id: int,
        attachment_id: int,
        *,
        sort_order: int | None = None,
    ) -> ProductionEventAttachment:
        self._require_mutable(event_id)
        if not self.attachments.exists(attachment_id):
            raise ProductionReferenceNotFoundError("Attachment не найден")
        order = (
            self.events.next_attachment_sort_order(event_id)
            if sort_order is None
            else sort_order
        )
        return self.events.add_attachment_relation(event_id, attachment_id, order)

    def detach_attachment(self, event_id: int, attachment_id: int) -> None:
        self._require_mutable(event_id)
        self.events.remove_attachment_relation(event_id, attachment_id)

    def link_worklog(
        self,
        event_id: int,
        worklog_entry_id: int,
        actor: ActorRef,
        relation_type: WorkLogRelationType = WorkLogRelationType.EXPLICIT,
    ) -> ProductionEventWorkLog:
        self._require_mutable(event_id)
        if relation_type is WorkLogRelationType.SAME_PRODUCT_PERIOD:
            raise InvalidProductionTransitionError(
                "same_product_period является производной связью и не сохраняется в P4"
            )
        if self.worklogs.get(worklog_entry_id) is None:
            raise ProductionReferenceNotFoundError("Запись журнала работ не найдена")
        return self.events.add_worklog_relation(
            event_id,
            worklog_entry_id,
            relation_type,
            actor,
            self.clock(),
        )

    def unlink_worklog(self, event_id: int, worklog_entry_id: int) -> None:
        self._require_mutable(event_id)
        self.events.remove_worklog_relation(event_id, worklog_entry_id)

    def list_product_events(self, product_id: int) -> list[ProductionEvent]:
        if self.products.get_product(product_id) is None:
            raise ProductionReferenceNotFoundError("Изделие не найдено")
        return self.events.list_by_product(product_id)

    def _create_idempotently(self, event: ProductionEvent) -> ProductionEvent:
        key = event.idempotency_key
        if key:
            existing = self.events.find_by_idempotency_key(key)
            if existing is not None:
                return self._require_same_payload(existing, event)
        try:
            return self.events.create(event)
        except ProductionEventIdempotencyConflictError:
            existing = self.events.find_by_idempotency_key(key or "")
            if existing is None:
                raise
            return self._require_same_payload(existing, event)

    @staticmethod
    def _require_same_payload(
        existing: ProductionEvent,
        candidate: ProductionEvent,
    ) -> ProductionEvent:
        if _payload_fingerprint(existing) != _payload_fingerprint(candidate):
            raise ProductionEventIdempotencyConflictError(
                "Один idempotency_key использован для разных production event"
            )
        return existing

    def _validate_reference_ids(
        self,
        product_id: int | None,
        stage_id: int | None,
        employee_id: int | None,
    ) -> None:
        if product_id is not None and self.products.get_product(product_id) is None:
            raise ProductionReferenceNotFoundError("Изделие не найдено")
        if stage_id is not None and not self.stages.exists(stage_id):
            raise ProductionReferenceNotFoundError("Производственный этап не найден")
        if employee_id is not None and self.employees.get(employee_id) is None:
            raise ProductionReferenceNotFoundError("Сотрудник не найден")

    def _required_product(self, product_id: int | None) -> ProductItem:
        if product_id is None:
            raise ProductionReferenceNotFoundError(
                "Для подтверждения production event требуется изделие"
            )
        product = self.products.get_product(product_id)
        if product is None:
            raise ProductionReferenceNotFoundError("Изделие не найдено")
        return product

    def _validate_readiness_change(self, event: ProductionEvent) -> None:
        if event.product_id is None or event.readiness_percent is None:
            return
        previous = self.events.latest_confirmed_readiness(event.product_id)
        if previous is None or event.readiness_percent >= previous:
            return
        if event.event_type in {
            ProductionEventType.REWORK,
            ProductionEventType.CORRECTION,
        }:
            return
        if not event.change_reason.strip():
            raise UnexplainedReadinessDecreaseError(
                "Снижение готовности observation требует сохраненной причины или rework"
            )

    def _validate_correction_source(self, event: ProductionEvent) -> None:
        if event.supersedes_event_id is None or event.supersedes_event_id == event.id:
            raise InvalidProductionCorrectionError("Некорректная correction-ссылка")
        source = self._required(event.supersedes_event_id)
        if source.status is not ProductionEventStatus.CONFIRMED:
            raise InvalidProductionCorrectionError(
                "Исходное событие correction должно оставаться confirmed до подтверждения"
            )
        superseding = self.events.find_superseding_event(source.id or 0)
        if superseding is not None and superseding.id != event.id:
            raise InvalidProductionCorrectionError(
                "Исходное событие уже заменено другой корректировкой"
            )

    def _require_acyclic_source(self, source: ProductionEvent) -> None:
        visited: set[int] = set()
        current: ProductionEvent | None = source
        while current is not None and current.supersedes_event_id is not None:
            if current.id is None or current.id in visited:
                raise InvalidProductionCorrectionError("Обнаружен цикл correction-ссылок")
            visited.add(current.id)
            current = self.events.get_by_id(current.supersedes_event_id)

    def _require_mutable(self, event_id: int) -> ProductionEvent:
        event = self._required(event_id)
        if event.status not in {
            ProductionEventStatus.DRAFT,
            ProductionEventStatus.READY,
        }:
            raise ProductionEventImmutableError(
                "Связи confirmed/rejected/superseded production event неизменяемы"
            )
        return event

    def _required(self, event_id: int) -> ProductionEvent:
        event = self.events.get_by_id(event_id)
        if event is None:
            raise ProductionEventNotFoundError("Production event не найден")
        return event

    @staticmethod
    def _reject_managed_snapshot(object_id_snapshot: int | None) -> None:
        if object_id_snapshot is not None:
            raise ObjectSnapshotManagedError(
                "object_id_snapshot автоматически фиксируется при подтверждении"
            )


def _optional_text(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


def _payload_fingerprint(event: ProductionEvent) -> str:
    payload = {
        "event_type": event.event_type.value,
        "observed_at_utc": event.observed_at_utc.isoformat(),
        "source_type": event.source_type.value,
        "source_ref": event.source_ref,
        "product_id": event.product_id,
        "stage_id": event.stage_id,
        "readiness_percent": event.readiness_percent,
        "description": event.description,
        "change_reason": event.change_reason,
        "reported_by_employee_id": event.reported_by_employee_id,
        "supersedes_event_id": event.supersedes_event_id,
        "actor_type": event.created_by.actor_type.value,
        "actor_uid": str(event.created_by.uid),
        "actor_local_user_id": event.created_by.local_user_id,
        "actor_display_name": event.created_by.display_name,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
