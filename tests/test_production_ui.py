"""P6 tests for the first manual production UI."""

from __future__ import annotations

import ast
import base64
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from auth import AuthSession, ROLE_ADMIN
from database import Database, DirectoryRepository, EmployeeRepository, WorkLogRepository
from models import Employee, ProductItem, WorkLogEntry
from production.actor_adapter import actor_from_auth_session
from production.models import ProductionEventStatus, ProductionEventType
from production.module import ProductionModule, build_production_module
from services import DirectoryService, EmployeeService, WorkLogService
from ui.product_production_dialog import ProductProductionDialog
from ui.production_controller import ProductionEventFormData, ProductionUiController
from ui.production_event_dialog import ProductionEventDialog
from ui.production_photo_viewer import ProductionPhotoViewer
from ui.production_overview_widget import ProductionOverviewWidget


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
MSK = timezone(timedelta(hours=3))


@dataclass(slots=True)
class UiContext:
    database: Database
    directories_repository: DirectoryRepository
    employees_repository: EmployeeRepository
    worklogs_repository: WorkLogRepository
    directories: DirectoryService
    employees: EmployeeService
    worklogs: WorkLogService
    controller: ProductionUiController
    production_module: ProductionModule
    attachment_root: Path
    product_id: int
    object_id: int
    employee_id: int


@pytest.fixture(scope="module", autouse=True)
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def context(tmp_path: Path) -> UiContext:
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories_repository = DirectoryRepository(database)
    employees_repository = EmployeeRepository(database)
    worklogs_repository = WorkLogRepository(database)
    directories = DirectoryService(directories_repository)
    employees = EmployeeService(employees_repository, directories)
    worklogs = WorkLogService(worklogs_repository, directories)
    object_id = directories_repository.upsert("objects", "Испытательный объект")
    product_id = directories_repository.save_product(
        ProductItem(
            object_id=object_id,
            object_name="Испытательный объект",
            name="ШУ тестовый",
            serial_number="P6-001",
            readiness_percent=0,
        )
    )
    employee_id = employees_repository.save(Employee("Иванов Иван Иванович"))
    attachment_root = tmp_path / "attachments"
    module = build_production_module(database, attachment_root)
    session = AuthSession(
        username="Руководитель",
        role=ROLE_ADMIN,
        organization_name='ООО "Компания"',
        department_name="АСУТП",
        leader_full_name="Иванов Иван Иванович",
    )
    controller = ProductionUiController(
        directories,
        employees,
        worklogs,
        module.stages,
        module.events,
        module.projections,
        module.attachments,
        module.exports,
        lambda: session,
        local_timezone=MSK,
    )
    return UiContext(
        database,
        directories_repository,
        employees_repository,
        worklogs_repository,
        directories,
        employees,
        worklogs,
        controller,
        module,
        attachment_root,
        product_id,
        object_id,
        employee_id,
    )


def _form(
    readiness: int | None,
    *,
    event_type: ProductionEventType = ProductionEventType.OBSERVATION,
    description: str = "Наблюдение",
    key: str = "test-ui",
) -> ProductionEventFormData:
    return ProductionEventFormData(
        observed_at_utc=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
        event_type=event_type,
        stage_id=None,
        readiness_percent=readiness,
        description=description,
        reported_by_employee_id=None,
        change_reason="",
        idempotency_key=key,
    )


def _confirmed(context: UiContext, readiness: int, key: str = "confirmed"):
    event = context.controller.create_draft(context.product_id, _form(readiness, key=key))
    assert event.id is not None
    return context.controller.confirm_draft(event.id)


def test_actor_adapter_is_stable_and_not_employee_identity() -> None:
    session = AuthSession("Оператор", ROLE_ADMIN, "Организация", "Отдел", "Руководитель")

    first = actor_from_auth_session(session)
    second = actor_from_auth_session(session)

    assert first.uid == second.uid
    assert first.local_user_id is None
    assert first.display_name == "Оператор"


def test_controller_converts_local_time_to_aware_utc(context: UiContext) -> None:
    converted = context.controller.local_to_utc(date(2026, 8, 9), time(12, 30))

    assert converted == datetime(2026, 8, 9, 9, 30, tzinfo=timezone.utc)
    assert context.controller.utc_to_local(converted).hour == 12


@pytest.mark.parametrize("readiness", [None, 0, 100])
def test_manual_dialog_confirms_null_zero_and_hundred(
    context: UiContext,
    readiness: int | None,
) -> None:
    product = context.controller.product(context.product_id)
    dialog = ProductionEventDialog(context.controller, product)
    dialog.description_edit.setPlainText(f"Значение {readiness}")
    dialog.readiness_edit.setText("" if readiness is None else str(readiness))
    dialog._show_error = pytest.fail  # type: ignore[method-assign]

    dialog._save()

    assert dialog.saved_event is not None
    assert dialog.saved_event.status is ProductionEventStatus.CONFIRMED
    assert dialog.saved_event.readiness_percent == readiness


