"""Analytics tab for work log entries."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from analytics import AnalyticsResult, format_money
from models import DirectoryItem, Employee
from ui.worklog_widget import CalendarDateEdit


class AnalyticsWidget(QWidget):
    filters_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.title = QLabel("Аналитика")
        self.title.setObjectName("SectionTitle")
        self.employee = QComboBox()
        self.object = QComboBox()
        self.date_from_enabled = QCheckBox("С даты")
        self.date_to_enabled = QCheckBox("По дату")
        self.date_from = CalendarDateEdit(QDate.currentDate())
        self.date_to = CalendarDateEdit(QDate.currentDate())
        self.apply_button = QPushButton("Рассчитать")
        self.clear_button = QPushButton("Сбросить")
        self.summary_labels = {
            "employees": QLabel("0"),
            "entries": QLabel("0"),
            "hours": QLabel("0"),
            "person_hours": QLabel("0"),
            "payroll": QLabel("0,00 руб."),
        }
        self.object_table = QTableWidget(0, 6)
        self.employee_table = QTableWidget(0, 7)
        self.work_type_table = QTableWidget(0, 5)
        self.date_table = QTableWidget(0, 6)
        self._setup_controls()
        self._build_layout()
        self._connect()

    def set_employees(self, employees: list[Employee]) -> None:
        current_id = self.employee_id()
        self.employee.blockSignals(True)
        self.employee.clear()
        self.employee.addItem("Все сотрудники", None)
        for employee in employees:
            self.employee.addItem(employee.full_name, employee.id)
        self._select_combo_value(self.employee, current_id)
        self.employee.blockSignals(False)

    def set_objects(self, objects: list[DirectoryItem]) -> None:
        current_id = self.object_id()
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
        return self.date_from.date().toPython() if self.date_from_enabled.isChecked() else None

    def date_to_value(self) -> date | None:
        return self.date_to.date().toPython() if self.date_to_enabled.isChecked() else None

    def set_result(self, result: AnalyticsResult) -> None:
        self.summary_labels["employees"].setText(str(result.summary.employees_count))
        self.summary_labels["entries"].setText(str(result.summary.entries_count))
        self.summary_labels["hours"].setText(str(result.summary.total_hours))
        self.summary_labels["person_hours"].setText(str(result.summary.person_hours))
        self.summary_labels["payroll"].setText(format_money(result.summary.payroll))
        self._fill_object_table(result)
        self._fill_employee_table(result)
        self._fill_work_type_table(result)
        self._fill_date_table(result)

    def _setup_controls(self) -> None:
        for combo in (self.employee, self.object):
            combo.setView(QListView())
        for date_edit in (self.date_from, self.date_to):
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("dd.MM.yyyy")
            date_edit.lineEdit().setReadOnly(True)
            date_edit.setEnabled(False)
        self.object_table.setHorizontalHeaderLabels(
            ["Объект", "Сотрудников", "Записей", "Часы", "Чел.-часы", "Зарплата"]
        )
        self.employee_table.setHorizontalHeaderLabels(
            ["Сотрудник", "Должность", "Категория", "Объектов", "Записей", "Часы", "Зарплата"]
        )
        self.work_type_table.setHorizontalHeaderLabels(["Вид работ", "Сотрудников", "Записей", "Часы", "Зарплата"])
        self.date_table.setHorizontalHeaderLabels(["Дата", "Тип дня", "Сотрудников", "Записей", "Часы", "Зарплата"])
        for table in (self.object_table, self.employee_table, self.work_type_table, self.date_table):
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            table.horizontalHeader().setStretchLastSection(True)
        self._fit_columns()

    def _build_layout(self) -> None:
        filters_group = QGroupBox()
        form = QFormLayout(filters_group)
        form.addRow("Сотрудник", self.employee)
        form.addRow("Объект", self.object)
        date_grid = QGridLayout()
        date_grid.addWidget(self.date_from_enabled, 0, 0)
        date_grid.addWidget(self.date_from, 0, 1)
        date_grid.addWidget(self.date_to_enabled, 1, 0)
        date_grid.addWidget(self.date_to, 1, 1)
        form.addRow("Период", date_grid)

        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()

        summary_group = QGroupBox("Сводка")
        summary = QGridLayout(summary_group)
        labels = [
            ("Сотрудников", "employees"),
            ("Записей", "entries"),
            ("Часы", "hours"),
            ("Человеко-часы", "person_hours"),
            ("Зарплата", "payroll"),
        ]
        for column, (title, key) in enumerate(labels):
            title_label = QLabel(title)
            title_label.setObjectName("AnalyticsMetricTitle")
            value_label = self.summary_labels[key]
            value_label.setObjectName("AnalyticsMetricValue")
            summary.addWidget(title_label, 0, column)
            summary.addWidget(value_label, 1, column)

        tables = QTabWidget()
        tables.addTab(self.object_table, "По объектам")
        tables.addTab(self.employee_table, "По сотрудникам")
        tables.addTab(self.work_type_table, "По видам работ")
        tables.addTab(self.date_table, "По датам")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.title)
        layout.addWidget(filters_group)
        layout.addLayout(buttons)
        layout.addWidget(summary_group)
        layout.addWidget(tables, 1)

    def _connect(self) -> None:
        self.apply_button.clicked.connect(self.filters_changed)
        self.clear_button.clicked.connect(self._clear_filters)
        self.date_from_enabled.toggled.connect(self.date_from.setEnabled)
        self.date_to_enabled.toggled.connect(self.date_to.setEnabled)
        self.employee.currentIndexChanged.connect(lambda _index: self.filters_changed.emit())
        self.object.currentIndexChanged.connect(lambda _index: self.filters_changed.emit())

    def _clear_filters(self) -> None:
        self.employee.setCurrentIndex(0)
        self.object.setCurrentIndex(0)
        self.date_from_enabled.setChecked(False)
        self.date_to_enabled.setChecked(False)
        self.filters_changed.emit()

    def _fill_object_table(self, result: AnalyticsResult) -> None:
        self.object_table.setRowCount(len(result.by_object))
        for row, item in enumerate(result.by_object):
            values = [
                item.object_name,
                item.employees_count,
                item.entries_count,
                item.total_hours,
                item.person_hours,
                format_money(item.payroll),
            ]
            self._set_row(self.object_table, row, values)

    def _fill_employee_table(self, result: AnalyticsResult) -> None:
        self.employee_table.setRowCount(len(result.by_employee))
        for row, item in enumerate(result.by_employee):
            values = [
                item.employee_name,
                item.position,
                item.category,
                item.objects_count,
                item.entries_count,
                item.total_hours,
                format_money(item.payroll),
            ]
            self._set_row(self.employee_table, row, values)

    def _fill_work_type_table(self, result: AnalyticsResult) -> None:
        self.work_type_table.setRowCount(len(result.by_work_type))
        for row, item in enumerate(result.by_work_type):
            values = [
                item.work_type_name,
                item.employees_count,
                item.entries_count,
                item.total_hours,
                format_money(item.payroll),
            ]
            self._set_row(self.work_type_table, row, values)

    def _fill_date_table(self, result: AnalyticsResult) -> None:
        self.date_table.setRowCount(len(result.by_date))
        for row, item in enumerate(result.by_date):
            values = [
                item.work_date,
                item.day_type,
                item.employees_count,
                item.entries_count,
                item.total_hours,
                format_money(item.payroll),
            ]
            self._set_row(self.date_table, row, values)

    def _set_row(self, table: QTableWidget, row: int, values: list[object]) -> None:
        for column, value in enumerate(values):
            text = str(value)
            cell = QTableWidgetItem(text)
            cell.setToolTip(text)
            table.setItem(row, column, cell)

    def _select_combo_value(self, combo: QComboBox, value: int | None) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _fit_columns(self) -> None:
        for table, widths in (
            (self.object_table, [240, 110, 90, 80, 100, 140]),
            (self.employee_table, [220, 180, 85, 85, 85, 75, 140]),
            (self.work_type_table, [260, 110, 90, 80, 140]),
            (self.date_table, [100, 190, 110, 90, 80, 140]),
        ):
            for column, width in enumerate(widths):
                table.setColumnWidth(column, width)
