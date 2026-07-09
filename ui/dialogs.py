"""Reusable dialogs."""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListView,
    QMessageBox,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from constants import APP_LOGO_FILE, APP_NAME, APP_VERSION
from directory_files import dictionary_statuses, merge_dictionary_updates
from models import AppSettings, DirectoryItem, Employee, PayRate
from requisites import RequisitesOptions
from services import category_values_from_rule
from update_checker import UpdateChecker


class EmployeeDialog(QDialog):
    def __init__(self, positions, employee: Employee | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сотрудник")
        self.setMinimumWidth(660)
        self.full_name = QLineEdit(employee.full_name if employee else "")
        self.position = QComboBox()
        self.position.setEditable(True)
        self.position.setView(QListView())
        self.position_items = {item.name: item for item in positions}
        self.position.addItem("")
        for item in positions:
            self.position.addItem(item.name)
        if employee and employee.position:
            index = self.position.findText(employee.position)
            if index >= 0:
                self.position.setCurrentIndex(index)
            else:
                self.position.setEditText(employee.position)
        self.category = QComboBox()
        self.category.setEditable(False)
        self._sync_categories(employee.category if employee else "")
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QFormLayout(self)
        layout.addRow("ФИО", self.full_name)
        layout.addRow("Должность", self.position)
        layout.addRow("Категория", self.category)
        layout.addRow(buttons)
        self.position.currentTextChanged.connect(lambda _text: self._sync_categories(self.category.currentText()))

    def employee(self, employee_id: int | None = None) -> Employee:
        return Employee(
            id=employee_id,
            full_name=self.full_name.text(),
            position=self.position.currentText(),
            category=self.category.currentText(),
        )

    def _sync_categories(self, current: str = "") -> None:
        position = self.position_items.get(self.position.currentText().strip())
        allowed = category_values_from_rule(position.category if position else "")
        if position and position.student_allowed:
            allowed = ["0 (студент)", *allowed]
        self.category.blockSignals(True)
        self.category.clear()
        if allowed:
            self.category.addItems(allowed)
            index = self.category.findText(current.strip())
            self.category.setCurrentIndex(index if index >= 0 else 0)
            self.category.setEnabled(True)
        else:
            self.category.addItem("")
            self.category.setEnabled(False)
        self.category.blockSignals(False)


class OrganizationDialog(QDialog):
    def __init__(self, settings: AppSettings, options: RequisitesOptions, required: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Авторизация")
        self.setMinimumWidth(520)
        self.required = required
        self.organization = QComboBox()
        self.department = QComboBox()
        self.leader = QComboBox()
        self._fill_combo(self.organization, options.organizations, settings.organization_name)
        self._fill_combo(self.department, options.departments, settings.department_name)
        self._fill_combo(self.leader, options.leaders, settings.leader_full_name)
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QFormLayout(self)
        layout.addRow("Организация", self.organization)
        layout.addRow("Отдел", self.department)
        layout.addRow("Руководитель", self.leader)
        layout.addRow(buttons)

    def accept(self) -> None:
        if not self.is_complete():
            self._info("Выберите организацию, отдел и руководителя")
            return
        super().accept()

    def reject(self) -> None:
        if self.required:
            self._info("Для работы с программой необходимо пройти авторизацию")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.required and not self.is_complete():
            self._info("Для работы с программой необходимо пройти авторизацию")
            event.ignore()
            return
        super().closeEvent(event)

    def is_complete(self) -> bool:
        return bool(
            self.organization.currentText().strip()
            and self.department.currentText().strip()
            and self.leader.currentText().strip()
        )

    def apply_to(self, settings: AppSettings) -> AppSettings:
        settings.organization_name = self.organization.currentText().strip()
        settings.department_name = self.department.currentText().strip()
        settings.leader_full_name = self.leader.currentText().strip()
        return settings

    def _fill_combo(self, combo: QComboBox, values: list[str], current: str) -> None:
        combo.addItem("")
        combo.addItems(values)
        index = combo.findText(current)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Авторизация")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class PositionDialog(QDialog):
    CATEGORY_RULES = ("—", "1", "1-2", "1-3", "1-4", "1-5", "1-6")
    GROUPS = ("Рабочие", "ИТР")

    def __init__(self, position: DirectoryItem | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Должность")
        self.setMinimumWidth(700)
        self.name = QLineEdit(position.name if position else "")
        self.category = QComboBox()
        self.category.addItems(self.CATEGORY_RULES)
        self.student_allowed = QCheckBox("Разрешить категорию 0 (студент)")
        self.salary = QLineEdit(position.salary if position else "")
        self.salary_type = QComboBox()
        self.salary_type.addItem("Ставка", "hourly")
        self.salary_type.addItem("Зарплата", "monthly")
        self.group = QComboBox()
        self.group.addItems(self.GROUPS)

        if position:
            category = position.category or "—"
            if self.category.findText(category) < 0:
                self.category.addItem(category)
            self.category.setCurrentText(category)
            self.student_allowed.setChecked(position.student_allowed)
            index = self.salary_type.findData(position.salary_type)
            self.salary_type.setCurrentIndex(index if index >= 0 else 0)
            group = position.group or "Рабочие"
            self.group.setCurrentText(group if group in self.GROUPS else "Рабочие")

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QFormLayout(self)
        layout.addRow("Название", self.name)
        layout.addRow("Категории", self.category)
        layout.addRow("", self.student_allowed)
        layout.addRow("Группа", self.group)
        layout.addRow(buttons)

    def values(self) -> tuple[str, str, bool, str, str, str]:
        return (
            self.name.text().strip(),
            self.category.currentText().strip() or "—",
            self.student_allowed.isChecked(),
            self.salary.text().strip(),
            str(self.salary_type.currentData() or "hourly"),
            self.group.currentText().strip() or "Рабочие",
        )

    def accept(self) -> None:
        if not self.name.text().strip():
            self._info("Укажите название должности")
            return
        super().accept()

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Должность")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class PayRateDialog(QDialog):
    def __init__(self, pay_rate: PayRate, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Оплата")
        self.setMinimumWidth(620)
        self.position = QLineEdit(pay_rate.position_name)
        self.position.setReadOnly(True)
        self.category = QLineEdit(pay_rate.category)
        self.category.setReadOnly(True)
        self.salary_type = QComboBox()
        self.salary_type.addItem("Ставка", "hourly")
        self.salary_type.addItem("Зарплата", "monthly")
        index = self.salary_type.findData(pay_rate.salary_type)
        self.salary_type.setCurrentIndex(index if index >= 0 else 0)
        self.salary = QLineEdit(pay_rate.salary)
        self.salary.setPlaceholderText("Например: 450 или 70000")

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QFormLayout(self)
        layout.addRow("Должность", self.position)
        layout.addRow("Категория", self.category)
        layout.addRow("Тип оплаты", self.salary_type)
        layout.addRow("Сумма", self.salary)
        layout.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.salary.text().strip(), str(self.salary_type.currentData() or "hourly")


class DirectoryDialog(QDialog):
    DIRECTORY_LABELS = {
        "locations": "Местонахождения",
        "objects": "Объекты",
        "positions": "Должности",
        "pay_rates": "Оплата",
        "work_types": "Виды работ",
    }

    def __init__(self, directory_service, parent=None, initial_key: str = "locations") -> None:
        super().__init__(parent)
        self.setWindowTitle("Справочники")
        self.resize(980, 520)
        self.directory_service = directory_service
        self.current_key = initial_key if initial_key in self.DIRECTORY_LABELS else "locations"
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(190)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по справочнику")
        self.table = QTableWidget(0, 2)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.add_button = QPushButton("Добавить")
        self.rename_button = QPushButton("Редактировать")
        self.disable_button = QPushButton("Отключить")
        self.restore_button = QPushButton("Активировать")
        self.delete_button = QPushButton("Удалить")
        self.close_button = QPushButton("Закрыть")
        self._items = []
        self._build_layout()
        self._connect()
        self._fill_navigation()
        self.refresh()

    def refresh(self) -> None:
        self._configure_table()
        self._update_button_visibility()
        needle = self.search.text().strip().lower()
        if self.current_key == "pay_rates":
            self._items = [
                item
                for item in self.directory_service.list_pay_rates()
                if not needle or needle in item.position_name.lower() or needle in item.category.lower()
            ]
            self._refresh_pay_rates()
            return
        self._items = [item for item in self.directory_service.list_all(self.current_key) if not needle or needle in item.name.lower()]
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            name = QTableWidgetItem(item.name)
            name.setData(Qt.ItemDataRole.UserRole, item.id)
            name.setToolTip(item.name)
            self.table.setItem(row, 0, name)
            if self.current_key == "positions":
                category = QTableWidgetItem(item.category)
                category.setToolTip(item.category)
                self.table.setItem(row, 1, category)
                student = QTableWidgetItem("Да" if item.student_allowed else "Нет")
                student.setToolTip(student.text())
                group = QTableWidgetItem(item.group or "Рабочие")
                group.setToolTip(group.text())
                self.table.setItem(row, 2, student)
                self.table.setItem(row, 3, group)
            status_column = self._status_column()
            self.table.setItem(row, status_column, _status_item(item.is_active))
            if not item.is_active:
                self._mark_disabled_row(row)

    def _refresh_pay_rates(self) -> None:
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            position = QTableWidgetItem(item.position_name)
            position.setData(Qt.ItemDataRole.UserRole, item.id)
            position.setToolTip(item.position_name)
            category = QTableWidgetItem(item.category)
            category.setToolTip(item.category)
            salary_type = QTableWidgetItem("Зарплата" if item.salary_type == "monthly" else "Ставка")
            salary_type.setToolTip(salary_type.text())
            salary = QTableWidgetItem(item.salary)
            salary.setToolTip(item.salary)
            payment = QTableWidgetItem(_salary_display(item.salary, item.salary_type))
            payment.setToolTip(payment.text())
            self.table.setItem(row, 0, position)
            self.table.setItem(row, 1, category)
            self.table.setItem(row, 2, salary_type)
            self.table.setItem(row, 3, salary)
            self.table.setItem(row, 4, payment)

    def _build_layout(self) -> None:
        header = QLabel("Справочники")
        header.setObjectName("DialogTitle")
        buttons = QHBoxLayout()
        for button in (self.add_button, self.rename_button, self.disable_button, self.restore_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        right = QVBoxLayout()
        right.addWidget(header)
        right.addWidget(self.search)
        right.addWidget(self.table)
        right.addLayout(buttons)
        content = QHBoxLayout(self)
        content.addWidget(self.navigation)
        content.addLayout(right)

    def _connect(self) -> None:
        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.search.textChanged.connect(self.refresh)
        self.add_button.clicked.connect(self._add_item)
        self.rename_button.clicked.connect(self._rename_item)
        self.disable_button.clicked.connect(lambda: self._set_active(False))
        self.restore_button.clicked.connect(lambda: self._set_active(True))
        self.delete_button.clicked.connect(self._delete_item)
        self.close_button.clicked.connect(self.accept)
        self.table.doubleClicked.connect(self._toggle_selected_active)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

    def _fill_navigation(self) -> None:
        for label in self.DIRECTORY_LABELS.values():
            self.navigation.addItem(label)
        keys = list(self.DIRECTORY_LABELS)
        self.navigation.setCurrentRow(keys.index(self.current_key))

    def _navigation_changed(self, row: int) -> None:
        keys = list(self.DIRECTORY_LABELS)
        if 0 <= row < len(keys):
            self.current_key = keys[row]
            self.search.clear()
            self.refresh()

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _selected_name(self) -> str:
        row = self.table.currentRow()
        return self.table.item(row, 0).text() if row >= 0 else ""

    def _selected_item(self) -> DirectoryItem | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _add_item(self) -> None:
        if self.current_key == "pay_rates":
            self._info("Строки оплаты формируются автоматически из активных должностей и их категорий")
            return
        if self.current_key == "positions":
            dialog = PositionDialog(parent=self)
            if dialog.exec():
                name, category, student_allowed, salary, salary_type, group = dialog.values()
                item_id = self.directory_service.ensure("positions", name)
                if item_id is not None:
                    self.directory_service.update_position_details(
                        item_id,
                        name,
                        category,
                        student_allowed,
                        salary,
                        salary_type,
                        group,
                    )
                self.refresh()
            return
        dialog = TextInputDialog("Добавить", "Название", parent=self)
        if dialog.exec() and dialog.value().strip():
            name = dialog.value()
            self.directory_service.ensure(self.current_key, name)
            self.refresh()

    def _rename_item(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            self._info("Выберите строку")
            return
        if self.current_key == "pay_rates":
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            dialog = PayRateDialog(item, self)
            if dialog.exec():
                salary, salary_type = dialog.values()
                self.directory_service.update_pay_rate(item_id, salary, salary_type)
                self.refresh()
            return
        if self.current_key == "positions":
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            dialog = PositionDialog(item, self)
            if dialog.exec():
                name, category, student_allowed, salary, salary_type, group = dialog.values()
                self.directory_service.update_position_details(
                    item_id,
                    name,
                    category,
                    student_allowed,
                    salary,
                    salary_type,
                    group,
                )
                self.refresh()
            return
        dialog = TextInputDialog("Переименовать", "Название", self._selected_name(), self)
        if dialog.exec() and dialog.value().strip():
            name = dialog.value()
            self.directory_service.rename(self.current_key, item_id, name)
            self.refresh()

    def _set_active(self, is_active: bool) -> None:
        if self.current_key == "pay_rates":
            self._info("Активность оплаты управляется через справочник должностей")
            return
        item_id = self._selected_id()
        if item_id is None:
            self._info("Выберите строку")
            return
        self.directory_service.set_active(self.current_key, item_id, is_active)
        self.refresh()

    def _delete_item(self) -> None:
        if self.current_key == "pay_rates":
            self._info("Строки оплаты удаляются из списка автоматически при изменении должностей или категорий")
            return
        item_id = self._selected_id()
        if item_id is None:
            self._info("Выберите строку")
            return
        if not self._ask(f"Удалить '{self._selected_name()}'?"):
            return
        try:
            self.directory_service.delete(self.current_key, item_id)
        except ValueError as exc:
            self._info(str(exc))
            return
        self.refresh()

    def _toggle_selected_active(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._items):
            return
        if self.current_key == "pay_rates":
            self._rename_item()
            return
        self.directory_service.set_active(self.current_key, self._items[row].id, not self._items[row].is_active)
        self.refresh()

    def _show_context_menu(self, position) -> None:
        menu = QMenu(self)
        add_action = QAction("Добавить", self)
        rename_action = QAction("Редактировать", self)
        disable_action = QAction("Отключить", self)
        restore_action = QAction("Активировать", self)
        delete_action = QAction("Удалить", self)
        has_item = self._selected_id() is not None
        if self.current_key == "pay_rates":
            rename_action.setEnabled(has_item)
            rename_action.triggered.connect(self._rename_item)
            menu.addAction(rename_action)
            menu.exec(self.table.viewport().mapToGlobal(position))
            return
        for action in (rename_action, disable_action, restore_action, delete_action):
            action.setEnabled(has_item)
        add_action.triggered.connect(self._add_item)
        rename_action.triggered.connect(self._rename_item)
        disable_action.triggered.connect(lambda: self._set_active(False))
        restore_action.triggered.connect(lambda: self._set_active(True))
        delete_action.triggered.connect(self._delete_item)
        menu.addAction(add_action)
        menu.addAction(rename_action)
        menu.addSeparator()
        menu.addAction(disable_action)
        menu.addAction(restore_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _configure_table(self) -> None:
        if self.current_key == "positions":
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(["Должность", "Кат.", "0", "Группа", "Статус"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, 500)
            self.table.setColumnWidth(1, 58)
            self.table.setColumnWidth(2, 42)
            self.table.setColumnWidth(3, 82)
            self.table.setColumnWidth(4, 92)
        elif self.current_key == "pay_rates":
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(["Должность", "Категория", "Тип", "Сумма", "Оплата"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
            self.table.setColumnWidth(0, 430)
            self.table.setColumnWidth(1, 100)
            self.table.setColumnWidth(2, 95)
            self.table.setColumnWidth(3, 110)
        else:
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Название", "Статус"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(1, 130)
        self.table.horizontalHeader().setStretchLastSection(False)

    def _status_column(self) -> int:
        return 4 if self.current_key == "positions" else 1

    def _update_button_visibility(self) -> None:
        is_pay_rates = self.current_key == "pay_rates"
        self.add_button.setVisible(not is_pay_rates)
        self.disable_button.setVisible(not is_pay_rates)
        self.restore_button.setVisible(not is_pay_rates)
        self.delete_button.setVisible(not is_pay_rates)

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Справочники")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    def _ask(self, message: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Справочники")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
        box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        box.exec()
        return box.clickedButton() == yes

    def _mark_disabled_row(self, row: int) -> None:
        background = QColor("#edf0f3")
        foreground = QColor("#6f7882")
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item:
                item.setBackground(background)
                item.setForeground(foreground)


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


def _salary_display(value: str, salary_type: str) -> str:
    if not value.strip():
        return ""
    suffix = " / мес" if salary_type == "monthly" else " / час"
    return f"{value.strip()}{suffix}"


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Справка")
        self.resize(720, 520)
        browser = QTextBrowser()
        browser.setHtml(HELP_HTML)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(browser)
        layout.addWidget(close_button)


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("О программе")
        self.setMinimumWidth(620)
        self.setObjectName("AboutDialog")
        logo = QLabel()
        logo.setFixedSize(62, 62)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if APP_LOGO_FILE.exists():
            pixmap = QPixmap(str(APP_LOGO_FILE))
            logo.setPixmap(pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        title = QLabel(APP_NAME)
        title.setObjectName("AboutTitle")
        version = QLabel(f"Версия {APP_VERSION}")
        version.setObjectName("AboutVersion")
        description = QLabel(
            "ProLOG предназначен для ежедневного учета выполненных производственных работ, "
            "унификации записей сотрудников и формирования отчетов на основе журнала работ."
        )
        description.setObjectName("AboutDescription")
        description.setWordWrap(True)
        product_note = QLabel("MVP-модуль будущей производственной платформы")
        product_note.setObjectName("AboutNote")
        rights = QLabel(
            "Исключительные права на данное программное обеспечение принадлежат "
            "Морарь Александру Александровичу; любое использование, копирование, "
            "распространение или модификация допускаются только с его письменного согласия."
        )
        rights.setObjectName("AboutRights")
        rights.setWordWrap(True)
        close_button = QPushButton("Закрыть")
        close_button.setMinimumWidth(110)
        close_button.clicked.connect(self.accept)

        header = QFrame()
        header.setObjectName("AboutHeader")
        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        text_layout.addWidget(title)
        text_layout.addWidget(version)
        text_layout.addWidget(product_note)
        text_layout.addSpacing(4)
        text_layout.addWidget(description)
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setSpacing(12)
        top_layout.addWidget(logo)
        top_layout.addLayout(text_layout)
        header.setLayout(top_layout)

        legal = QFrame()
        legal.setObjectName("AboutLegal")
        legal_layout = QVBoxLayout(legal)
        legal_layout.setContentsMargins(14, 10, 14, 10)
        legal_layout.setSpacing(5)
        legal_title = QLabel("Правообладатель")
        legal_title.setObjectName("AboutLegalTitle")
        legal_layout.addWidget(legal_title)
        legal_layout.addWidget(rights)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(header)
        layout.addWidget(legal)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)


class UpdateStatusDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Проверка обновлений")
        self.resize(920, 520)
        self.release_url = ""
        self.title = QLabel("Состояние ProLOG и справочников")
        self.title.setObjectName("DialogTitle")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Компонент", "Текущая версия", "Доступная версия", "Статус"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 190)
        self.table.setColumnWidth(3, 340)
        note = QLabel(
            "Справочники обновляются только дополнением: пользовательские должности, объекты "
            "и настройки не удаляются и не перезаписываются."
        )
        note.setWordWrap(True)
        note.setObjectName("WizardSubtitle")
        self.refresh_button = QPushButton("Проверить")
        self.merge_button = QPushButton("Дополнить справочники")
        self.release_button = QPushButton("Открыть релиз")
        self.close_button = QPushButton("Закрыть")
        self.release_button.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.merge_button)
        buttons.addWidget(self.release_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(note)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.refresh_button.clicked.connect(self.refresh_status)
        self.merge_button.clicked.connect(self.merge_dictionaries)
        self.release_button.clicked.connect(self.open_release)
        self.close_button.clicked.connect(self.accept)
        self.refresh_status()

    def refresh_status(self) -> None:
        rows = []
        update_info = UpdateChecker().check()
        if update_info:
            self.release_url = update_info.release_url
            self.release_button.setEnabled(bool(update_info.release_url))
            core_status = "Есть обновление" if update_info.is_newer else "Актуален"
            rows.append(("Ядро ProLOG", APP_VERSION, update_info.latest_version or "неизвестно", core_status))
        else:
            self.release_url = ""
            self.release_button.setEnabled(False)
            rows.append(("Ядро ProLOG", APP_VERSION, "не удалось проверить", "Проверьте интернет или GitHub Releases"))
        for status in dictionary_statuses():
            rows.append((status.label, status.current_version, status.bundled_version, status.status_text))
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)

    def merge_dictionaries(self) -> None:
        added = merge_dictionary_updates()
        self.refresh_status()
        self._info(f"Добавлено записей в справочники: {added}")

    def open_release(self) -> None:
        if self.release_url:
            webbrowser.open(self.release_url)

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Проверка обновлений")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class TextInputDialog(QDialog):
    def __init__(self, title: str, label: str, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(660)
        self.input = QLineEdit(text)
        accept_button = QPushButton("ОК")
        cancel_button = QPushButton("Отмена")
        accept_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(accept_button)
        buttons.addWidget(cancel_button)
        layout = QFormLayout(self)
        layout.addRow(label, self.input)
        layout.addRow(buttons)

    def value(self) -> str:
        return self.input.text()


HELP_HTML = f"""
<h2>{APP_NAME}</h2>
<p><b>Назначение:</b> ежедневный сбор и унификация записей о выполненных работах.</p>
<h3>Основной порядок работы</h3>
<ol>
  <li>Пройдите авторизацию через меню <b>Файл - Авторизация</b>.</li>
  <li>При первом запуске пройдите первичную настройку: сотрудники, должности, виды работ и объекты.</li>
  <li>Выберите сотрудника слева и дату в рабочей области.</li>
  <li>Заполните объект, вид работ, описание, часы и комментарий.</li>
  <li>Сохраните запись. Ниже появится список работ на выбранную дату.</li>
  <li>Используйте меню <b>Экспорт</b> для формирования отчета или сменного задания.</li>
</ol>
<h3>Импорт сотрудников</h3>
<p>Excel-файл должен содержать колонки: №, ФИО, Должность, Категория.</p>
<h3>Импорт старых отчетов</h3>
<p>После завершения мастера настройки используйте <b>Файл - Импорт старых отчетов Excel</b>.
Перед переносом программа покажет ошибки, пропущенные дни, неизвестных сотрудников и новые объекты.</p>
<h3>Версия</h3>
<p>{APP_VERSION}</p>
"""
