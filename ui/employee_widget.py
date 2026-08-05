"""Employee directory widget."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import DirectoryItem, Employee


class EmployeeWidget(QWidget):
    selected = Signal(object)
    add_requested = Signal()
    edit_requested = Signal(object)
    delete_requested = Signal(object)
    import_requested = Signal()
    export_requested = Signal()
    search_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._employees: list[Employee] = []
        self._management_enabled = True
        self.title = QLabel("Сотрудники")
        self.title.setObjectName("SectionTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти сотрудника")
        self.group_filter = QComboBox()
        self.group_filter.addItem("Все группы", "")
        self.group_filter.setView(QListView())
        self.position_filter = QComboBox()
        self.position_filter.addItem("Все должности", "")
        self.position_filter.setView(QListView())
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ФИО", "Должность", "Разряд/категория"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, 120)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.add_button = QPushButton("Добавить")
        self.edit_button = QPushButton("Редактировать")
        self.delete_button = QPushButton("Удалить")
        self.import_button = QPushButton("Импорт Excel")
        self.export_button = QPushButton("Экспорт сотрудников")
        self._columns_fitted = False
        self._adjusting_columns = False
        self._manual_column_widths = False

        button_layout = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.delete_button, self.import_button, self.export_button):
            button_layout.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.title)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        filters.addWidget(self.group_filter)
        filters.addWidget(self.position_filter)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        layout.addLayout(button_layout)

        self.search.textChanged.connect(self.search_changed)
        self.group_filter.currentIndexChanged.connect(lambda _index: self.search_changed.emit(self.search.text()))
        self.position_filter.currentIndexChanged.connect(lambda _index: self.search_changed.emit(self.search.text()))
        self.table.itemSelectionChanged.connect(self._emit_selected)
        self.table.doubleClicked.connect(lambda: self._emit_edit())
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().sectionResized.connect(self._column_resized)
        self.add_button.clicked.connect(self.add_requested)
        self.edit_button.clicked.connect(self._emit_edit)
        self.delete_button.clicked.connect(self._emit_delete)
        self.import_button.clicked.connect(self.import_requested)
        self.export_button.clicked.connect(self.export_requested)

    def set_employees(self, employees: list[Employee]) -> None:
        self._employees = employees
        self.table.setRowCount(len(employees))
        for row, employee in enumerate(employees):
            values = [
                employee.full_name,
                employee.position,
                employee.category,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(256, employee.id)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        if not self._columns_fitted:
            self._fit_columns_to_width()
            self._columns_fitted = True

    def set_position_filter_options(self, positions) -> None:
        current = self.position_filter.currentData()
        self.position_filter.blockSignals(True)
        self.position_filter.clear()
        self.position_filter.addItem("Все должности", "")
        for position in positions:
            self.position_filter.addItem(position.name, position.name)
        index = self.position_filter.findData(current)
        self.position_filter.setCurrentIndex(index if index >= 0 else 0)
        self.position_filter.blockSignals(False)

    def set_group_filter_options(self, groups: list[DirectoryItem]) -> None:
        current = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("Все группы", "")
        for group in groups:
            self.group_filter.addItem(group.name, group.name)
        index = self.group_filter.findData(current)
        self.group_filter.setCurrentIndex(index if index >= 0 else 0)
        self.group_filter.blockSignals(False)

    def set_management_enabled(self, enabled: bool) -> None:
        self._management_enabled = enabled
        for button in (self.add_button, self.edit_button, self.delete_button, self.import_button, self.export_button):
            button.setVisible(enabled)

    def current_position_filter(self) -> str:
        return str(self.position_filter.currentData() or "")

    def current_group_filter(self) -> str:
        return str(self.group_filter.currentData() or "")

    def current_employee(self) -> Employee | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._employees):
            return None
        return self._employees[row]

    def select_employee(self, employee_id: int) -> None:
        for row, employee in enumerate(self._employees):
            if employee.id == employee_id:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def column_widths(self) -> list[int]:
        return [self.table.columnWidth(column) for column in range(self.table.columnCount())]

    def apply_column_widths(self, widths: list[int]) -> None:
        effective_widths = widths[1:] if len(widths) == 4 else widths
        applied = False
        self._adjusting_columns = True
        for column, width in enumerate(effective_widths[: self.table.columnCount()]):
            if width > 0:
                self.table.setColumnWidth(column, width)
                applied = True
        self._adjusting_columns = False
        self._manual_column_widths = applied

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._manual_column_widths:
            self._fit_columns_to_width()

    def _column_resized(self, _column: int, _old_size: int, _new_size: int) -> None:
        if not self._adjusting_columns and self.isVisible():
            self._manual_column_widths = True

    def _emit_selected(self) -> None:
        employee = self.current_employee()
        if employee:
            self.selected.emit(employee)

    def _emit_edit(self) -> None:
        if not self._management_enabled:
            return
        employee = self.current_employee()
        if employee:
            self.edit_requested.emit(employee)

    def _emit_delete(self) -> None:
        if not self._management_enabled:
            return
        employee = self.current_employee()
        if employee:
            self.delete_requested.emit(employee)

    def _show_context_menu(self, position) -> None:
        if not self._management_enabled:
            return
        menu = QMenu(self)
        add_action = QAction("Добавить сотрудника", self)
        edit_action = QAction("Редактировать", self)
        delete_action = QAction("Удалить сотрудника", self)
        import_action = QAction("Импорт из Excel", self)
        export_action = QAction("Экспорт сотрудников", self)
        has_employee = self.current_employee() is not None
        edit_action.setEnabled(has_employee)
        delete_action.setEnabled(has_employee)
        add_action.triggered.connect(self.add_requested)
        edit_action.triggered.connect(self._emit_edit)
        delete_action.triggered.connect(self._emit_delete)
        import_action.triggered.connect(self.import_requested)
        export_action.triggered.connect(self.export_requested)
        menu.addAction(add_action)
        menu.addAction(edit_action)
        menu.addAction(delete_action)
        menu.addSeparator()
        menu.addAction(import_action)
        menu.addAction(export_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _fit_columns_to_width(self) -> None:
        if self.table.columnCount() != 3:
            return
        available_width = max(self.table.viewport().width() - 6, 260)
        category_width = 120
        remaining = max(available_width - category_width, 160)
        self._adjusting_columns = True
        self.table.setColumnWidth(0, int(remaining * 0.55))
        self.table.setColumnWidth(1, remaining - int(remaining * 0.55))
        self.table.setColumnWidth(2, category_width)
        self._adjusting_columns = False
