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
from production.local_attachment_store import LocalAttachmentStore
from production.projections import ProductionProjectionService
from production.repository import ProductionStageRepository
from production.service import ProductionStageService


@dataclass(frozen=True, slots=True)
class ProductionModule:
    stages: ProductionStageService
    attachments: AttachmentService
    exports: AttachmentExportService
    events: ProductionService
    projections: ProductionProjectionService


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
    )
