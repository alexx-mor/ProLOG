"""Composition root for the local production module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from production.attachment_repository import AttachmentRepository
from production.attachment_export import AttachmentExportService
from production.attachment_service import AttachmentService
from production.event_repository import ProductionEventRepository
from production.event_service import ProductionService
from production.grouping_repository import ProductionInboxGroupingRepository
from production.grouping_service import ProductionInboxGroupingService
from production.matching_repository import ProductionInboxMatchingRepository
from production.matching_service import ProductionInboxMatchingService
from production.local_attachment_store import LocalAttachmentStore
from production.projections import ProductionProjectionService
from production.repository import ProductionStageRepository
from production.service import ProductionStageService
from production.source_transport_repository import ProductionSourceTransportRepository
from production.source_transport_service import ProductionSourceTransportService


@dataclass(frozen=True, slots=True)
class ProductionModule:
    stages: ProductionStageService
    attachments: AttachmentService
    exports: AttachmentExportService
    events: ProductionService
    projections: ProductionProjectionService
    source_transport: ProductionSourceTransportService
    grouping: ProductionInboxGroupingService
    matching: ProductionInboxMatchingService


def build_production_module(
    database: Database,
    attachment_root: Path,
) -> ProductionModule:
    """Wire production infrastructure outside presentation widgets."""

    directories = DirectoryRepository(database)
    employees = EmployeeRepository(database)
    worklogs = WorkLogRepository(database)
    stages = ProductionStageRepository(database)
    attachments = AttachmentRepository(database)
    events = ProductionEventRepository(database)
    source_transport = ProductionSourceTransportService(
        ProductionSourceTransportRepository(database)
    )
    grouping = ProductionInboxGroupingService(
        ProductionInboxGroupingRepository(database)
    )
    matching = ProductionInboxMatchingService(
        ProductionInboxMatchingRepository(database)
    )
    projections = ProductionProjectionService(
        events,
        stages,
        attachments,
        directories,
        employees,
        worklogs,
    )
    attachment_service = AttachmentService(
        attachments,
        LocalAttachmentStore(attachment_root),
    )
    return ProductionModule(
        stages=ProductionStageService(stages),
        attachments=attachment_service,
        exports=AttachmentExportService(attachment_service),
        events=ProductionService(
            events,
            stages,
            attachments,
            directories,
            employees,
            worklogs,
            projection_service=projections,
        ),
        projections=projections,
        source_transport=source_transport,
        grouping=grouping,
        matching=matching,
    )
