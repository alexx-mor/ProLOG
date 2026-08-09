"""Presentation-facing production use-cases without persistence access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone, tzinfo
from pathlib import Path
from typing import Callable

from auth import AuthSession
from models import Employee, ProductItem, WorkLogEntry
from production.actor_adapter import actor_from_auth_session
from production.attachment_service import AttachmentService
from production.commands import (
    ConfirmProductionEvent,
    CorrectProductionEvent,
    CreateProductionEvent,
)
from production.event_service import ProductionService
from production.errors import (
    AttachmentIntegrityError,
    AttachmentNotFoundError,
    InvalidReadinessError,
    ProductionEventImmutableError,
    ProductionReferenceNotFoundError,
)
from production.models import (
    Attachment,
    ProductionEvent,
    ProductionEventStatus,
    ProductionEventType,
    ProductionSourceType,
    ProductionStage,
)
from production.projection_models import (
    ProductProductionState,
    ProductionTimelineItem,
)
from production.projections import ProductionProjectionService
from production.service import ProductionStageService
from services import DirectoryService, EmployeeService, WorkLogService


@dataclass(frozen=True, slots=True)
class ProductionEventFormData:
    observed_at_utc: datetime
    event_type: ProductionEventType
    stage_id: int | None
    readiness_percent: int | None
    description: str
    reported_by_employee_id: int | None
    change_reason: str
    idempotency_key: str


class ProductionUiController:
    """Stable boundary consumed by production widgets and dialogs."""

    def __init__(
        self,
        products: DirectoryService,
        employees: EmployeeService,
        worklogs: WorkLogService,
        stages: ProductionStageService,
        events: ProductionService,
        projections: ProductionProjectionService,
        attachments: AttachmentService,
        session_provider: Callable[[], AuthSession],
        *,
        local_timezone: tzinfo | None = None,
    ) -> None:
        self.products = products
        self.employees = employees
        self.worklogs = worklogs
        self.stages = stages
        self.events = events
        self.projections = projections
        self.attachments = attachments
        self.session_provider = session_provider
        self.local_timezone = local_timezone or datetime.now().astimezone().tzinfo or timezone.utc

    def product(self, product_id: int) -> ProductItem:
        product = self.products.get_product(product_id)
        if product is None:
            raise ValueError("Изделие больше не найдено в выбранной базе")
        return product

    def state(self, product_id: int) -> ProductProductionState:
        return self.projections.get_product_state(product_id)

    def timeline(
        self,
        product_id: int,
        *,
        include_audit: bool = False,
    ) -> list[ProductionTimelineItem]:
        return self.projections.get_product_timeline(
            product_id,
            include_audit=include_audit,
        )

    def worklogs_for_product(self, product_id: int) -> list[WorkLogEntry]:
        return self.worklogs.search_entries(product_id=product_id)

    def active_stages(self) -> list[ProductionStage]:
        return self.stages.list_active()

    def employees_for_reporting(self) -> list[Employee]:
        return self.employees.list()

    def create_draft(
        self,
        product_id: int,
        data: ProductionEventFormData,
        *,
        source_event_id: int | None = None,
    ) -> ProductionEvent:
        actor = actor_from_auth_session(self.session_provider())
        if source_event_id is not None:
            return self.events.correct_event(
                CorrectProductionEvent(
                    actor=actor,
                    observed_at_utc=data.observed_at_utc,
                    source_event_id=source_event_id,
                    source_type=ProductionSourceType.MANUAL,
                    product_id=product_id,
                    stage_id=data.stage_id,
                    readiness_percent=data.readiness_percent,
                    description=data.description,
                    reported_by_employee_id=data.reported_by_employee_id,
                    reason=data.change_reason,
                    idempotency_key=data.idempotency_key,
                )
            )
        if data.event_type is ProductionEventType.REWORK and not data.description.strip():
            raise ValueError("Для возврата или переработки укажите причину в описании")
        return self.events.create_event(
            CreateProductionEvent(
                actor=actor,
                observed_at_utc=data.observed_at_utc,
                source_type=ProductionSourceType.MANUAL,
                product_id=product_id,
                stage_id=data.stage_id,
                readiness_percent=data.readiness_percent,
                description=data.description,
                reported_by_employee_id=data.reported_by_employee_id,
                event_type=data.event_type,
                change_reason=data.change_reason,
                idempotency_key=data.idempotency_key,
            )
        )

    def store_photo(self, file_path: str | Path) -> Attachment:
        return self.attachments.store_file(
            Path(file_path),
            received_at_utc=datetime.now(timezone.utc),
            source_type="manual",
        )

    def attach_photo(
        self,
        event_id: int,
        attachment_id: int,
        sort_order: int,
    ) -> None:
        self.events.attach_existing_attachment(
            event_id,
            attachment_id,
            sort_order=sort_order,
        )

    def confirm_draft(self, event_id: int) -> ProductionEvent:
        event = self.events.get_event(event_id)
        if event.status is ProductionEventStatus.DRAFT:
            event = self.events.mark_ready(event_id)
        if event.status is ProductionEventStatus.READY:
            return self.events.confirm_event(
                ConfirmProductionEvent(
                    event_id,
                    actor_from_auth_session(self.session_provider()),
                    datetime.now(timezone.utc),
                )
            )
        if event.status is ProductionEventStatus.CONFIRMED:
            return event
        raise ValueError("Запись производства больше нельзя подтвердить")

    def attachment_bytes(self, attachment_id: int) -> bytes:
        return self.attachments.read_bytes(attachment_id)

    def requires_readiness_resolution(
        self,
        product_id: int,
        event_type: ProductionEventType,
        readiness_percent: int | None,
    ) -> tuple[int, int] | None:
        if event_type is not ProductionEventType.OBSERVATION or readiness_percent is None:
            return None
        current = self.state(product_id).readiness_percent
        if current is None or readiness_percent >= current:
            return None
        return current, readiness_percent

    def local_to_utc(self, local_date: date, local_time: time) -> datetime:
        local_value = datetime.combine(local_date, local_time).replace(
            tzinfo=self.local_timezone
        )
        return local_value.astimezone(timezone.utc)

    def utc_to_local(self, value: datetime) -> datetime:
        return value.astimezone(self.local_timezone)


def production_error_message(error: Exception) -> str:
    """Translate common domain failures without leaking infrastructure details."""

    if isinstance(error, InvalidReadinessError):
        return "Готовность должна быть целым числом от 0 до 100%."
    if isinstance(error, ProductionReferenceNotFoundError):
        return "Связанные данные больше не найдены. Обновите карточку и повторите действие."
    if isinstance(error, ProductionEventImmutableError):
        return "Подтвержденную запись нельзя изменить напрямую. Используйте исправление."
    if isinstance(error, AttachmentNotFoundError):
        return "Файл фотографии недоступен. Запись истории сохранена."
    if isinstance(error, AttachmentIntegrityError):
        return "Файл фотографии поврежден или изменен. Запись истории сохранена."
    if isinstance(error, ValueError):
        return str(error)
    return "Не удалось выполнить операцию. Подробности записаны в журнал ProLOG."