def test_stage_combo_contains_only_active_stages(context: UiContext) -> None:
    stages = context.controller.active_stages()
    assert len(stages) >= 2
    disabled = stages[0]
    assert disabled.id is not None
    context.controller.stages.deactivate(disabled.id)

    dialog = ProductionEventDialog(
        context.controller,
        context.controller.product(context.product_id),
    )
    ids = {dialog.stage_combo.itemData(index) for index in range(dialog.stage_combo.count())}

    assert disabled.id not in ids
    assert None in ids


def test_empty_card_shows_legacy_state_and_first_action(context: UiContext) -> None:
    card = ProductProductionDialog(context.controller, context.product_id)

    assert "из карточки изделия" in card.readiness_value.text()
    assert card.add_first_button.isVisibleTo(card.production_tab)
    assert card.timeline_widget.empty_label.text().startswith("История производства")


def test_product_worklogs_are_aggregated_without_creating_event_links(
    context: UiContext,
) -> None:
    context.worklogs_repository.save(
        WorkLogEntry(
            employee_id=context.employee_id,
            work_date=date(2026, 8, 9),
            location_id=None,
            object_id=context.object_id,
            work_type_id=None,
            product_id=context.product_id,
            description="Монтаж",
            hours=7.5,
        )
    )
    card = ProductProductionDialog(context.controller, context.product_id)

    assert card.worklogs_table.rowCount() == 1
    assert "7,5" in card.worklog_summary.text()
    assert card.timeline_items == []


def test_manual_photo_is_stored_attached_and_read_through_service(
    context: UiContext,
    tmp_path: Path,
) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG_1X1)
    dialog = ProductionEventDialog(
        context.controller,
        context.controller.product(context.product_id),
    )
    dialog.description_edit.setPlainText("Фото изделия")
    photo_item = QListWidgetItem(path.name)
    photo_item.setData(Qt.ItemDataRole.UserRole, {"path": str(path)})
    dialog.photos.addItem(photo_item)
    dialog._show_error = pytest.fail  # type: ignore[method-assign]

    dialog._save()

    timeline = context.controller.timeline(context.product_id)
    assert len(timeline) == 1
    assert len(timeline[0].attachments) == 1
    attachment_id = timeline[0].attachments[0].attachment.id
    assert context.controller.attachment_bytes(attachment_id or 0) == PNG_1X1


def test_correction_and_rework_are_explicit_and_refreshable(context: UiContext) -> None:
    original = _confirmed(context, 70, "original")
    original_item = context.controller.timeline(context.product_id)[0]
    correction = ProductionEventDialog(
        context.controller,
        context.controller.product(context.product_id),
        source_event=original,
        source_attachments=original_item.attachments,
    )
    correction.readiness_edit.setText("65")
    correction.description_edit.setPlainText("Исправленная оценка")
    correction._show_error = pytest.fail  # type: ignore[method-assign]
    correction._save()

    refreshed_original = context.controller.events.get_event(original.id or 0)
    assert refreshed_original.status is ProductionEventStatus.SUPERSEDED
    assert correction.saved_event is not None
    assert correction.saved_event.event_type is ProductionEventType.CORRECTION

    rework = context.controller.create_draft(
        context.product_id,
        _form(
            40,
            event_type=ProductionEventType.REWORK,
            description="Возврат на доработку",
            key="rework",
        ),
    )
    context.controller.confirm_draft(rework.id or 0)
    assert context.controller.state(context.product_id).readiness_percent == 40


def test_cancelled_correction_does_not_supersede_original(context: UiContext) -> None:
    original = _confirmed(context, 35, "cancel-source")
    dialog = ProductionEventDialog(
        context.controller,
        context.controller.product(context.product_id),
        source_event=original,
    )

    dialog.reject()

    assert context.controller.events.get_event(original.id or 0).status is ProductionEventStatus.CONFIRMED
    assert len(context.controller.timeline(context.product_id)) == 1


def test_missing_photo_is_presented_without_crashing(context: UiContext) -> None:
    class BrokenController:
        def attachment_bytes(self, _attachment_id: int) -> bytes:
            raise FileNotFoundError("Файл удален")

    viewer = ProductionPhotoViewer(BrokenController(), [])  # type: ignore[arg-type]
    viewer._show_current()

    assert viewer.image_label.text() == "Фотографии отсутствуют"


def test_production_ui_has_no_persistence_or_workbot_imports() -> None:
    paths = (
        Path("ui/production_controller.py"),
        Path("ui/production_event_dialog.py"),
        Path("ui/production_timeline_widget.py"),
        Path("ui/production_photo_viewer.py"),
        Path("ui/product_production_dialog.py"),
        Path("ui/production_overview_widget.py"),
    )
    forbidden = {"sqlite3", "workbot", "production.event_repository", "production.local_attachment_store"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        assert imports.isdisjoint(forbidden), path


def test_production_overview_uses_projection_and_opens_existing_card(
    context: UiContext,
) -> None:
    _confirmed(context, 45, "overview")
    widget = ProductionOverviewWidget(context.controller)
    opened: list[int] = []
    widget.card_requested.connect(opened.append)

    widget.refresh()

    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 5).text() == "45%"
    assert widget.table.item(0, 6).text() == "История"
    widget.table.doubleClicked.emit(widget.table.model().index(0, 0))
    assert opened == [context.product_id]


