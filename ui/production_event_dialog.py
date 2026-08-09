"""Manual observation, rework and correction entry dialog."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable
from uuid import uuid4

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QIntValidator, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from models import ProductItem
from production.models import ProductionEvent, ProductionEventType
from production.projection_models import TimelineAttachment
from ui.production_controller import (
    ProductionEventFormData,
    ProductionUiController,
    production_error_message,
)


logger = logging.getLogger(__name__)


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ProductionEventDialog(QDialog):
    def __init__(
        self,
        controller: ProductionUiController,
        product: ProductItem,
        *,
        event_type: ProductionEventType = ProductionEventType.OBSERVATION,
        source_event: ProductionEvent | None = None,
        source_attachments: tuple[TimelineAttachment, ...] = (),
        decrease_resolver: Callable[[int, int], str] | None = None,
        reason_provider: Callable[[], str | None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.product = product
        self.event_type = (
            ProductionEventType.CORRECTION
            if source_event is not None
            else event_type
        )
        self.source_event = source_event
        self.decrease_resolver = decrease_resolver
        self.reason_provider = reason_provider
        self._idempotency_key = f"manual-ui:{uuid4()}"
        self._draft_event_id: int | None = None
        self._stored_paths: dict[str, int] = {}
        self.saved_event: ProductionEvent | None = None
        self.correction_requested = False
        self.setWindowTitle(self._window_title())
        self.resize(760, 720)
        self.setMinimumSize(680, 620)

        self.mode_note = QLabel(self._mode_note())
        self.mode_note.setWordWrap(True)
        self.mode_note.setObjectName("ProductionModeNote")
        self.product_label = QLabel(self._product_label())
        self.product_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.time_edit = QTimeEdit(QTime.currentTime())
        self.time_edit.setDisplayFormat("HH:mm")
        self.stage_combo = NoWheelComboBox()
        self.stage_combo.addItem("Этап не указан", None)
        self.readiness_edit = QLineEdit()
        self.readiness_edit.setPlaceholderText("Не указана")
        self.readiness_edit.setValidator(QIntValidator(0, 100, self))
        self.description_edit = QPlainTextEdit()
        self.description_edit.setPlaceholderText("Что произошло с изделием")
        self.description_edit.setMinimumHeight(130)
        self.employee_combo = NoWheelComboBox()
        self.employee_combo.addItem("Не указан", None)
        self.photos = QListWidget()
        self.photos.setMinimumHeight(120)
        self.add_photos_button = QPushButton("Добавить фотографии")
        self.remove_photo_button = QPushButton("Убрать из записи")
        self.save_button = QPushButton("Сохранить и подтвердить")
        self.cancel_button = QPushButton("Отмена")
        for button in (
            self.add_photos_button,
            self.remove_photo_button,
            self.save_button,
            self.cancel_button,
        ):
            button.setAutoDefault(False)
            button.setDefault(False)

        self._fill_reference_data()
        self._load_source(source_attachments)
        self._build_layout()
        self._connect()

    def _build_layout(self) -> None:
        date_time = QWidget()
        date_time_layout = QHBoxLayout(date_time)
        date_time_layout.setContentsMargins(0, 0, 0, 0)
        date_time_layout.addWidget(self.date_edit, 2)
        date_time_layout.addWidget(self.time_edit, 1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.addRow("Изделие", self.product_label)
        form.addRow("Дата и время", date_time)
        form.addRow("Этап", self.stage_combo)
        form.addRow("Готовность, %", self.readiness_edit)
        form.addRow("Описание", self.description_edit)
        form.addRow("Сообщил сотрудник", self.employee_combo)

        photo_buttons = QHBoxLayout()
        photo_buttons.addWidget(self.add_photos_button)
        photo_buttons.addWidget(self.remove_photo_button)
        photo_buttons.addStretch()

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.save_button)
        actions.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.mode_note)
        layout.addLayout(form)
        layout.addWidget(QLabel("Фотографии"))
        layout.addWidget(self.photos, 1)
        layout.addLayout(photo_buttons)
        layout.addLayout(actions)

    def _connect(self) -> None:
        self.add_photos_button.clicked.connect(self._choose_photos)
        self.remove_photo_button.clicked.connect(self._remove_selected_photo)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)

    def _fill_reference_data(self) -> None:
        source_stage_id = self.source_event.stage_id if self.source_event else None
        stage_ids = set()
        for stage in self.controller.active_stages():
            self.stage_combo.addItem(stage.name, stage.id)
            stage_ids.add(stage.id)
        if source_stage_id is not None and source_stage_id not in stage_ids:
            source_item = next(
                (
                    item
                    for item in self.controller.timeline(self.product.id or 0, include_audit=True)
                    if item.event.id == self.source_event.id
                ),
                None,
            )
            if source_item and source_item.stage:
                self.stage_combo.addItem(
                    f"{source_item.stage.name} (отключен)",
                    source_item.stage.id,
                )
        for employee in self.controller.employees_for_reporting():
            if employee.id is not None:
                self.employee_combo.addItem(employee.full_name, employee.id)

    def _load_source(
        self,
        source_attachments: tuple[TimelineAttachment, ...],
    ) -> None:
        if self.source_event is None:
            return
        local = self.controller.utc_to_local(self.source_event.observed_at_utc)
        self.date_edit.setDate(QDate(local.year, local.month, local.day))
        self.time_edit.setTime(QTime(local.hour, local.minute))
        self._select_data(self.stage_combo, self.source_event.stage_id)
        if self.source_event.readiness_percent is not None:
            self.readiness_edit.setText(str(self.source_event.readiness_percent))
        self.description_edit.setPlainText(self.source_event.description)
        self._select_data(
            self.employee_combo,
            self.source_event.reported_by_employee_id,
        )
        for item in source_attachments:
            self._add_existing_attachment(
                item.attachment.id or 0,
                item.attachment.original_name,
            )

    def _choose_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Добавить фотографии",
            "",
            "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff);;Все файлы (*.*)",
        )
        existing = {
            self.photos.item(row).data(Qt.ItemDataRole.UserRole).get("path")
            for row in range(self.photos.count())
        }
        for path in paths:
            if path not in existing:
                item = QListWidgetItem(Path(path).name)
                item.setToolTip(path)
                item.setData(Qt.ItemDataRole.UserRole, {"path": path})
                self.photos.addItem(item)

    def _add_existing_attachment(self, attachment_id: int, name: str) -> None:
        item = QListWidgetItem(f"{name} (из исходной записи)")
        item.setData(Qt.ItemDataRole.UserRole, {"attachment_id": attachment_id})
        self.photos.addItem(item)

    def _remove_selected_photo(self) -> None:
        row = self.photos.currentRow()
        if row >= 0:
            self.photos.takeItem(row)

    def _save(self) -> None:
        try:
            data = self._form_data()
            resolution = self.controller.requires_readiness_resolution(
                self.product.id or 0,
                data.event_type,
                data.readiness_percent,
            )
            if resolution is not None:
                data = self._resolve_decrease(data, *resolution)
                if self.correction_requested:
                    self.reject()
                    return
            if not self._has_meaningful_content(data):
                raise ValueError("Укажите этап, готовность, описание или добавьте фотографию")
            event = self.controller.create_draft(
                self.product.id or 0,
                data,
                source_event_id=self.source_event.id if self.source_event else None,
            )
            self._draft_event_id = event.id
            self._lock_form()
            self._store_and_attach_photos(event.id or 0)
            self.saved_event = self.controller.confirm_draft(event.id or 0)
            self.accept()
        except Exception as exc:
            logger.exception("Не удалось сохранить production event")
            self._show_error(production_error_message(exc))

    def _form_data(self) -> ProductionEventFormData:
        readiness_text = self.readiness_edit.text().strip()
        readiness = int(readiness_text) if readiness_text else None
        observed = self.controller.local_to_utc(
            self.date_edit.date().toPython(),
            self.time_edit.time().toPython(),
        )
        return ProductionEventFormData(
            observed_at_utc=observed,
            event_type=self.event_type,
            stage_id=self.stage_combo.currentData(),
            readiness_percent=readiness,
            description=self.description_edit.toPlainText().strip(),
            reported_by_employee_id=self.employee_combo.currentData(),
            change_reason=(
                "Исправление подтвержденной записи"
                if self.source_event is not None
                else ""
            ),
            idempotency_key=self._idempotency_key,
        )

    def _resolve_decrease(
        self,
        data: ProductionEventFormData,
        current: int,
        new: int,
    ) -> ProductionEventFormData:
        choice = (
            self.decrease_resolver(current, new)
            if self.decrease_resolver
            else self._ask_decrease_resolution(current, new)
        )
        if choice == "rework":
            return replace(data, event_type=ProductionEventType.REWORK)
        if choice == "correction":
            self.correction_requested = True
            return data
        if choice == "observation":
            reason = self.reason_provider() if self.reason_provider else self._ask_reason()
            if not reason:
                raise ValueError("Для подтверждения снижения укажите причину")
            return replace(data, change_reason=reason.strip())
        raise ValueError("Сохранение отменено")

    def _ask_decrease_resolution(self, current: int, new: int) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("Снижение готовности")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Новое значение готовности ниже текущего ({current}% → {new}%).")
        rework = box.addButton("Возврат / переработка", QMessageBox.ButtonRole.ActionRole)
        correction = box.addButton("Исправить прежнюю запись", QMessageBox.ButtonRole.ActionRole)
        observation = box.addButton("Новое наблюдение", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {
            rework: "rework",
            correction: "correction",
            observation: "observation",
        }.get(box.clickedButton(), "cancel")

    def _ask_reason(self) -> str | None:
        value, accepted = QInputDialog.getMultiLineText(
            self,
            "Причина снижения",
            "Почему новая оценка готовности ниже текущей?",
        )
        return value if accepted else None

    def _store_and_attach_photos(self, event_id: int) -> None:
        for order in range(self.photos.count()):
            data = self.photos.item(order).data(Qt.ItemDataRole.UserRole)
            attachment_id = data.get("attachment_id")
            path = data.get("path")
            if attachment_id is None and path:
                attachment_id = self._stored_paths.get(path)
                if attachment_id is None:
                    attachment = self.controller.store_photo(path)
                    attachment_id = attachment.id
                    if attachment_id is None:
                        raise ValueError("Не удалось зарегистрировать фотографию")
                    self._stored_paths[path] = attachment_id
            if attachment_id is not None:
                self.controller.attach_photo(event_id, attachment_id, order)

    def _lock_form(self) -> None:
        for widget in (
            self.date_edit,
            self.time_edit,
            self.stage_combo,
            self.readiness_edit,
            self.description_edit,
            self.employee_combo,
            self.add_photos_button,
            self.remove_photo_button,
        ):
            widget.setEnabled(False)

    def _has_meaningful_content(self, data: ProductionEventFormData) -> bool:
        return bool(
            data.stage_id is not None
            or data.readiness_percent is not None
            or data.description
            or self.photos.count()
        )

    def _window_title(self) -> str:
        if self.source_event is not None:
            return "Исправление производственной записи"
        if self.event_type is ProductionEventType.REWORK:
            return "Возврат / переработка"
        return "Новая запись о производстве"

    def _mode_note(self) -> str:
        if self.source_event is not None:
            return (
                "Будет создана новая исправленная запись. Исходная запись "
                "останется в истории."
            )
        if self.event_type is ProductionEventType.REWORK:
            return "Зафиксируйте фактический возврат изделия или переработку."
        return "Зафиксируйте фактическое наблюдение о ходе производства."

    def _product_label(self) -> str:
        serial = f" · зав. № {self.product.serial_number}" if self.product.serial_number else ""
        return f"{self.product.name}{serial}"

    @staticmethod
    def _select_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Запись производства", message)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            event.accept()
            return
        super().keyPressEvent(event)
