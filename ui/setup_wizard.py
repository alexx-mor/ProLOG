"""First-run setup wizard."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDialog,
)

import excel_export
from services import DirectoryService, EmployeeService
from ui.dialogs import EmployeeDialog, ObjectDialog, PositionDialog, TextInputDialog


class InitialSetupDialog(QDialog):
    def __init__(self, employees: EmployeeService, directories: DirectoryService, department: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Мастер настройки ProLOG")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(940, 620)
        self.employees = employees
        self.directories = directories
        self.department = department
        self.pages = QStackedWidget()
        self.employee_page = _EmployeeSetupPage(employees, directories, self)
        self.position_page = _DirectorySetupPage(directories, "positions", "Должности", self)
        self.work_type_page = _DirectorySetupPage(directories, "work_types", "Виды работ", self)
        self.object_page = _DirectorySetupPage(directories, "objects", "Объекты", self)
        self.finish_page = _FinishPage()
        for page in (self.employee_page, self.position_page, self.work_type_page, self.object_page, self.finish_page):
            self.pages.addWidget(page)

        self.back_button = QPushButton("Назад")
        self.next_button = QPushButton("Далее")
        self.finish_button = QPushButton("Готово")
        for button in (self.back_button, self.next_button, self.finish_button):
            button.setMinimumWidth(118)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        buttons.addWidget(self.finish_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)
        layout.addWidget(self.pages)
        layout.addLayout(buttons)
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.finish_button.clicked.connect(self.accept)
        self._sync_buttons()

    def reject(self) -> None:
        super().reject()

    def _back(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._sync_buttons()

    def _next(self) -> None:
        self.pages.setCurrentIndex(min(self.pages.count() - 1, self.pages.currentIndex() + 1))
        refresh = getattr(self._current_page(), "refresh", None)
        if refresh:
            refresh()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        index = self.pages.currentIndex()
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < self.pages.count() - 1)
        self.finish_button.setVisible(index == self.pages.count() - 1)

    def _current_page(self):
        return self.pages.currentWidget()

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Мастер настройки ProLOG")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class _EmployeeSetupPage(QWidget):
    def __init__(self, employees: EmployeeService, directories: DirectoryService, parent=None) -> None:
        super().__init__(parent)
        self.employees = employees
        self.directories = directories
        title = QLabel("Добро пожаловать в ProLOG")
        title.setObjectName("WizardTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(
            "Для начала работы добавьте сотрудников вручную или импортируйте Excel-файл "
            "с колонками: №, ФИО, Должность, Разряд. Позже сотрудников можно будет "
            "добавлять, корректировать и удалять в основном окне."
        )
        text.setObjectName("WizardSubtitle")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ФИО", "Должность", "Разряд/категория"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 110)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        import_button = QPushButton("Импорт Excel")
        add_button = QPushButton("Добавить вручную")
        import_button.clicked.connect(self._import_excel)
        add_button.clicked.connect(self._add_employee)
        buttons = QHBoxLayout()
        buttons.addWidget(import_button)
        buttons.addWidget(add_button)
        buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 12)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(text)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        employees = self.employees.list()
        self.table.setRowCount(len(employees))
        for row, employee in enumerate(employees):
            values = [employee.full_name, employee.position, employee.category]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)

    def _import_excel(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Импорт сотрудников", "", "Excel (*.xlsx)")
        if not file_name:
            return
        try:
            excel_export.import_employees(file_name, self.employees)
        except Exception as exc:
            self._info(str(exc))
            return
        self.refresh()

    def _add_employee(self) -> None:
        dialog = EmployeeDialog(self.directories.list("positions"), parent=self)
        if dialog.exec():
            try:
                self.employees.save(dialog.employee())
            except ValueError as exc:
                self._info(str(exc))
                return
            self.refresh()

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Сотрудники")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class _DirectorySetupPage(QWidget):
    def __init__(self, directories: DirectoryService, key: str, title: str, parent=None) -> None:
        super().__init__(parent)
        self.directories = directories
        self.key = key
        self.items = []
        header = QLabel(title)
        header.setObjectName("DialogTitle")
        note = QLabel("Проверьте активные элементы справочника. Двойной клик по строке открывает редактирование.")
        note.setWordWrap(True)
        self.table = QTableWidget(0, 3 if key == "positions" else 2)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.add_button = QPushButton("Добавить")
        self.rename_button = QPushButton("Редактировать")
        self.delete_button = QPushButton("Удалить")
        self.activate_button = QPushButton("Активировать")
        self.disable_button = QPushButton("Отключить")
        buttons = QHBoxLayout()
        for button in (self.add_button, self.rename_button, self.delete_button, self.activate_button, self.disable_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 12)
        layout.setSpacing(10)
        layout.addWidget(header)
        layout.addWidget(note)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self._add)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button.clicked.connect(self._delete)
        self.activate_button.clicked.connect(lambda: self._set_active(True))
        self.disable_button.clicked.connect(lambda: self._set_active(False))
        self.table.doubleClicked.connect(self._toggle)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.refresh()

    def refresh(self) -> None:
        self.items = self.directories.list_all(self.key)
        if self.key == "positions":
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(
                ["Должность", "Категории/разряды", "Ученик/стажер", "Группа", "Статус"]
            )
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, 400)
            self.table.setColumnWidth(1, 170)
            self.table.setColumnWidth(2, 140)
            self.table.setColumnWidth(3, 120)
            self.table.setColumnWidth(4, 100)
        else:
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Название", "Статус"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(1, 140)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            name = QTableWidgetItem(item.name)
            name.setToolTip(item.name)
            self.table.setItem(row, 0, name)
            if self.key == "positions":
                category = QTableWidgetItem(item.category)
                category.setToolTip(item.category)
                student = QTableWidgetItem("Да" if item.student_allowed else "Нет")
                student.setToolTip(student.text())
                group = QTableWidgetItem(item.group)
                group.setToolTip(group.text())
                self.table.setItem(row, 1, category)
                self.table.setItem(row, 2, student)
                self.table.setItem(row, 3, group)
            status_column = 4 if self.key == "positions" else 1
            self.table.setItem(row, status_column, _status_item(item.is_active))
            if not item.is_active:
                self._mark_disabled_row(row)

    def _selected(self):
        row = self.table.currentRow()
        return self.items[row] if 0 <= row < len(self.items) else None

    def _position_groups(self, current_group: str = ""):
        groups = list(self.directories.list("employee_groups"))
        normalized = current_group.strip().casefold()
        if normalized and all(getattr(group, "name", str(group)).casefold() != normalized for group in groups):
            groups.append(current_group.strip())
        return groups

    def _add(self) -> None:
        if self.key == "positions":
            dialog = PositionDialog(parent=self, groups=self._position_groups())
            if dialog.exec():
                name, category, student_allowed, salary, salary_type, group, is_active = dialog.values()
                item_id = self.directories.ensure(self.key, name)
                if item_id is not None:
                    self.directories.update_position_details(item_id, name, category, student_allowed, salary, salary_type, group)
                    self.directories.set_active(self.key, item_id, is_active)
                self.refresh()
            return
        if self.key == "objects":
            dialog = ObjectDialog(parent=self)
            if dialog.exec():
                values = dialog.values()
                item_id = self.directories.ensure(self.key, values[0])
                if item_id is not None:
                    self.directories.update_object_details(item_id, *values)
                self.refresh()
            return
        dialog = TextInputDialog("Добавить", "Название", parent=self)
        if dialog.exec() and dialog.value().strip():
            self.directories.ensure(self.key, dialog.value())
            self.refresh()

    def _rename(self) -> None:
        item = self._selected()
        if not item:
            return
        if self.key == "positions":
            dialog = PositionDialog(item, self, groups=self._position_groups(item.group))
            if dialog.exec():
                name, category, student_allowed, salary, salary_type, group, is_active = dialog.values()
                self.directories.update_position_details(item.id, name, category, student_allowed, salary, salary_type, group)
                self.directories.set_active(self.key, item.id, is_active)
                self.refresh()
            return
        if self.key == "objects":
            dialog = ObjectDialog(item, self)
            if dialog.exec():
                self.directories.update_object_details(item.id, *dialog.values())
                self.refresh()
            return
        dialog = TextInputDialog("Переименовать", "Название", item.name, self)
        if dialog.exec() and dialog.value().strip():
            self.directories.rename(self.key, item.id, dialog.value())
            self.refresh()

    def _delete(self) -> None:
        item = self._selected()
        if not item:
            return
        try:
            self.directories.delete(self.key, item.id)
        except ValueError as exc:
            self._info(str(exc))
            return
        self.refresh()

    def _set_active(self, active: bool) -> None:
        item = self._selected()
        if item:
            self.directories.set_active(self.key, item.id, active)
            self.refresh()

    def _toggle(self) -> None:
        item = self._selected()
        if item:
            self._rename()

    def _context_menu(self, position) -> None:
        menu = QMenu(self)
        actions = [
            ("Добавить", self._add),
            ("Редактировать", self._rename),
            ("Удалить", self._delete),
            ("Активировать", lambda: self._set_active(True)),
            ("Отключить", lambda: self._set_active(False)),
        ]
        for label, callback in actions:
            action = QAction(label, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _mark_disabled_row(self, row: int) -> None:
        background = QColor("#edf0f3")
        foreground = QColor("#6f7882")
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item:
                item.setBackground(background)
                item.setForeground(foreground)

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Справочник")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class _FinishPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        title = QLabel("Настройка завершена")
        title.setObjectName("DialogTitle")
        text = QLabel(
            "ProLOG готов к ежедневной работе. Вы можете вернуться к справочникам, "
            "сотрудникам и авторизации через меню программы. Приятного пользования!"
        )
        text.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 12)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(text)
        layout.addStretch()

    def refresh(self) -> None:
        return


def _status_item(is_active: bool) -> QTableWidgetItem:
    item = QTableWidgetItem("Активен" if is_active else "Отключен")
    item.setIcon(_status_icon("#16833a" if is_active else "#c62828"))
    item.setToolTip(item.text())
    return item


def _status_icon(color: str) -> QIcon:
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, 12, 12)
    painter.end()
    return QIcon(pixmap)
