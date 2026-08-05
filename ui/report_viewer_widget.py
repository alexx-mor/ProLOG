"""Work log report viewer with filters."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hours import format_hours
from models import DirectoryItem, Employee, WorkLogEntry
from ui.period_filter import PeriodFilterWidget


class ReportViewerWidget(QWidget):
    filters_changed = Signal()
    entry_open_requested = Signal(int)
    entry_delete_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._employees: list[Employee] = []
        self._objects: list[DirectoryItem] = []
        self.title = QLabel("Просмотр отчетов")
        self.title.setObjectName("SectionTitle")
        self.employee = QComboBox()
        self.object = QComboBox()
        self.period = PeriodFilterWidget()
        self.apply_button = QPushButton("Показать")
        self.clear_button = QPushButton("Сбросить")
        self.delete_button = QPushButton("Удалить выбранную")
        self.delete_button.setEnabled(False)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Дата", "Сотрудник", "Объект", "Изделие", "Вид работ", "Описание", "Часы", "Комментарий"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for combo in (self.employee, self.object):
            combo.setView(QListView())
        self._build_layout()
        self._connect()
        self._fit_columns()

    def set_employees(self, employees: list[Employee]) -> None:
        current_id = self.employee_id()
        self._employees = employees
        self.employee.blockSignals(True)
        self.employee.clear()
        self.employee.addItem("Все сотрудники", None)
        for employee in employees:
            self.employee.addItem(employee.full_name, employee.id)
        self._select_combo_value(self.employee, current_id)
        self.employee.blockSignals(False)

    def set_objects(self, objects: list[DirectoryItem]) -> None:
        current_id = self.object_id()
        self._objects = objects
        self.object.blockSignals(True)
        self.object.clear()
        self.object.addItem("Все объекты", None)
        for item in objects:
            self.object.addItem(item.name, item.id)
        self._select_combo_value(self.object, current_id)
        self.object.blockSignals(False)

    def set_current_employee(self, employee: Employee | None) -> None:
        self._select_combo_value(self.employee, employee.id if employee else None)

    def employee_id(self) -> int | None:
        return self.employee.currentData()

    def object_id(self) -> int | None:
        return self.object.currentData()

    def date_from_value(self) -> date | None:
        return self.period.date_from_value()

    def date_to_value(self) -> date | None:
        return self.period.date_to_value()

    def set_entries(self, entries: list[WorkLogEntry]) -> None:
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.work_date.strftime("%d.%m.%Y"),
                entry.employee_name,
                entry.object_name,
                entry.product_name,
                entry.work_type_name,
                entry.description,
                format_hours(entry.hours),
                entry.comment,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(256, entry.id)
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
        self._fit_columns()

    def _build_layout(self) -> None:
        filters_group = QGroupBox()
        form = QFormLayout(filters_group)
        form.addRow("Сотрудник", self.employee)
        form.addRow("Объект", self.object)
        form.addRow("Период", self.period)
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.title)
        layout.addWidget(filters_group)
        layout.addLayout(buttons)
        layout.addWidget(self.table)

    def _connect(self) -> None:
        self.apply_button.clicked.connect(self.filters_changed)
        self.clear_button.clicked.connect(self._clear_filters)
        self.employee.currentIndexChanged.connect(lambda _index: self.filters_changed.emit())
        self.object.currentIndexChanged.connect(lambda _index: self.filters_changed.emit())
        self.table.doubleClicked.connect(self._open_selected_entry)
        self.table.itemSelectionChanged.connect(
            lambda: self.delete_button.setEnabled(self._selected_entry_id() is not None)
        )
        self.delete_button.clicked.connect(self._delete_selected_entry)

    def _clear_filters(self) -> None:
        self.employee.setCurrentIndex(0)
        self.object.setCurrentIndex(0)
        self.period.clear()
        self.filters_changed.emit()

    def _select_combo_value(self, combo: QComboBox, value: int | None) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _open_selected_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is not None:
            self.entry_open_requested.emit(entry_id)

    def _delete_selected_entry(self) -> None:
        entry_id = self._selected_entry_id()
        if entry_id is not None:
            self.entry_delete_requested.emit(entry_id)

    def _selected_entry_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        entry_id = item.data(256) if item else None
        return int(entry_id) if entry_id else None

    def _fit_columns(self) -> None:
        widths = [90, 180, 150, 160, 180, 280, 70, 220]
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