def test_production_overview_search_and_filters(context: UiContext) -> None:
    widget = ProductionOverviewWidget(context.controller)
    widget.refresh()

    widget.search.setText("p6-001")
    assert widget.table.rowCount() == 1
    widget.search.setText("несуществующее изделие")
    assert widget.table.rowCount() == 0
    widget.search.clear()
    widget.object_filter.setCurrentIndex(1)
    assert widget.table.rowCount() == 1


def test_production_overview_layout_fits_fhd_and_4k(
    context: UiContext,
    application: QApplication,
) -> None:
    widget = ProductionOverviewWidget(context.controller)
    widget.refresh()
    for width, height in ((1600, 900), (3840, 2160)):
        widget.resize(width, height)
        widget.show()
        application.processEvents()
        assert widget.table.geometry().right() <= widget.rect().right()
        assert widget.open_button.geometry().bottom() <= widget.rect().bottom()
    widget.close()


def test_export_one_preserves_original_bytes_and_hides_storage_key(
    context: UiContext,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(PNG_1X1)
    attachment = context.controller.store_photo(source)
    before = context.production_module.attachments.get_attachment(attachment.id or 0)

    exported = context.controller.export_attachment(
        attachment.id or 0,
        tmp_path / "export" / "Фото шкафа",
    )

    after = context.production_module.attachments.get_attachment(attachment.id or 0)
    assert exported.read_bytes() == PNG_1X1
    assert exported.suffix == ".png"
    assert before == after
    assert attachment.sha256 not in exported.name
    assert attachment.storage_key not in str(exported)


def test_export_duplicate_names_never_overwrite(context: UiContext, tmp_path: Path) -> None:
    source = tmp_path / "duplicate.png"
    source.write_bytes(PNG_1X1)
    attachment = context.controller.store_photo(source)
    requested = tmp_path / "export" / "Одинаковое имя.png"

    first = context.controller.export_attachment(attachment.id or 0, requested)
    second = context.controller.export_attachment(attachment.id or 0, requested)

    assert first != second
    assert first.exists() and second.exists()
    assert second.stem.endswith("_2")


def test_batch_export_writes_multiple_structured_files(
    context: UiContext,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(PNG_1X1)
    second_path.write_bytes(PNG_1X1)
    first = context.controller.store_photo(first_path)
    second = context.controller.store_photo(second_path)
    event = context.controller.create_draft(
        context.product_id,
        _form(55, key="batch-export"),
    )
    context.controller.attach_photo(event.id or 0, first.id or 0, 0)
    context.controller.attach_photo(event.id or 0, second.id or 0, 1)
    confirmed = context.controller.confirm_draft(event.id or 0)
    metadata_before = (
        context.production_module.attachments.get_attachment(first.id or 0),
        context.production_module.attachments.get_attachment(second.id or 0),
    )

    report = context.controller.export_product_photos(
        context.product_id,
        tmp_path / "batch",
    )

    assert report.is_successful
    assert len(report.exported_paths) == 2
    assert all(path.read_bytes() == PNG_1X1 for path in report.exported_paths)
    assert all("2026-08-09" in path.name for path in report.exported_paths)
    assert context.controller.events.get_event(confirmed.id or 0) == confirmed
    assert metadata_before == (
        context.production_module.attachments.get_attachment(first.id or 0),
        context.production_module.attachments.get_attachment(second.id or 0),
    )


def test_batch_export_continues_when_one_original_is_missing(
    context: UiContext,
    tmp_path: Path,
) -> None:
    good_path = tmp_path / "good.png"
    missing_path = tmp_path / "missing.png"
    good_path.write_bytes(PNG_1X1)
    missing_path.write_bytes(PNG_1X1 + b"different")
    good = context.controller.store_photo(good_path)
    missing = context.controller.store_photo(missing_path)
    event = context.controller.create_draft(
        context.product_id,
        _form(None, description="Фотографии", key="partial-export"),
    )
    context.controller.attach_photo(event.id or 0, good.id or 0, 0)
    context.controller.attach_photo(event.id or 0, missing.id or 0, 1)
    context.controller.confirm_draft(event.id or 0)
    missing_file = context.attachment_root / Path(missing.storage_key)
    missing_file.unlink()

    report = context.controller.export_product_photos(
        context.product_id,
        tmp_path / "partial",
    )

    assert len(report.exported_paths) == 1
    assert len(report.failures) == 1
    assert report.failures[0].attachment_id == missing.id
    assert report.exported_paths[0].read_bytes() == PNG_1X1
