"""Timeline presentation for immutable production facts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from production.models import ProductionEventStatus, ProductionEventType
from production.projection_models import ProductionTimelineItem


EVENT_TYPE_LABELS = {
    ProductionEventType.OBSERVATION: "Наблюдение",
    ProductionEventType.BASELINE: "Исходное состояние",
    ProductionEventType.CORRECTION: "Исправление",
    ProductionEventType.REWORK: "Возврат / переработка",
}

STATUS_LABELS = {
    ProductionEventStatus.DRAFT: "Черновик",
    ProductionEventStatus.READY: "Готово к подтверждению",
    ProductionEventStatus.CONFIRMED: "Подтверждено",
    ProductionEventStatus.REJECTED: "Отклонено",
    ProductionEventStatus.SUPERSEDED: "Исправлено",
}


class ProductionTimelineWidget(QWidget):
    """Compact, deterministic timeline table with explicit actions."""

    audit_changed = Signal(bool)
    details_requested = Signal(int)
    correction_requested = Signal(int)
    photos_requested = Signal(int)

    def __init__(
        self,
        localize_datetime: Callable[[datetime], datetime] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.localize_datetime = localize_datetime or (lambda value: value)
        self._items: list[ProductionTimelineItem] = []
        self.empty_label = QLabel(
            "История производства пока пуста. Добавьте первое наблюдение."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("WizardSubtitle")
        self.empty_label.setMinimumHeight(110)
        self.audit_checkbox = QCheckBox("Показать служебные записи")
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "Дата и время",
                "Тип",
                "Этап",
                "Готовность",
                "Описание",
                "Автор / источник",
                "Сообщил",
                "Подтвердил",
                "Фото",
                "Статус",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self.table.setColumnWidth(0, 135)
        self.table.setColumnWidth(1, 145)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 95)
        self.table.setColumnWidth(4, 310)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 155)
        self.table.setColumnWidth(7, 155)
        self.table.setColumnWidth(8, 65)
        self.table.setColumnWidth(9, 155)

        self.details_button = QPushButton("Подробнее")
        self.correct_button = QPushButton("Исправить запись")
        self.photos_button = QPushButton("Открыть фотографии")
        self._update_actions()

        top = QHBoxLayout()
        top.addWidget(self.audit_checkbox)
        top.addStretch()
        actions = QHBoxLayout()
        actions.addWidget(self.details_button)
        actions.addWidget(self.correct_button)
        actions.addWidget(self.photos_button)
        actions.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)

        self.audit_checkbox.toggled.connect(self.audit_changed)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.doubleClicked.connect(self._emit_details)
        self.details_button.clicked.connect(self._emit_details)
        self.correct_button.clicked.connect(self._emit_correction)
        self.photos_button.clicked.connect(self._emit_photos)

    def set_items(self, items: Iterable[ProductionTimelineItem]) -> None:
        selected_id = self.selected_event_id()
        self._items = list(reversed(list(items)))
        self.table.setRowCount(len(self._items))
        for row, timeline_item in enumerate(self._items):
            self._populate_row(row, timeline_item)
        has_items = bool(self._items)
        self.empty_label.setVisible(not has_items)
        self.table.setVisible(has_items)
        if selected_id is not None:
            self.select_event(selected_id)
        elif has_items:
            self.table.selectRow(0)
        self._update_actions()

    def selected_item(self) -> ProductionTimelineItem | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def selected_event_id(self) -> int | None:
        item = self.selected_item()
        return item.event.id if item is not None else None

    def select_event(self, event_id: int) -> None:
        for row, item in enumerate(self._items):
            if item.event.id == event_id:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def _populate_row(self, row: int, item: ProductionTimelineItem) -> None:
        event = item.event
        observed = self.localize_datetime(event.observed_at_utc)
        readiness = (
            f"{event.readiness_percent}%"
            if event.readiness_percent is not None
            else "Не указана"
        )
        values = (
            observed.strftime("%d.%m.%Y %H:%M"),
            self._event_type_label(item),
            item.stage.name if item.stage else "Не указан",
            readiness,
            event.description or "",
            f"{event.created_by.display_name} · {self._source_label(item)}",
            item.reported_employee_name or "Не указан",
            event.confirmed_by.display_name if event.confirmed_by else "Не подтверждено",
            str(len(item.attachments)),
            self._status_label(item),
        )
        for column, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setToolTip(value or "Нет данных")
            cell.setData(Qt.ItemDataRole.UserRole, event.id)
            if event.status is ProductionEventStatus.SUPERSEDED:
                cell.setForeground(QColor("#7a838c"))
                cell.setBackground(QColor("#f1f3f5"))
            elif event.event_type is ProductionEventType.REWORK:
                cell.setBackground(QColor("#fff3e3"))
            elif event.event_type is ProductionEventType.CORRECTION:
                cell.setBackground(QColor("#eef5fb"))
            self.table.setItem(row, column, cell)

    @staticmethod
    def _source_label(item: ProductionTimelineItem) -> str:
        source = item.event.source_type.value
        return {
            "manual": "Вручную",
            "integration": "Интеграция",
            "import": "Импорт",
            "system": "Система",
        }.get(source, source)

    def _event_type_label(self, item: ProductionTimelineItem) -> str:
        label = EVENT_TYPE_LABELS.get(item.event.event_type, str(item.event.event_type))
        if item.event.event_type is ProductionEventType.CORRECTION:
            source = self._find_item(item.event.supersedes_event_id)
            if source is not None:
                date_text = self.localize_datetime(
                    source.event.observed_at_utc
                ).strftime("%d.%m.%Y %H:%M")
                return f"{label} записи от {date_text}"
        return label

    def _status_label(self, item: ProductionTimelineItem) -> str:
        label = STATUS_LABELS.get(item.event.status, str(item.event.status))
        if item.event.status is ProductionEventStatus.SUPERSEDED:
            replacement = self._find_item(item.superseded_by_event_id)
            if replacement is not None:
                date_text = self.localize_datetime(
                    replacement.event.observed_at_utc
                ).strftime("%d.%m.%Y %H:%M")
                return f"Исправлено записью от {date_text}"
        return label

    def _find_item(self, event_id: int | None) -> ProductionTimelineItem | None:
        return next(
            (item for item in self._items if item.event.id == event_id),
            None,
        )

    def _update_actions(self) -> None:
        item = self.selected_item()
        has_item = item is not None and item.event.id is not None
        self.details_button.setEnabled(has_item)
        self.photos_button.setEnabled(has_item and bool(item.attachments))
        self.correct_button.setEnabled(
            has_item and item.event.status is ProductionEventStatus.CONFIRMED
        )

    def _emit_details(self) -> None:
        event_id = self.selected_event_id()
        if event_id is not None:
            self.details_requested.emit(event_id)

    def _emit_correction(self) -> None:
        event_id = self.selected_event_id()
        if event_id is not None:
            self.correction_requested.emit(event_id)

    def _emit_photos(self) -> None:
        event_id = self.selected_event_id()
        if event_id is not None:
            self.photos_requested.emit(event_id)
