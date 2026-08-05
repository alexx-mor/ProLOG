"""Reusable dialogs."""

from __future__ import annotations

import webbrowser
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QSize, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QIntValidator, QPainter, QPen, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QCalendarWidget,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMessageBox,
    QMenu,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from config import ConfigManager
from constants import APP_LOGO_FILE, APP_NAME, APP_VERSION, DATABASE_FILE, DIRECTORY_ICONS_DIR
from database_registry import (
    ALIASES_DATABASE,
    DATABASE_KIND_LABELS,
    EMPLOYEES_DATABASE,
    OBJECTS_DATABASE,
    PROLOG_DATABASE,
    PRODUCTS_DATABASE,
    WORKBOT_DATABASE,
    DatabaseRegistry,
    DatabaseSource,
)
from directory_files import dictionary_statuses, merge_dictionary_updates
from category_rules import NO_CATEGORY, STUDENT_CATEGORY
from models import AliasItem, DirectoryItem, Employee, ObjectStatus, PayRate, ProductItem, ProductStatus, WorkCalendarDay, WorkDayType
from services import category_values_from_rule
from update_checker import UpdateChecker


PAY_CATEGORY_COLUMNS = (STUDENT_CATEGORY, "1", "2", "3")
ALIAS_TYPE_LABELS = {
    "employee": "Сотрудник",
    "object": "Объект",
    "location": "Местонахождение",
    "work_type": "Вид работ",
    "product": "Изделие",
}
MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
FIXED_RU_HOLIDAYS = {
    (1, 1): "Новогодние каникулы",
    (1, 2): "Новогодние каникулы",
    (1, 3): "Новогодние каникулы",
    (1, 4): "Новогодние каникулы",
    (1, 5): "Новогодние каникулы",
    (1, 6): "Новогодние каникулы",
    (1, 7): "Рождество Христово",
    (1, 8): "Новогодние каникулы",
    (2, 23): "День защитника Отечества",
    (3, 8): "Международный женский день",
    (5, 1): "Праздник Весны и Труда",
    (5, 9): "День Победы",
    (6, 12): "День России",
    (11, 4): "День народного единства",
}
_CALENDAR_BLOCKED_KEYS = {
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
}


@dataclass(slots=True)
class PayRateGroup:
    position_id: int
    position_name: str
    rates: dict[str, PayRate]

    @property
    def salary_type(self) -> str:
        first = self._first_rate()
        return first.salary_type if first else "hourly"

    @property
    def far_trip_coeff(self) -> str:
        first = self._first_rate()
        return first.far_trip_coeff if first else "1"

    @property
    def near_trip_coeff(self) -> str:
        first = self._first_rate()
        return first.near_trip_coeff if first else "1"

    @property
    def holiday_coeff(self) -> str:
        first = self._first_rate()
        return first.holiday_coeff if first else "1"

    @property
    def saturday_coeff(self) -> str:
        first = self._first_rate()
        return first.saturday_coeff if first else "1"

    def _first_rate(self) -> PayRate | None:
        return next(iter(self.rates.values()), None)


class MonthCalendarWidget(QCalendarWidget):
    def __init__(self, month: int, parent=None) -> None:
        super().__init__(parent)
        self.month = month
        self.year = QDate.currentDate().year()
        self.setGridVisible(True)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setNavigationBarVisible(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumWidth(210)
        self.setMaximumHeight(205)
        self.set_month(self.year, month)

    def set_month(self, year: int, month: int) -> None:
        self.year = year
        self.month = month
        self.setMinimumDate(QDate(year, 1, 1))
        self.setMaximumDate(QDate(year, 12, 31))
        self.setCurrentPage(year, month)
        self._install_event_guards()

    def _install_event_guards(self) -> None:
        self.installEventFilter(self)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for child in self.findChildren(QWidget):
            child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            child.installEventFilter(self)
            viewport = getattr(child, "viewport", lambda: None)()
            if viewport is not None:
                viewport.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                viewport.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Wheel:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in _CALENDAR_BLOCKED_KEYS:
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event) -> None:
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in _CALENDAR_BLOCKED_KEYS:
            event.accept()
            return
        super().keyPressEvent(event)

    def paintCell(self, painter: QPainter, rect, date_value: QDate) -> None:
        if date_value.year() != self.year or date_value.month() != self.month:
            return
        super().paintCell(painter, rect, date_value)


class EmployeeDialog(QDialog):
    def __init__(self, positions, employee: Employee | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сотрудник")
        self.setMinimumWidth(660)
        self.full_name = QLineEdit(employee.full_name if employee else "")
        self.mobile_phone = QLineEdit(employee.mobile_phone if employee else "")
        self.mobile_phone.setPlaceholderText("+7 999 123-45-67")
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
        layout.addRow("Мобильный телефон", self.mobile_phone)
        layout.addRow("Должность", self.position)
        layout.addRow("Разряд", self.category)
        layout.addRow(buttons)
        self.position.currentTextChanged.connect(lambda _text: self._position_changed(layout))
        self._position_changed(layout)

    def employee(self, employee_id: int | None = None) -> Employee:
        return Employee(
            id=employee_id,
            full_name=self.full_name.text(),
            position=self.position.currentText(),
            category=self.category.currentText(),
            mobile_phone=self.mobile_phone.text(),
        )

    def _sync_categories(self, current: str = "") -> None:
        position = self.position_items.get(self.position.currentText().strip())
        allowed = category_values_from_rule(position.category if position else "")
        if position and position.student_allowed:
            allowed = [STUDENT_CATEGORY, *allowed]
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

    def _position_changed(self, layout: QFormLayout) -> None:
        self._sync_categories(self.category.currentText())
        label = layout.labelForField(self.category)
        if label:
            label.setText("Категория" if _is_asutp_engineer(self.position.currentText()) else "Разряд")


class PositionDialog(QDialog):
    CATEGORY_RULES = ("—", "1", "1-2", "1-3", "1-4", "1-5", "1-6")

    def __init__(
        self,
        position: DirectoryItem | None = None,
        parent=None,
        groups: list[DirectoryItem] | list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Должность")
        self.setMinimumWidth(700)
        group_names = self._group_names(groups, position.group if position else "")
        self.name = QLineEdit(position.name if position else "")
        self.category = QComboBox()
        self.category.addItems(self.CATEGORY_RULES)
        self.student_allowed = QCheckBox("Разрешить разряд 0 (ученик/стажер)")
        self.salary = QLineEdit(position.salary if position else "")
        self.salary_type = QComboBox()
        self.salary_type.addItem("Ставка", "hourly")
        self.salary_type.addItem("Зарплата", "monthly")
        self.group = QComboBox()
        self.group.addItems(group_names)
        self.group.setEnabled(bool(group_names))
        self.is_active = position.is_active if position else True
        self.status_label = QLabel()
        self.toggle_active_button = QPushButton()
        self._sync_active_controls()

        if position:
            category = position.category or "—"
            if self.category.findText(category) < 0:
                self.category.addItem(category)
            self.category.setCurrentText(category)
            self.student_allowed.setChecked(position.student_allowed)
            index = self.salary_type.findData(position.salary_type)
            self.salary_type.setCurrentIndex(index if index >= 0 else 0)
            group = position.group or (group_names[0] if group_names else "")
            index = self.group.findText(group)
            self.group.setCurrentIndex(index if index >= 0 else 0)
            self.is_active = position.is_active
            self._sync_active_controls()

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        self.toggle_active_button.clicked.connect(self._toggle_active)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QFormLayout(self)
        layout.addRow("Название", self.name)
        layout.addRow("Категории/разряды", self.category)
        layout.addRow("", self.student_allowed)
        layout.addRow("Группа", self.group)
        layout.addRow("Статус", self.status_label)
        layout.addRow("", self.toggle_active_button)
        layout.addRow(buttons)

    def values(self) -> tuple[str, str, bool, str, str, str, bool]:
        return (
            self.name.text().strip(),
            self.category.currentText().strip() or "—",
            self.student_allowed.isChecked(),
            self.salary.text().strip(),
            str(self.salary_type.currentData() or "hourly"),
            self.group.currentText().strip(),
            self.is_active,
        )

    def _group_names(self, groups: list[DirectoryItem] | list[str] | None, current_group: str) -> list[str]:
        names: list[str] = []
        for group in groups or []:
            name = getattr(group, "name", str(group)).strip()
            if name and name.casefold() not in {existing.casefold() for existing in names}:
                names.append(name)
        if current_group.strip() and current_group.casefold() not in {name.casefold() for name in names}:
            names.append(current_group.strip())
        return names

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

    def _toggle_active(self) -> None:
        self.is_active = not self.is_active
        self._sync_active_controls()

    def _sync_active_controls(self) -> None:
        self.status_label.setText("Активна" if self.is_active else "Отключена")
        self.toggle_active_button.setText("Отключить" if self.is_active else "Активировать")


class ObjectDialog(QDialog):
    CONTRACT_TYPES = ("", "Поставка", "Строительство (СМР)")
    OBJECT_TYPES = ("", "Очистные сооружения", "Насосные станции", "Резервуары и емкости", "Другое")
    SUBTYPES = {
        "Очистные сооружения": ("КОС", "МБР", "ВОС", "ЛОС", "ОСЛВ", "Сливная станция"),
        "Насосные станции": ("ВНС", "КНС", "НС"),
        "Резервуары и емкости": ("РВС", "РПВ", "РК"),
        "Другое": (),
    }

    def __init__(self, item: DirectoryItem | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Объект")
        self.setMinimumWidth(720)
        self.name = QLineEdit(item.name if item else "")
        self.project_number = QLineEdit(item.project_number if item else "")
        self.contract_number = QLineEdit(item.contract_number if item else "")
        self.customer = QLineEdit(item.customer if item else "")
        self.object_status = QComboBox()
        self.object_status.addItems([status.value for status in ObjectStatus])
        self.contract_type = QComboBox()
        self.contract_type.addItems(self.CONTRACT_TYPES)
        self.object_type = QComboBox()
        self.object_type.addItems(self.OBJECT_TYPES)
        self.object_subtype = QComboBox()
        self.object_subtype.setEditable(True)
        self.signed_date = QDateEdit()
        self.due_date = QDateEdit()
        self.days_left = QLabel("")
        self.days_left.setObjectName("WizardSubtitle")
        for date_edit in (self.signed_date, self.due_date):
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("dd.MM.yyyy")
            date_edit.setSpecialValueText("")
            date_edit.setMinimumDate(QDate(2000, 1, 1))
            date_edit.setMaximumDate(QDate(2100, 12, 31))

        if item:
            self._set_combo_text(self.object_status, item.object_status)
            self._set_combo_text(self.contract_type, item.contract_type)
            self._set_combo_text(self.object_type, item.object_type)
        self._sync_subtypes(item.object_subtype if item else "")
        if item:
            self._set_date(self.signed_date, item.signed_date)
            self._set_date(self.due_date, item.due_date)
        else:
            today = QDate.currentDate()
            self.signed_date.setDate(today)
            self.due_date.setDate(today)

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QFormLayout(self)
        layout.addRow("Общее наименование", self.name)
        layout.addRow("№ проекта/заявки", self.project_number)
        layout.addRow("№ договора", self.contract_number)
        layout.addRow("Дата подписания", self.signed_date)
        due_row = QHBoxLayout()
        due_row.addWidget(self.due_date)
        due_row.addWidget(self.days_left)
        layout.addRow("Дата окончания", due_row)
        layout.addRow("Заказчик", self.customer)
        layout.addRow("Состояние", self.object_status)
        layout.addRow("Предмет договора", self.contract_type)
        layout.addRow("Тип объекта", self.object_type)
        layout.addRow("Подтип объекта", self.object_subtype)
        layout.addRow(buttons)

        self.object_type.currentTextChanged.connect(lambda _text: self._sync_subtypes())
        self.due_date.dateChanged.connect(lambda _date: self._refresh_days_left())
        self._refresh_days_left()

    def values(self) -> tuple[str, str, str, str, str, str, str, str, str, str]:
        return (
            self.name.text().strip(),
            self.project_number.text().strip(),
            self.contract_number.text().strip(),
            self.customer.text().strip(),
            self.contract_type.currentText().strip(),
            self.object_type.currentText().strip(),
            self.object_subtype.currentText().strip(),
            self._date_value(self.signed_date),
            self._date_value(self.due_date),
            self.object_status.currentText().strip(),
        )

    def accept(self) -> None:
        if not self.name.text().strip():
            self._info("Укажите общее наименование объекта")
            return
        super().accept()

    def _sync_subtypes(self, current: str = "") -> None:
        object_type = self.object_type.currentText().strip()
        values = self.SUBTYPES.get(object_type, ())
        current_text = current or self.object_subtype.currentText().strip()
        self.object_subtype.blockSignals(True)
        self.object_subtype.clear()
        self.object_subtype.addItem("")
        self.object_subtype.addItems(values)
        self.object_subtype.setEditable(True)
        if current_text:
            if self.object_subtype.findText(current_text) < 0:
                self.object_subtype.addItem(current_text)
            self.object_subtype.setCurrentText(current_text)
        self.object_subtype.blockSignals(False)

    def _refresh_days_left(self) -> None:
        days = QDate.currentDate().daysTo(self.due_date.date())
        if days > 0:
            text = f"Осталось дней: {days}"
            style = ""
        elif days == 0:
            text = "Срок сдачи сегодня"
            style = "color: #8a5a00; font-weight: 600;"
        else:
            text = f"Просрочено дней: {abs(days)}"
            style = "color: #b42318; font-weight: 700;"
        self.days_left.setText(text)
        self.days_left.setStyleSheet(style)
        self.due_date.setStyleSheet("border: 1px solid #d92d20;" if days < 0 else "")

    def _set_combo_text(self, combo: QComboBox, value: str) -> None:
        if value and combo.findText(value) < 0:
            combo.addItem(value)
        if value:
            combo.setCurrentText(value)

    def _set_date(self, date_edit: QDateEdit, value: str) -> None:
        date_value = QDate.fromString(value, "yyyy-MM-dd")
        date_edit.setDate(date_value if date_value.isValid() else QDate.currentDate())

    def _date_value(self, date_edit: QDateEdit) -> str:
        return date_edit.date().toString("yyyy-MM-dd")

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Объект")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class CalendarDayDialog(QDialog):
    def __init__(self, calendar_day: WorkCalendarDay | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Календарный день")
        self.setMinimumWidth(560)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setMinimumDate(QDate(2000, 1, 1))
        self.date_edit.setMaximumDate(QDate(2100, 12, 31))
        self.day_type = QComboBox()
        self.day_type.addItems([day_type.value for day_type in WorkDayType])
        self.note = QLineEdit(calendar_day.note if calendar_day else "")
        self.note.setPlaceholderText("Например: перенос рабочего дня или официальный праздник")
        self.item_id = calendar_day.id if calendar_day else None

        if calendar_day:
            self.date_edit.setDate(QDate.fromString(calendar_day.work_date.isoformat(), "yyyy-MM-dd"))
            index = self.day_type.findText(calendar_day.day_type)
            self.day_type.setCurrentIndex(index if index >= 0 else 0)
        else:
            self.date_edit.setDate(QDate.currentDate())

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QFormLayout(self)
        layout.addRow("Дата", self.date_edit)
        layout.addRow("Тип дня", self.day_type)
        layout.addRow("Примечание", self.note)
        layout.addRow(buttons)

    def value(self) -> WorkCalendarDay:
        return WorkCalendarDay(
            id=self.item_id,
            work_date=self.date_edit.date().toPython(),
            day_type=self.day_type.currentText().strip(),
            note=self.note.text().strip(),
        )


class ProductDialog(QDialog):
    def __init__(self, objects: list[DirectoryItem], product: ProductItem | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изделие")
        self.setMinimumWidth(720)
        self.item_id = product.id if product else None
        self.is_active = product.is_active if product else True
        self.object = QComboBox()
        self.object.setView(QListView())
        self.object.setMinimumWidth(360)
        self.object.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.object.addItem("", None)
        for item in objects:
            self.object.addItem(item.name, item.id)
        self.serial_number = QLineEdit(product.serial_number if product else "")
        self.name = QLineEdit(product.name if product else "")
        self.code = QLineEdit(product.code if product else "")
        self.product_status = QComboBox()
        self.product_status.addItems([status.value for status in ProductStatus])
        self.readiness_percent = QLineEdit(str(product.readiness_percent if product else 0))
        self.readiness_percent.setValidator(QIntValidator(0, 100, self))
        self.start_date = QDateEdit()
        self.release_date = QDateEdit()
        for date_edit in (self.start_date, self.release_date):
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("dd.MM.yyyy")
            date_edit.setMinimumDate(QDate(2000, 1, 1))
            date_edit.setMaximumDate(QDate(2100, 12, 31))

        if product:
            self._select_object(product.object_id)
            self._set_combo_text(self.product_status, product.product_status)
            self._set_date(self.start_date, product.start_date)
            self._set_date(self.release_date, product.release_date)
        else:
            today = QDate.currentDate()
            self.start_date.setDate(today)
            self.release_date.setDate(today)

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QFormLayout(self)
        layout.addRow("Объект", self.object)
        layout.addRow("Заводской номер", self.serial_number)
        layout.addRow("Наименование", self.name)
        layout.addRow("Шифр", self.code)
        layout.addRow("Состояние", self.product_status)
        layout.addRow("Готовность, %", self.readiness_percent)
        layout.addRow("Дата начала изготовления", self.start_date)
        layout.addRow("Дата выпуска", self.release_date)
        layout.addRow(buttons)

    def value(self) -> ProductItem:
        readiness = int(self.readiness_percent.text() or 0)
        return ProductItem(
            id=self.item_id,
            object_id=int(self.object.currentData()),
            serial_number=self.serial_number.text().strip(),
            name=self.name.text().strip(),
            code=self.code.text().strip(),
            product_status=self.product_status.currentText().strip(),
            readiness_percent=max(0, min(100, readiness)),
            start_date=self._date_value(self.start_date),
            release_date=self._date_value(self.release_date),
            is_active=self.is_active,
        )

    def accept(self) -> None:
        if self.object.currentData() is None:
            self._info("Выберите объект")
            return
        if not self.name.text().strip():
            self._info("Укажите наименование изделия")
            return
        super().accept()

    def _select_object(self, object_id: int) -> None:
        index = self.object.findData(object_id)
        self.object.setCurrentIndex(index if index >= 0 else 0)

    def _set_combo_text(self, combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _set_date(self, date_edit: QDateEdit, value: str) -> None:
        date_value = QDate.fromString(value, "yyyy-MM-dd")
        date_edit.setDate(date_value if date_value.isValid() else QDate.currentDate())

    def _date_value(self, date_edit: QDateEdit) -> str:
        return date_edit.date().toString("yyyy-MM-dd")

    def _info(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Изделие")
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class PayRateDialog(QDialog):
    def __init__(self, group: PayRateGroup, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Оплата")
        self.setMinimumWidth(860)
        self.group = group
        self.salary_type = QComboBox()
        self.salary_type.addItem("Ставка", "hourly")
        self.salary_type.addItem("Зарплата", "monthly")
        index = self.salary_type.findData(group.salary_type)
        self.salary_type.setCurrentIndex(index if index >= 0 else 0)
        self.category_rows = _pay_rate_row_categories(group)
        self.table = QTableWidget(len(self.category_rows), 4)
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 280)
        self.table.setColumnWidth(1, 170)
        self._fill_amount_table()
        self.near_trip_coeff = QLineEdit(group.near_trip_coeff)
        self.holiday_coeff = QLineEdit(group.holiday_coeff)
        self.saturday_coeff = QLineEdit(group.saturday_coeff)
        for coeff in (self.near_trip_coeff, self.holiday_coeff, self.saturday_coeff):
            coeff.setPlaceholderText("Например: 1,5")
        self.salary_type.currentIndexChanged.connect(self._update_amount_header)

        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QFormLayout(self)
        layout.addRow("Тип оплаты", self.salary_type)
        layout.addRow(self.table)
        layout.addRow("КТУ командировка ближняя", self.near_trip_coeff)
        layout.addRow("КТУ воскр. и праздники", self.holiday_coeff)
        layout.addRow("КТУ суббота", self.saturday_coeff)
        layout.addRow(buttons)

    def _fill_amount_table(self) -> None:
        self._update_amount_header()
        for row, category in enumerate(self.category_rows):
            position = QTableWidgetItem(self.group.position_name if row == 0 else "")
            position.setFlags(position.flags() & ~Qt.ItemFlag.ItemIsEditable)
            position.setToolTip(self.group.position_name)
            category_item = QTableWidgetItem(_pay_category_row_title(category, self.group.position_name))
            category_item.setFlags(category_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            category_item.setToolTip(category_item.text())
            amount_item = QTableWidgetItem(self.group.rates[category].salary)
            amount_item.setToolTip(amount_item.text())
            far_trip_item = QTableWidgetItem(self.group.rates[category].far_trip_salary)
            far_trip_item.setToolTip(far_trip_item.text())
            self.table.setItem(row, 0, position)
            self.table.setItem(row, 1, category_item)
            self.table.setItem(row, 2, amount_item)
            self.table.setItem(row, 3, far_trip_item)
        if len(self.category_rows) > 1:
            self.table.setSpan(0, 0, len(self.category_rows), 1)
        self.table.resizeRowsToContents()

    def _update_amount_header(self) -> None:
        amount_header = "Зарплата" if self.salary_type.currentData() == "monthly" else "Ставка"
        self.table.setHorizontalHeaderLabels(["Должность", "Разряд", amount_header, "Командировка дальняя"])

    def values(self) -> tuple[str, dict[str, str], dict[str, str], str, str, str]:
        return (
            str(self.salary_type.currentData() or "hourly"),
            {
                category: (self.table.item(row, 2).text().strip() if self.table.item(row, 2) else "")
                for row, category in enumerate(self.category_rows)
            },
            {
                category: (self.table.item(row, 3).text().strip() if self.table.item(row, 3) else "")
                for row, category in enumerate(self.category_rows)
            },
            self.near_trip_coeff.text().strip(),
            self.holiday_coeff.text().strip(),
            self.saturday_coeff.text().strip(),
        )


class AliasDialog(QDialog):
    def __init__(
        self,
        targets: dict[str, list[tuple[int, str]]],
        alias: AliasItem | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.targets = targets
        self.alias = alias
        self.setWindowTitle("Алиас")
        self.setFixedWidth(700)
        self.alias_type = QComboBox()
        for key, label in ALIAS_TYPE_LABELS.items():
            self.alias_type.addItem(label, key)
        self.original_alias = QLineEdit(alias.original_alias if alias else "")
        self.target = QComboBox()
        self.target.setView(QListView())
        self.target.setMinimumWidth(440)
        self.alias_type.currentIndexChanged.connect(self._fill_targets)
        if alias:
            index = self.alias_type.findData(alias.alias_type)
            self.alias_type.setCurrentIndex(max(0, index))
        self._fill_targets()
        if alias:
            index = self.target.findData(alias.target_id)
            self.target.setCurrentIndex(max(0, index))
        form = QFormLayout()
        form.addRow("Тип", self.alias_type)
        form.addRow("Написание в сообщении", self.original_alias)
        form.addRow("Соответствует", self.target)
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def value(self) -> AliasItem:
        return AliasItem(
            alias_type=str(self.alias_type.currentData() or ""),
            original_alias=self.original_alias.text().strip(),
            target_id=int(self.target.currentData()),
            target_name=self.target.currentText(),
        )

    def accept(self) -> None:
        if not self.original_alias.text().strip():
            QMessageBox.warning(self, "Алиас", "Укажите написание из сообщения")
            return
        if self.target.currentData() is None:
            QMessageBox.warning(self, "Алиас", "Выберите запись, которой соответствует алиас")
            return
        super().accept()

    def _fill_targets(self) -> None:
        current_id = self.target.currentData()
        self.target.clear()
        for item_id, name in self.targets.get(str(self.alias_type.currentData() or ""), []):
            self.target.addItem(name, item_id)
        index = self.target.findData(current_id)
        if index >= 0:
            self.target.setCurrentIndex(index)


class DatabaseSourceDialog(QDialog):
    """Adds an existing ProLOG or WorkBot SQLite file to the registry."""

    def __init__(self, source: DatabaseSource | None = None, parent=None) -> None:
        super().__init__(parent)
        self.source = source
        self.setWindowTitle("База данных")
        self.setFixedWidth(720)
        self.name = QLineEdit(source.name if source else "")
        self.kind = QComboBox()
        self.kind.addItem("База ProLOG", PROLOG_DATABASE)
        self.kind.addItem("Сотрудники ProLOG", EMPLOYEES_DATABASE)
        self.kind.addItem("Объекты ProLOG", OBJECTS_DATABASE)
        self.kind.addItem("Изделия ProLOG", PRODUCTS_DATABASE)
        self.kind.addItem("Алиасы ProLOG", ALIASES_DATABASE)
        self.kind.addItem("База WorkBot", WORKBOT_DATABASE)
        if source:
            index = self.kind.findData(source.kind)
            self.kind.setCurrentIndex(max(0, index))
            self.kind.setEnabled(False)
        self.path = QLineEdit(source.path if source else "")
        self.path.setReadOnly(True)
        browse_button = QPushButton("Обзор")
        browse_button.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse_button)
        path_panel = QWidget()
        path_panel.setLayout(path_row)
        form = QFormLayout()
        form.addRow("Название", self.name)
        form.addRow("Назначение", self.kind)
        form.addRow("Файл", path_panel)
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str, str]:
        return self.name.text().strip(), str(self.kind.currentData()), self.path.text().strip()

    def accept(self) -> None:
        if not self.name.text().strip():
            self._warn("Укажите понятное название базы данных")
            return
        if not self.path.text().strip():
            self._warn("Выберите файл базы данных")
            return
        super().accept()

    def _browse(self) -> None:
        current = str(Path(self.path.text()).parent) if self.path.text() else ""
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите базу данных",
            current,
            "База SQLite (*.sqlite3 *.db);;Все файлы (*)",
        )
        if not file_name:
            return
        self.path.setText(file_name)
        if not self.name.text().strip():
            self.name.setText(Path(file_name).stem)

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "База данных", message)


class DirectoryDialog(QDialog):
    DIRECTORY_LABELS = {
        "locations": "Местонахождения",
        "work_types": "Виды работ",
        "employee_groups": "Блоки/Группы",
        "positions": "Должности",
        "pay_rates": "Оплата",
        "objects": "Объекты",
        "products": "Изделия",
        "aliases": "Алиасы",
        "calendar": "Производственный календарь",
        "databases": "Базы данных",
    }
    DIRECTORY_TITLES = {
        "locations": "Справочник местонахождений",
        "work_types": "Справочник видов работ",
        "employee_groups": "Справочник блоков/групп",
        "positions": "Справочник должностей",
        "pay_rates": "Справочник оплаты",
        "objects": "Справочник объектов",
        "products": "Справочник изделий",
        "aliases": "Справочник алиасов",
        "calendar": "Производственный календарь",
        "databases": "Справочник баз данных",
    }

    def __init__(
        self,
        directory_service,
        parent=None,
        initial_key: str = "locations",
        can_edit_pay_rates: bool = True,
        can_edit_databases: bool = True,
        database_registry: DatabaseRegistry | None = None,
        config_manager: ConfigManager | None = None,
        app_settings=None,
        current_database_path: Path | None = None,
        current_database_paths: dict[str, Path] | None = None,
        employee_service=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Справочники")
        self.resize(1320, 820)
        self.directory_service = directory_service
        self.employee_service = employee_service
        self.database_registry = database_registry or DatabaseRegistry()
        self.config_manager = config_manager or ConfigManager()
        self.app_settings = app_settings or self.config_manager.load()
        self.current_database_path = current_database_path or DATABASE_FILE
        self.current_database_paths = current_database_paths or {
            PROLOG_DATABASE: self.current_database_path
        }
        self._database_sources: list[DatabaseSource] = []
        self._database_statuses: dict[str, tuple[bool, str]] = {}
        self.directory_labels = dict(self.DIRECTORY_LABELS)
        if not can_edit_pay_rates:
            self.directory_labels.pop("pay_rates", None)
        if not can_edit_databases:
            self.directory_labels.pop("databases", None)
        self.current_key = initial_key if initial_key in self.directory_labels else next(iter(self.directory_labels))
        self._show_inactive_by_key = {
            key: self.directory_service.ui_setting(f"directory/show_inactive/{key}", "1") == "1"
            for key in self.directory_labels
        }
        self._column_widths_by_key = {
            key: _parse_column_widths(
                self.directory_service.ui_setting(f"directory/column_widths/{key}", "")
            )
            for key in self.directory_labels
        }
        self._configured_key = ""
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(285)
        self.navigation.setIconSize(QSize(22, 22))
        self.title_label = QLabel()
        self.title_label.setObjectName("DialogTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по справочнику")
        self.show_inactive = QCheckBox("Показывать неактивные")
        self.show_inactive.setChecked(True)
        self.table = QTableWidget(0, 2)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.product_filter_panel = QWidget()
        self.product_object_filter = QComboBox()
        self.product_object_filter.setView(QListView())
        self.product_object_filter.setMinimumWidth(280)
        self.product_object_filter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.product_sort = QComboBox()
        self.product_sort.addItem("Заданный порядок", "manual")
        self.product_sort.addItem("Заводской номер: по возрастанию", "serial_asc")
        self.product_sort.addItem("Заводской номер: по убыванию", "serial_desc")
        self.product_sort.addItem("Готовность: по возрастанию", "readiness_asc")
        self.product_sort.addItem("Готовность: по убыванию", "readiness_desc")
        saved_product_sort = self.directory_service.ui_setting(
            "directory/products/sort",
            "manual",
        )
        saved_sort_index = self.product_sort.findData(saved_product_sort)
        self.product_sort.setCurrentIndex(saved_sort_index if saved_sort_index >= 0 else 0)
        self.calendar_panel = QWidget()
        self.calendar_year = QSpinBox()
        self.calendar_year.setRange(2000, 2100)
        self.calendar_year.setValue(QDate.currentDate().year())
        self.calendar_widgets = [self._create_month_calendar(month) for month in range(1, 13)]
        self._selected_calendar_date = QDate.currentDate().toPython()
        self.calendar_day_label = QLabel()
        self.calendar_day_status = QLabel()
        self.calendar_day_status.setWordWrap(True)
        self.calendar_day_status.setObjectName("WizardSubtitle")
        self.calendar_legend = QLabel(
            "Цвета: серый - выходной, красный - праздник, желтый - сокращенный день, "
            "зеленый - рабочая суббота, голубой - рабочий праздник."
        )
        self.calendar_legend.setWordWrap(True)
        self.calendar_legend.setObjectName("WizardSubtitle")
        self.calendar_import_button = QPushButton("Загрузить год")
        self._calendar_days_by_date: dict[date, WorkCalendarDay] = {}
        self._calendar_formatted_dates: set[date] = set()
        self.add_button = QPushButton("Добавить")
        self.rename_button = QPushButton("Редактировать")
        self.disable_button = QPushButton("Отключить")
        self.restore_button = QPushButton("Активировать")
        self.delete_button = QPushButton("Удалить")
        self.move_up_button = QPushButton("Вверх")
        self.move_down_button = QPushButton("Вниз")
        self.use_database_button = QPushButton("Использовать")
        self.check_database_button = QPushButton("Проверить")
        self.close_button = QPushButton("Закрыть")
        self._items = []
        self._row_items = []
        self._build_layout()
        self._connect()
        self._fill_navigation()
        self.refresh()

    def refresh(self) -> None:
        if self._configured_key == self.current_key:
            self._remember_column_widths()
        self.title_label.setText(self.DIRECTORY_TITLES.get(self.current_key, "Справочник"))
        self._configure_table()
        self._restore_column_widths()
        self._configured_key = self.current_key
        self._update_button_visibility()
        needle = self.search.text().strip().casefold()
        if self.current_key == "pay_rates":
            self._items = [
                item
                for item in _pay_rate_groups(self.directory_service.list_pay_rates())
                if not needle or needle in item.position_name.casefold()
            ]
            self._refresh_pay_rates()
            return
        if self.current_key == "calendar":
            year = self.calendar_year.value()
            self._items = self.directory_service.list_calendar_days(date(year, 1, 1), date(year, 12, 31))
            self._refresh_calendar_view()
            return
        if self.current_key == "databases":
            self._refresh_databases(needle)
            return
        if self.current_key == "aliases":
            self._refresh_aliases(needle)
            return
        if self.current_key == "products":
            self._populate_product_object_filter()
            selected_object_id = self.product_object_filter.currentData()
            self._items = [
                item
                for item in self.directory_service.list_products()
                if (
                    self._should_show_item(item)
                    and
                    (selected_object_id is None or item.object_id == selected_object_id)
                    and (
                        not needle
                        or needle in item.object_name.casefold()
                        or needle in item.serial_number.casefold()
                        or needle in item.name.casefold()
                        or needle in item.code.casefold()
                        or needle in item.product_status.casefold()
                    )
                )
            ]
            self._items = _sort_products(self._items, str(self.product_sort.currentData()))
            self._refresh_products()
            self._update_button_visibility()
            return
        self._items = [
            item
            for item in self.directory_service.list_all(self.current_key)
            if self._should_show_item(item) and (not needle or needle in item.name.casefold())
        ]
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
                group = QTableWidgetItem(item.group)
                group.setToolTip(group.text())
                self.table.setItem(row, 2, student)
                self.table.setItem(row, 3, group)
            elif self.current_key == "objects":
                values = [
                    item.project_number,
                    item.contract_number,
                    item.customer,
                    item.object_status,
                    item.contract_type,
                    item.object_type,
                    item.object_subtype,
                    _display_date(item.due_date),
                    _days_left_display(item.due_date),
                ]
                for column, value in enumerate(values, start=1):
                    cell = QTableWidgetItem(value)
                    cell.setToolTip(value)
                    self.table.setItem(row, column, cell)
                if _is_overdue(item.due_date):
                    for column in (8, 9):
                        cell = self.table.item(row, column)
                        if cell:
                            cell.setForeground(QColor("#b42318"))
                            cell.setBackground(QColor("#fff1f0"))
            status_column = self._status_column()
            self.table.setItem(row, status_column, _status_item(item.is_active))
            if not item.is_active:
                self._mark_disabled_row(row)

    def _refresh_pay_rates(self) -> None:
        self.table.clearSpans()
        rows_count = sum(max(1, len(_pay_rate_row_categories(group))) for group in self._items)
        self.table.setRowCount(rows_count)
        self._row_items = []
        row = 0
        for group in self._items:
            categories = _pay_rate_row_categories(group) or [NO_CATEGORY]
            position = QTableWidgetItem(group.position_name)
            position.setData(Qt.ItemDataRole.UserRole, group.position_id)
            position.setToolTip(group.position_name)
            salary_type = QTableWidgetItem("Зарплата" if group.salary_type == "monthly" else "Ставка")
            salary_type.setToolTip(salary_type.text())
            self.table.setItem(row, 0, position)
            self.table.setItem(row, 2, salary_type)
            coeffs = [group.near_trip_coeff, group.holiday_coeff, group.saturday_coeff]
            for column, value in enumerate(coeffs, start=5):
                cell = QTableWidgetItem(_format_coefficient(value))
                cell.setToolTip(cell.text())
                self.table.setItem(row, column, cell)

            for offset, category in enumerate(categories):
                current_row = row + offset
                self._row_items.append(group)
                rate = group.rates.get(category)
                category_cell = QTableWidgetItem(_pay_category_row_title(category, group.position_name))
                category_cell.setToolTip(category_cell.text())
                salary_value = _format_money(rate.salary) if rate else "—"
                salary = QTableWidgetItem(salary_value)
                salary.setToolTip(salary.text())
                far_trip_value = _format_money(rate.far_trip_salary) if rate and rate.far_trip_salary.strip() else "—"
                far_trip = QTableWidgetItem(far_trip_value)
                far_trip.setToolTip(far_trip.text())
                self.table.setItem(current_row, 1, category_cell)
                self.table.setItem(current_row, 3, salary)
                self.table.setItem(current_row, 4, far_trip)

            if len(categories) > 1:
                for column in (0, 2, 5, 6, 7):
                    self.table.setSpan(row, column, len(categories), 1)
            row += len(categories)

    def _refresh_calendar_days(self) -> None:
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            date_cell = QTableWidgetItem(_display_calendar_date(item.work_date))
            date_cell.setData(Qt.ItemDataRole.UserRole, item.id)
            date_cell.setToolTip(date_cell.text())
            day_type = QTableWidgetItem(item.day_type)
            day_type.setToolTip(item.day_type)
            note = QTableWidgetItem(item.note)
            note.setToolTip(item.note)
            payment_mode = QTableWidgetItem(_calendar_payment_mode(item.day_type))
            payment_mode.setToolTip(payment_mode.text())
            self.table.setItem(row, 0, date_cell)
            self.table.setItem(row, 1, day_type)
            self.table.setItem(row, 2, note)
            self.table.setItem(row, 3, payment_mode)

    def _refresh_calendar_view(self) -> None:
        self._calendar_days_by_date = {item.work_date: item for item in self._items}
        self._sync_calendar_pages()
        self._apply_calendar_formats()
        self._sync_calendar_selection()

    def _apply_calendar_formats(self) -> None:
        empty_format = QTextCharFormat()
        for calendar in self.calendar_widgets:
            for work_date in self._calendar_formatted_dates:
                calendar.setDateTextFormat(_date_to_qdate(work_date), empty_format)
        self._calendar_formatted_dates.clear()

        year = self.calendar_year.value()
        current = date(year, 1, 1)
        end = date(year, 12, 31)
        while current <= end:
            calendar_day = self._calendar_days_by_date.get(current)
            day_type = calendar_day.day_type if calendar_day else _default_calendar_day_type(current)
            visual_day_type = _calendar_visual_day_type(current, day_type)
            if visual_day_type != WorkDayType.WORKDAY.value:
                qdate = _date_to_qdate(current)
                for calendar in self.calendar_widgets:
                    calendar.setDateTextFormat(qdate, _calendar_date_format(visual_day_type))
                self._calendar_formatted_dates.add(current)
            current += timedelta(days=1)

    def _create_month_calendar(self, month: int) -> MonthCalendarWidget:
        calendar = MonthCalendarWidget(month, self)
        calendar.clicked.connect(self._calendar_date_clicked)
        return calendar

    def _sync_calendar_pages(self) -> None:
        year = self.calendar_year.value()
        for month, calendar in enumerate(self.calendar_widgets, start=1):
            calendar.blockSignals(True)
            calendar.set_month(year, month)
            calendar.blockSignals(False)

    def _refresh_products(self) -> None:
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            object_cell = QTableWidgetItem(item.object_name)
            object_cell.setData(Qt.ItemDataRole.UserRole, item.id)
            object_cell.setToolTip(item.object_name)
            values = [
                item.serial_number,
                item.name,
                item.code,
                item.product_status,
                f"{item.readiness_percent} %",
                _display_date(item.start_date),
                _display_date(item.release_date),
            ]
            self.table.setItem(row, 0, object_cell)
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
            self.table.setItem(row, 8, _status_item(item.is_active))
            if not item.is_active:
                self._mark_disabled_row(row)

    def _refresh_databases(self, needle: str = "") -> None:
        sources = self.database_registry.list(
            self.app_settings.prolog_database_path,
            self.app_settings.workbot_database_path,
            self.app_settings.employees_database_path,
            self.app_settings.objects_database_path,
            self.app_settings.products_database_path,
            self.app_settings.aliases_database_path,
        )
        self._database_sources = [
            item
            for item in sources
            if not needle
            or needle in item.name.casefold()
            or needle in item.path.casefold()
        ]
        self._items = self._database_sources
        self.table.setRowCount(len(self._database_sources))
        for row, source in enumerate(self._database_sources):
            if source.id not in self._database_statuses:
                self._database_statuses[source.id] = self.database_registry.check(source.path, source.kind)
            available, status_text = self._database_statuses[source.id]
            selected = self._database_selection_text(source)
            values = [
                source.name,
                DATABASE_KIND_LABELS.get(source.kind, source.kind),
                source.path,
                status_text,
                selected,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, source.id)
                if column == 3:
                    item.setForeground(QColor("#26834a" if available else "#b42318"))
                self.table.setItem(row, column, item)

    def _refresh_aliases(self, needle: str = "") -> None:
        self._items = [
            item
            for item in self.directory_service.list_aliases()
            if not needle
            or needle in item.original_alias.casefold()
            or needle in item.target_name.casefold()
            or needle in ALIAS_TYPE_LABELS.get(item.alias_type, item.alias_type).casefold()
        ]
        self.table.setRowCount(len(self._items))
        for row, alias in enumerate(self._items):
            values = [
                ALIAS_TYPE_LABELS.get(alias.alias_type, alias.alias_type),
                alias.original_alias,
                alias.target_name or f"ID {alias.target_id}",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                cell.setData(
                    Qt.ItemDataRole.UserRole,
                    (alias.alias_type, alias.alias_normalized),
                )
                self.table.setItem(row, column, cell)

    def _database_selection_text(self, source: DatabaseSource) -> str:
        if source.kind == WORKBOT_DATABASE:
            return "Да" if _same_path(source.path, self.app_settings.workbot_database_path) else ""
        setting_name = {
            PROLOG_DATABASE: "prolog_database_path",
            EMPLOYEES_DATABASE: "employees_database_path",
            OBJECTS_DATABASE: "objects_database_path",
            PRODUCTS_DATABASE: "products_database_path",
            ALIASES_DATABASE: "aliases_database_path",
        }[source.kind]
        configured = getattr(self.app_settings, setting_name) or str(
            self.current_database_paths.get(source.kind, "")
        )
        if not _same_path(source.path, configured):
            return ""
        current_path = self.current_database_paths.get(source.kind)
        return "Да" if current_path and _same_path(source.path, str(current_path)) else "После перезапуска"

    def _populate_product_object_filter(self) -> None:
        current_id = self.product_object_filter.currentData()
        self.product_object_filter.blockSignals(True)
        self.product_object_filter.clear()
        self.product_object_filter.addItem("Все объекты", None)
        for item in self.directory_service.list_all("objects"):
            if not self._should_show_item(item):
                continue
            self.product_object_filter.addItem(item.name, item.id)
        if current_id is not None:
            index = self.product_object_filter.findData(current_id)
            self.product_object_filter.setCurrentIndex(index if index >= 0 else 0)
        self.product_object_filter.blockSignals(False)

    def _should_show_item(self, item) -> bool:
        return self.show_inactive.isChecked() or getattr(item, "is_active", True)

    def _calendar_year_changed(self, year: int) -> None:
        month_date = QDate(year, self._selected_calendar_date.month, 1)
        day = min(self._selected_calendar_date.day, month_date.daysInMonth())
        self._selected_calendar_date = date(year, self._selected_calendar_date.month, day)
        self.refresh()

    def _calendar_date_clicked(self, selected: QDate) -> None:
        calendar = self.sender()
        if isinstance(calendar, MonthCalendarWidget) and selected.month() != calendar.month:
            return
        self._selected_calendar_date = _qdate_to_date(selected)
        if self.calendar_year.value() != selected.year():
            self.calendar_year.setValue(selected.year())
            return
        self._sync_calendar_selection()

    def _sync_calendar_selection(self) -> None:
        selected = self._selected_calendar_date
        calendar_day = self._calendar_days_by_date.get(selected)
        self.calendar_day_label.setText(_display_calendar_date(selected))
        self.calendar_day_status.setText(_calendar_day_description(selected, calendar_day))

    def _import_calendar_year(self) -> None:
        year = self.calendar_year.value()
        progress = QProgressDialog(f"Загружаю производственный календарь за {year} год...", "", 0, 0, self)
        progress.setWindowTitle("Производственный календарь")
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        progress.repaint()
        try:
            imported_count = self.directory_service.import_production_calendar(year)
        except Exception as exc:
            progress.close()
            self._info(str(exc))
            return
        progress.close()
        self.refresh()
        self._info(f"Загружено дней: {imported_count}")

    def _build_layout(self) -> None:
        product_filter_layout = QHBoxLayout(self.product_filter_panel)
        product_filter_layout.setContentsMargins(0, 0, 0, 0)
        product_filter_layout.addWidget(QLabel("Объект"))
        product_filter_layout.addWidget(self.product_object_filter, 1)
        product_filter_layout.addWidget(QLabel("Сортировка"))
        product_filter_layout.addWidget(self.product_sort)
        product_filter_layout.addStretch()

        calendar_top = QHBoxLayout()
        calendar_top.addWidget(QLabel("Год"))
        calendar_top.addWidget(self.calendar_year)
        calendar_top.addWidget(self.calendar_import_button)
        calendar_top.addStretch()

        calendar_grid = QGridLayout()
        calendar_grid.setHorizontalSpacing(12)
        calendar_grid.setVerticalSpacing(12)
        for index, calendar in enumerate(self.calendar_widgets):
            month_card = QWidget()
            month_layout = QVBoxLayout(month_card)
            month_layout.setContentsMargins(0, 0, 0, 0)
            month_layout.setSpacing(4)
            month_title = QLabel(MONTH_NAMES[index])
            month_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            month_title.setStyleSheet("font-weight: 600;")
            month_layout.addWidget(month_title)
            month_layout.addWidget(calendar)
            calendar_grid.addWidget(month_card, index // 4, index % 4)

        calendar_editor = QFormLayout()
        calendar_editor.addRow("Дата", self.calendar_day_label)
        calendar_editor.addRow("Информация", self.calendar_day_status)

        calendar_layout = QVBoxLayout(self.calendar_panel)
        calendar_layout.setContentsMargins(0, 0, 0, 0)
        calendar_layout.addLayout(calendar_top)
        calendar_layout.addWidget(self.calendar_legend)
        calendar_layout.addLayout(calendar_grid)
        calendar_layout.addLayout(calendar_editor)

        buttons = QHBoxLayout()
        for button in (
            self.add_button,
            self.rename_button,
            self.disable_button,
            self.restore_button,
            self.delete_button,
            self.move_up_button,
            self.move_down_button,
            self.use_database_button,
            self.check_database_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        right = QVBoxLayout()
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.show_inactive)
        right.addWidget(self.title_label)
        right.addLayout(filters)
        right.addWidget(self.product_filter_panel)
        right.addWidget(self.table)
        right.addWidget(self.calendar_panel)
        right.addLayout(buttons)
        content = QHBoxLayout(self)
        content.addWidget(self.navigation)
        content.addLayout(right)

    def _connect(self) -> None:
        self.navigation.currentRowChanged.connect(self._navigation_changed)
        self.search.textChanged.connect(self.refresh)
        self.show_inactive.toggled.connect(self._show_inactive_changed)
        self.add_button.clicked.connect(self._add_item)
        self.rename_button.clicked.connect(self._rename_item)
        self.disable_button.clicked.connect(lambda: self._set_active(False))
        self.restore_button.clicked.connect(lambda: self._set_active(True))
        self.delete_button.clicked.connect(self._delete_item)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))
        self.use_database_button.clicked.connect(self._use_selected_database)
        self.check_database_button.clicked.connect(self._check_selected_database)
        self.close_button.clicked.connect(self.accept)
        self.table.doubleClicked.connect(self._toggle_selected_active)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.product_object_filter.currentIndexChanged.connect(self.refresh)
        self.product_sort.currentIndexChanged.connect(self._product_sort_changed)
        self.calendar_year.valueChanged.connect(self._calendar_year_changed)
        self.calendar_import_button.clicked.connect(self._import_calendar_year)

    def _fill_navigation(self) -> None:
        for key, label in self.directory_labels.items():
            item = QListWidgetItem(_directory_icon(key), label)
            item.setToolTip(label)
            self.navigation.addItem(item)
        keys = list(self.directory_labels)
        self.navigation.setCurrentRow(keys.index(self.current_key))

    def _navigation_changed(self, row: int) -> None:
        keys = list(self.directory_labels)
        if 0 <= row < len(keys):
            self._remember_column_widths()
            self.current_key = keys[row]
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._sync_show_inactive_checkbox()
            self.refresh()

    def _sync_show_inactive_checkbox(self) -> None:
        self.show_inactive.blockSignals(True)
        self.show_inactive.setChecked(self._show_inactive_by_key.get(self.current_key, True))
        self.show_inactive.blockSignals(False)

    def _show_inactive_changed(self, checked: bool) -> None:
        self._show_inactive_by_key[self.current_key] = checked
        self.directory_service.set_ui_setting(
            f"directory/show_inactive/{self.current_key}",
            "1" if checked else "0",
        )
        self.refresh()

    def _product_sort_changed(self) -> None:
        self.directory_service.set_ui_setting(
            "directory/products/sort",
            str(self.product_sort.currentData() or "manual"),
        )
        self.refresh()

    def _selected_id(self) -> int | str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        if self.current_key == "pay_rates":
            item = self._selected_item()
            return item.position_id if item else None
        cell = self.table.item(row, 0)
        if cell is None:
            return None
        return cell.data(Qt.ItemDataRole.UserRole)

    def _selected_name(self) -> str:
        if self.current_key == "aliases":
            item = self._selected_item()
            return item.original_alias if item else ""
        if self.current_key == "databases":
            item = self._selected_item()
            return item.name if item else ""
        if self.current_key == "products":
            item = self._selected_item()
            return item.name if item else ""
        if self.current_key == "pay_rates":
            item = self._selected_item()
            return item.position_name if item else ""
        row = self.table.currentRow()
        cell = self.table.item(row, 0)
        return cell.text() if row >= 0 and cell else ""

    def _selected_item(self):
        row = self.table.currentRow()
        if self.current_key == "pay_rates":
            return self._row_items[row] if 0 <= row < len(self._row_items) else None
        return self._items[row] if 0 <= row < len(self._items) else None

    def _position_groups(self, current_group: str = "") -> list[DirectoryItem | str]:
        groups: list[DirectoryItem | str] = list(self.directory_service.list("employee_groups"))
        normalized = current_group.strip().casefold()
        if normalized and all(getattr(group, "name", str(group)).casefold() != normalized for group in groups):
            groups.append(current_group.strip())
        return groups

    def _alias_targets(self) -> dict[str, list[tuple[int, str]]]:
        employees = self.employee_service.list() if self.employee_service is not None else []
        return {
            "employee": [(item.id, item.full_name) for item in employees if item.id is not None],
            "object": [
                (item.id, item.name)
                for item in self.directory_service.list_all("objects")
                if item.id is not None
            ],
            "location": [
                (item.id, item.name)
                for item in self.directory_service.list_all("locations")
                if item.id is not None
            ],
            "work_type": [
                (item.id, item.name)
                for item in self.directory_service.list_all("work_types")
                if item.id is not None
            ],
            "product": [
                (item.id, f"{item.object_name} / {item.name}" if item.object_name else item.name)
                for item in self.directory_service.list_products(active_only=False)
                if item.id is not None
            ],
        }

    def _add_item(self) -> None:
        if self.current_key == "aliases":
            dialog = AliasDialog(self._alias_targets(), parent=self)
            if dialog.exec():
                try:
                    self.directory_service.save_alias(dialog.value())
                except ValueError as exc:
                    self._info(str(exc))
                    return
                self.refresh()
            return
        if self.current_key == "databases":
            dialog = DatabaseSourceDialog(parent=self)
            if dialog.exec():
                try:
                    self.database_registry.add(*dialog.values())
                except ValueError as exc:
                    self._info(str(exc))
                    return
                self.refresh()
            return
        if self.current_key == "pay_rates":
            self._info("Строки оплаты формируются автоматически из активных должностей и их категорий")
            return
        if self.current_key == "calendar":
            self._info("Календарь загружается по году и доступен только для просмотра")
            return
        if self.current_key == "products":
            objects = self.directory_service.list_all("objects")
            if not objects:
                self._info("Сначала добавьте объект")
                return
            dialog = ProductDialog(objects, parent=self)
            if dialog.exec():
                self._save_product(dialog.value())
            return
        if self.current_key == "positions":
            dialog = PositionDialog(parent=self, groups=self._position_groups())
            if dialog.exec():
                name, category, student_allowed, salary, salary_type, group, is_active = dialog.values()
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
                    self.directory_service.set_active("positions", item_id, is_active)
                self.refresh()
            return
        if self.current_key == "objects":
            dialog = ObjectDialog(parent=self)
            if dialog.exec():
                values = dialog.values()
                item_id = self.directory_service.ensure("objects", values[0])
                if item_id is not None:
                    self.directory_service.update_object_details(item_id, *values)
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
        if self.current_key == "aliases":
            alias = self._selected_item()
            if alias is None:
                return
            dialog = AliasDialog(self._alias_targets(), alias, self)
            if dialog.exec():
                try:
                    self.directory_service.save_alias(
                        dialog.value(), alias.alias_type, alias.alias_normalized
                    )
                except ValueError as exc:
                    self._info(str(exc))
                    return
                self.refresh()
            return
        if self.current_key == "databases":
            source = self._selected_item()
            if source is None:
                return
            dialog = DatabaseSourceDialog(source, self)
            if dialog.exec():
                try:
                    self.database_registry.update(source.id, *dialog.values())
                    self._database_statuses.pop(source.id, None)
                except ValueError as exc:
                    self._info(str(exc))
                    return
                self.refresh()
            return
        if self.current_key == "pay_rates":
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            dialog = PayRateDialog(item, self)
            if dialog.exec():
                self._save_pay_rate_group(item, dialog.values())
                self.refresh()
            return
        if self.current_key == "calendar":
            self._info("Календарь доступен только для просмотра")
            return
        if self.current_key == "products":
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            objects = self.directory_service.list_all("objects")
            dialog = ProductDialog(objects, item, self)
            if dialog.exec():
                self._save_product(dialog.value())
            return
        if self.current_key == "positions":
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            dialog = PositionDialog(item, self, groups=self._position_groups(item.group))
            if dialog.exec():
                name, category, student_allowed, salary, salary_type, group, is_active = dialog.values()
                self.directory_service.update_position_details(
                    item_id,
                    name,
                    category,
                    student_allowed,
                    salary,
                    salary_type,
                    group,
                )
                self.directory_service.set_active("positions", item_id, is_active)
                self.refresh()
            return
        if self.current_key == "objects":
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            dialog = ObjectDialog(item, self)
            if dialog.exec():
                self.directory_service.update_object_details(item_id, *dialog.values())
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
        if self.current_key == "calendar":
            self._info("Календарь доступен только для просмотра")
            return
        item_id = self._selected_id()
        if item_id is None:
            self._info("Выберите строку")
            return
        if self.current_key == "products":
            self.directory_service.set_product_active(item_id, is_active)
            self.refresh()
            return
        self.directory_service.set_active(self.current_key, item_id, is_active)
        self.refresh()

    def _save_calendar_day(self, calendar_day: WorkCalendarDay) -> None:
        try:
            self.directory_service.save_calendar_day(calendar_day)
        except ValueError as exc:
            self._info(str(exc))
            return
        self.refresh()

    def _save_product(self, product: ProductItem) -> None:
        try:
            self.directory_service.save_product(product)
        except ValueError as exc:
            self._info(str(exc))
            return
        self.refresh()

    def _save_pay_rate_group(
        self,
        group: PayRateGroup,
        values: tuple[str, dict[str, str], dict[str, str], str, str, str],
    ) -> None:
        salary_type, amounts, far_trip_amounts, near_trip, holiday, saturday = values
        for category, rate in group.rates.items():
            if rate.id is None:
                continue
            salary = amounts.get(category, rate.salary)
            far_trip_salary = far_trip_amounts.get(category, rate.far_trip_salary)
            self.directory_service.update_pay_rate(
                rate.id,
                salary,
                far_trip_salary,
                salary_type,
                "1",
                near_trip,
                holiday,
                saturday,
            )

    def _delete_item(self) -> None:
        if self.current_key == "aliases":
            alias = self._selected_item()
            if alias is None:
                self._info("Выберите строку")
                return
            if not self._ask(f"Удалить алиас '{alias.original_alias}'?"):
                return
            self.directory_service.delete_alias(alias.alias_type, alias.alias_normalized)
            self.refresh()
            return
        if self.current_key == "databases":
            source = self._selected_item()
            if source is None:
                self._info("Выберите строку")
                return
            if self._database_selection_text(source):
                self._info("Сначала выберите другую базу этого назначения")
                return
            if not self._ask(f"Удалить '{source.name}' из реестра? Сам файл удален не будет."):
                return
            self.database_registry.remove(source.id)
            self._database_statuses.pop(source.id, None)
            self.refresh()
            return
        if self.current_key == "pay_rates":
            self._info("Строки оплаты удаляются из списка автоматически при изменении должностей или категорий")
            return
        if self.current_key == "calendar":
            self._info("Календарь доступен только для просмотра")
            return
        if self.current_key == "products":
            item_id = self._selected_id()
            if item_id is None:
                self._info("Выберите строку")
                return
            if not self._ask(f"Удалить изделие '{self._selected_name()}'?"):
                return
            try:
                self.directory_service.delete_product(item_id)
            except ValueError as exc:
                self._info(str(exc))
                return
            self.refresh()
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

    def _move_selected(self, direction: int) -> None:
        if self.current_key not in {"locations", "work_types", "objects", "products"}:
            return
        if self.current_key == "products" and self.product_sort.currentData() != "manual":
            self._info("Для ручного перемещения выберите сортировку «Заданный порядок»")
            return
        item_id = self._selected_id()
        if item_id is None:
            self._info("Выберите строку")
            return
        active_only = not self.show_inactive.isChecked()
        if self.current_key == "products":
            self.directory_service.move_product(item_id, direction, active_only=active_only)
        else:
            self.directory_service.move(
                self.current_key,
                item_id,
                direction,
                active_only=active_only,
            )
        self.refresh()
        self._select_row_by_id(item_id)

    def _check_selected_database(self) -> None:
        source = self._selected_item()
        if self.current_key != "databases" or source is None:
            self._info("Выберите базу данных")
            return
        result = self.database_registry.check(source.path, source.kind)
        self._database_statuses[source.id] = result
        self.refresh()
        self._select_row_by_id(source.id)
        self._info(result[1])

    def _use_selected_database(self) -> None:
        source = self._selected_item()
        if self.current_key != "databases" or source is None:
            self._info("Выберите базу данных")
            return
        available, message = self.database_registry.check(source.path, source.kind)
        self._database_statuses[source.id] = (available, message)
        if not available:
            self.refresh()
            self._info(message)
            return
        if source.kind == PROLOG_DATABASE:
            self.app_settings.prolog_database_path = source.path
        elif source.kind == EMPLOYEES_DATABASE:
            self.app_settings.employees_database_path = source.path
        elif source.kind == OBJECTS_DATABASE:
            self.app_settings.objects_database_path = source.path
        elif source.kind == PRODUCTS_DATABASE:
            self.app_settings.products_database_path = source.path
        elif source.kind == ALIASES_DATABASE:
            self.app_settings.aliases_database_path = source.path
        else:
            self.app_settings.workbot_database_path = source.path
            result_message = "База WorkBot выбрана."
        if source.kind != WORKBOT_DATABASE:
            current_path = self.current_database_paths.get(source.kind)
            result_message = (
                f"Компонент «{DATABASE_KIND_LABELS[source.kind]}» выбран и будет подключен после "
                "перезапуска программы. Для сетевого файла необходимы стабильное подключение и "
                "регулярные резервные копии."
                if not current_path or not _same_path(source.path, str(current_path))
                else "Эта база уже используется."
            )
        self.config_manager.save(self.app_settings)
        self.refresh()
        self._select_row_by_id(source.id)
        self._info(result_message)

    def _select_row_by_id(self, item_id: int) -> None:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell and cell.data(Qt.ItemDataRole.UserRole) == item_id:
                self.table.selectRow(row)
                self.table.scrollToItem(cell)
                return

    def _toggle_selected_active(self) -> None:
        row = self.table.currentRow()
        max_rows = len(self._row_items) if self.current_key == "pay_rates" else len(self._items)
        if row < 0 or row >= max_rows:
            return
        self._rename_item()

    def _show_context_menu(self, position) -> None:
        menu = QMenu(self)
        add_action = QAction("Добавить", self)
        rename_action = QAction("Редактировать", self)
        disable_action = QAction("Отключить", self)
        restore_action = QAction("Активировать", self)
        delete_action = QAction("Удалить", self)
        move_up_action = QAction("Переместить вверх", self)
        move_down_action = QAction("Переместить вниз", self)
        has_item = self._selected_id() is not None
        if self.current_key == "databases":
            use_action = QAction("Использовать", self)
            check_action = QAction("Проверить", self)
            for action in (rename_action, delete_action, use_action, check_action):
                action.setEnabled(has_item)
            rename_action.triggered.connect(self._rename_item)
            delete_action.triggered.connect(self._delete_item)
            use_action.triggered.connect(self._use_selected_database)
            check_action.triggered.connect(self._check_selected_database)
            menu.addAction(rename_action)
            menu.addAction(use_action)
            menu.addAction(check_action)
            menu.addSeparator()
            menu.addAction(delete_action)
            menu.exec(self.table.viewport().mapToGlobal(position))
            return
        if self.current_key == "aliases":
            for action in (rename_action, delete_action):
                action.setEnabled(has_item)
            add_action.triggered.connect(self._add_item)
            rename_action.triggered.connect(self._rename_item)
            delete_action.triggered.connect(self._delete_item)
            menu.addAction(add_action)
            menu.addAction(rename_action)
            menu.addSeparator()
            menu.addAction(delete_action)
            menu.exec(self.table.viewport().mapToGlobal(position))
            return
        if self.current_key == "pay_rates":
            rename_action.setEnabled(has_item)
            rename_action.triggered.connect(self._rename_item)
            menu.addAction(rename_action)
            menu.exec(self.table.viewport().mapToGlobal(position))
            return
        if self.current_key == "calendar":
            return
        for action in (rename_action, disable_action, restore_action, delete_action):
            action.setEnabled(has_item)
        move_up_action.setEnabled(has_item)
        move_down_action.setEnabled(has_item)
        add_action.triggered.connect(self._add_item)
        rename_action.triggered.connect(self._rename_item)
        disable_action.triggered.connect(lambda: self._set_active(False))
        restore_action.triggered.connect(lambda: self._set_active(True))
        delete_action.triggered.connect(self._delete_item)
        move_up_action.triggered.connect(lambda: self._move_selected(-1))
        move_down_action.triggered.connect(lambda: self._move_selected(1))
        menu.addAction(add_action)
        menu.addAction(rename_action)
        manual_order_available = self.current_key in {
            "locations",
            "work_types",
            "objects",
            "products",
        }
        if self.current_key == "products" and self.product_sort.currentData() != "manual":
            manual_order_available = False
        if manual_order_available:
            menu.addSeparator()
            menu.addAction(move_up_action)
            menu.addAction(move_down_action)
        menu.addSeparator()
        menu.addAction(disable_action)
        menu.addAction(restore_action)
        menu.addSeparator()
        menu.addAction(delete_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _configure_table(self) -> None:
        self.table.clearSpans()
        if self.current_key == "positions":
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
        elif self.current_key == "pay_rates":
            self.table.setColumnCount(8)
            self.table.setHorizontalHeaderLabels(
                [
                    "Должность",
                    "Разряд",
                    "Тип",
                    "Ставка/зарплата",
                    "Командировка дальняя",
                    "КТУ командировка ближняя",
                    "КТУ воскр./празд.",
                    "КТУ Суббота",
                ]
            )
            for column in range(8):
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(0, 280)
            self.table.setColumnWidth(1, 150)
            self.table.setColumnWidth(2, 92)
            self.table.setColumnWidth(3, 130)
            self.table.setColumnWidth(4, 165)
            self.table.setColumnWidth(5, 205)
            self.table.setColumnWidth(6, 155)
            self.table.setColumnWidth(7, 120)
        elif self.current_key == "objects":
            self.table.setColumnCount(11)
            self.table.setHorizontalHeaderLabels(
                [
                    "Объект",
                    "№ проекта",
                    "№ договора",
                    "Заказчик",
                    "Состояние",
                    "Предмет договора",
                    "Тип объекта",
                    "Подтип",
                    "Дата окончания",
                    "Осталось",
                    "Статус",
                ]
            )
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            for column in range(1, 11):
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, 210)
            self.table.setColumnWidth(1, 105)
            self.table.setColumnWidth(2, 105)
            self.table.setColumnWidth(3, 140)
            self.table.setColumnWidth(4, 120)
            self.table.setColumnWidth(5, 125)
            self.table.setColumnWidth(6, 135)
            self.table.setColumnWidth(7, 90)
            self.table.setColumnWidth(8, 105)
            self.table.setColumnWidth(9, 100)
            self.table.setColumnWidth(10, 88)
        elif self.current_key == "products":
            self.table.setColumnCount(9)
            self.table.setHorizontalHeaderLabels(
                ["Объект", "Зав. №", "Наименование", "Шифр", "Состояние", "Готовность", "Начало", "Выпуск", "Статус"]
            )
            for column in range(9):
                self.table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.Interactive
                )
            self.table.setColumnWidth(0, 220)
            self.table.setColumnWidth(1, 110)
            self.table.setColumnWidth(2, 240)
            self.table.setColumnWidth(3, 110)
            self.table.setColumnWidth(4, 125)
            self.table.setColumnWidth(5, 90)
            self.table.setColumnWidth(6, 95)
            self.table.setColumnWidth(7, 95)
            self.table.setColumnWidth(8, 88)
        elif self.current_key == "calendar":
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["Дата", "Тип дня", "Примечание", "Оплата"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, 110)
            self.table.setColumnWidth(1, 190)
            self.table.setColumnWidth(3, 210)
        elif self.current_key == "databases":
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(
                ["Название", "Назначение", "Расположение", "Состояние", "Используется"]
            )
            for column in range(5):
                self.table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.Interactive
                )
            self.table.setColumnWidth(0, 220)
            self.table.setColumnWidth(1, 170)
            self.table.setColumnWidth(2, 520)
            self.table.setColumnWidth(3, 220)
            self.table.setColumnWidth(4, 160)
        elif self.current_key == "aliases":
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(["Тип", "Алиас", "Соответствует"])
            for column in range(3):
                self.table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.Interactive
                )
            self.table.setColumnWidth(0, 180)
            self.table.setColumnWidth(1, 360)
            self.table.setColumnWidth(2, 480)
        elif self.current_key == "employee_groups":
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Блок/Группа", "Статус"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(1, 130)
        else:
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Название", "Статус"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(1, 130)
        self.table.horizontalHeader().setStretchLastSection(False)

    def _remember_column_widths(self) -> None:
        if not self._configured_key or self.table.columnCount() <= 0:
            return
        widths = [
            self.table.columnWidth(column) for column in range(self.table.columnCount())
        ]
        self._column_widths_by_key[self._configured_key] = widths
        self.directory_service.set_ui_setting(
            f"directory/column_widths/{self._configured_key}",
            ",".join(str(width) for width in widths),
        )

    def _restore_column_widths(self) -> None:
        widths = self._column_widths_by_key.get(self.current_key, [])
        if len(widths) != self.table.columnCount():
            return
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)

    def done(self, result: int) -> None:
        self._remember_column_widths()
        super().done(result)

    def _status_column(self) -> int:
        if self.current_key == "positions":
            return 4
        if self.current_key == "objects":
            return 10
        if self.current_key == "products":
            return 8
        return 1

    def _update_button_visibility(self) -> None:
        is_pay_rates = self.current_key == "pay_rates"
        is_calendar = self.current_key == "calendar"
        is_products = self.current_key == "products"
        is_databases = self.current_key == "databases"
        is_aliases = self.current_key == "aliases"
        self.search.setVisible(not is_calendar)
        self.show_inactive.setVisible(
            not is_pay_rates and not is_calendar and not is_databases and not is_aliases
        )
        self.table.setVisible(not is_calendar)
        self.calendar_panel.setVisible(is_calendar)
        self.product_filter_panel.setVisible(is_products)
        self.add_button.setVisible(not is_pay_rates and not is_calendar and not is_databases)
        self.rename_button.setVisible(not is_calendar)
        self.disable_button.setVisible(
            not is_pay_rates and not is_calendar and not is_databases and not is_aliases
        )
        self.restore_button.setVisible(
            not is_pay_rates and not is_calendar and not is_databases and not is_aliases
        )
        self.delete_button.setVisible(not is_pay_rates and not is_calendar)
        is_ordered = self.current_key in {"locations", "work_types", "objects", "products"}
        if self.current_key == "products" and self.product_sort.currentData() != "manual":
            is_ordered = False
        self.move_up_button.setVisible(is_ordered)
        self.move_down_button.setVisible(is_ordered)
        self.use_database_button.setVisible(is_databases)
        self.check_database_button.setVisible(is_databases)

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


def _parse_column_widths(value: str) -> list[int]:
    try:
        widths = [int(part) for part in value.split(",") if part.strip()]
    except ValueError:
        return []
    return widths if all(width > 0 for width in widths) else []


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


def _same_path(first: str, second: str) -> bool:
    if not first or not second:
        return False
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def _directory_icon(key: str) -> QIcon:
    icon_file = DIRECTORY_ICONS_DIR / f"{key}.png"
    if icon_file.exists():
        source = QPixmap(str(icon_file))
        if not source.isNull():
            canvas = QPixmap(24, 24)
            canvas.fill(Qt.GlobalColor.transparent)
            scaled = source.scaled(
                22,
                22,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter = QPainter(canvas)
            painter.drawPixmap((24 - scaled.width()) // 2, (24 - scaled.height()) // 2, scaled)
            painter.end()
            return QIcon(canvas)
    colors = {
        "locations": "#2f80ed",
        "work_types": "#7c5cc4",
        "employee_groups": "#5966b3",
        "positions": "#2f6f73",
        "pay_rates": "#a46a12",
        "objects": "#3d6f9f",
        "products": "#6c7a2f",
        "calendar": "#8a4b6f",
        "aliases": "#477a69",
        "databases": "#4f6475",
    }
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colors.get(key, "#52606d")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 20, 20, 5, 5)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#ffffff"), 1.6))
    if key == "locations":
        painter.drawEllipse(9, 5, 6, 6)
        painter.drawLine(12, 11, 12, 18)
        painter.drawLine(9, 15, 12, 18)
        painter.drawLine(15, 15, 12, 18)
    elif key == "work_types":
        painter.drawLine(7, 16, 16, 7)
        painter.drawLine(14, 7, 18, 11)
        painter.drawLine(6, 15, 9, 18)
    elif key == "employee_groups":
        painter.drawEllipse(6, 6, 5, 5)
        painter.drawEllipse(13, 6, 5, 5)
        painter.drawLine(8, 12, 8, 17)
        painter.drawLine(15, 12, 15, 17)
        painter.drawLine(8, 14, 15, 14)
    elif key == "positions":
        painter.drawRect(6, 9, 12, 8)
        painter.drawLine(9, 9, 9, 7)
        painter.drawLine(9, 7, 15, 7)
        painter.drawLine(15, 7, 15, 9)
    elif key == "pay_rates":
        painter.drawEllipse(6, 5, 12, 12)
        painter.drawLine(12, 8, 12, 15)
    elif key == "databases":
        painter.drawEllipse(6, 5, 12, 5)
        painter.drawRect(6, 7, 12, 9)
        painter.drawArc(6, 13, 12, 5, 0, -180 * 16)
        painter.drawLine(9, 10, 15, 10)
    elif key == "aliases":
        painter.drawEllipse(5, 8, 7, 7)
        painter.drawEllipse(12, 8, 7, 7)
        painter.drawLine(10, 11, 14, 11)
    elif key == "objects":
        painter.drawRect(6, 6, 12, 12)
        for x in (9, 12, 15):
            painter.drawLine(x, 8, x, 16)
        painter.drawLine(6, 12, 18, 12)
    elif key == "products":
        painter.drawRect(7, 8, 10, 9)
        painter.drawLine(7, 8, 12, 5)
        painter.drawLine(17, 8, 12, 5)
        painter.drawLine(12, 5, 12, 14)
    elif key == "calendar":
        painter.drawRect(5, 7, 14, 11)
        painter.drawLine(5, 10, 19, 10)
        painter.drawLine(9, 5, 9, 8)
        painter.drawLine(15, 5, 15, 8)
    painter.end()
    return QIcon(pixmap)


def _salary_display(value: str, salary_type: str) -> str:
    if not value.strip():
        return ""
    suffix = " / мес" if salary_type == "monthly" else " / час"
    return f"{value.strip()}{suffix}"


def _pay_rate_groups(rates: list[PayRate]) -> list[PayRateGroup]:
    groups: dict[int, PayRateGroup] = {}
    for rate in rates:
        group = groups.setdefault(rate.position_id, PayRateGroup(rate.position_id, rate.position_name, {}))
        group.rates[rate.category] = rate
    return sorted(groups.values(), key=lambda group: group.position_name.casefold())


def _pay_rate_row_categories(group: PayRateGroup) -> list[str]:
    order = [STUDENT_CATEGORY, "1", "2", "3", NO_CATEGORY]
    known = [category for category in order if category in group.rates]
    unknown = sorted((category for category in group.rates if category not in order), key=str.casefold)
    return [*known, *unknown]


def _pay_category_title(category: str) -> str:
    if category == STUDENT_CATEGORY:
        return "Ученик/стажер"
    if category == NO_CATEGORY:
        return "Без разряда"
    return f"Разряд {category}"


def _pay_category_row_title(category: str, position_name: str = "") -> str:
    if category == STUDENT_CATEGORY:
        return "Ученик/стажер"
    if category == NO_CATEGORY:
        return "—"
    title = "Категория" if _is_asutp_engineer(position_name) else "Разряд"
    return f"{title} {category}"


def _is_asutp_engineer(position: str) -> bool:
    normalized = position.strip().casefold()
    return "инженер" in normalized and "асутп" in normalized


def _format_money(value: str) -> str:
    amount = _parse_decimal(value)
    if amount is None:
        return value.strip()
    text = f"{amount:,.2f}"
    return text.replace(",", " ").replace(".", ",")


def _format_coefficient(value: str) -> str:
    normalized = value.strip().replace(".", ",")
    return normalized or "1"


def _parse_decimal(value: str) -> Decimal | None:
    normalized = value.strip().replace(" ", "").replace(",", ".")
    if not normalized or normalized == "—":
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _display_date(value: str) -> str:
    date_value = QDate.fromString(value, "yyyy-MM-dd")
    return date_value.toString("dd.MM.yyyy") if date_value.isValid() else ""


def _days_left_display(value: str) -> str:
    date_value = QDate.fromString(value, "yyyy-MM-dd")
    if not date_value.isValid():
        return ""
    days = QDate.currentDate().daysTo(date_value)
    if days > 0:
        return f"{days} дн."
    if days == 0:
        return "Сегодня"
    return f"Проср. {abs(days)} дн."


def _is_overdue(value: str) -> bool:
    date_value = QDate.fromString(value, "yyyy-MM-dd")
    return date_value.isValid() and QDate.currentDate().daysTo(date_value) < 0


def _display_calendar_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _date_to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _qdate_to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _default_calendar_day_type(value: date) -> str:
    if _fixed_ru_holiday_name(value):
        return WorkDayType.HOLIDAY.value
    return WorkDayType.DAY_OFF.value if value.weekday() >= 5 else WorkDayType.WORKDAY.value


def _calendar_visual_day_type(value: date, day_type: str) -> str:
    if _fixed_ru_holiday_name(value) and day_type in {WorkDayType.DAY_OFF.value, WorkDayType.HOLIDAY.value}:
        return WorkDayType.HOLIDAY.value
    return day_type


def _calendar_day_description(value: date, calendar_day: WorkCalendarDay | None) -> str:
    day_type = calendar_day.day_type if calendar_day else _default_calendar_day_type(value)
    holiday_name = _fixed_ru_holiday_name(value)
    if day_type == WorkDayType.WORKDAY.value:
        return "Рабочий день"
    if day_type == WorkDayType.SHORTENED_WORKDAY.value:
        return "Рабочий день: сокращенный предпраздничный день"
    if day_type == WorkDayType.WORKING_SATURDAY.value:
        return "Рабочий день: рабочая суббота"
    if day_type == WorkDayType.WORKING_HOLIDAY.value:
        return "Рабочий день: рабочий выходной или праздничный день"
    if day_type == WorkDayType.HOLIDAY.value or holiday_name:
        suffix = holiday_name or "праздничный день по производственному календарю"
        return f"Нерабочий день: праздник - {suffix}"
    return "Нерабочий день: выходной"


def _fixed_ru_holiday_name(value: date) -> str:
    return FIXED_RU_HOLIDAYS.get((value.month, value.day), "")


def _calendar_date_format(day_type: str) -> QTextCharFormat:
    palette = {
        WorkDayType.DAY_OFF.value: ("#f1f3f5", "#6b7280"),
        WorkDayType.HOLIDAY.value: ("#ffe4e6", "#b42318"),
        WorkDayType.WORKING_SATURDAY.value: ("#dcfce7", "#166534"),
        WorkDayType.WORKING_HOLIDAY.value: ("#e0f2fe", "#075985"),
        WorkDayType.SHORTENED_WORKDAY.value: ("#fff7cc", "#92400e"),
    }
    background, foreground = palette.get(day_type, ("#ffffff", "#111827"))
    text_format = QTextCharFormat()
    text_format.setBackground(QBrush(QColor(background)))
    text_format.setForeground(QBrush(QColor(foreground)))
    return text_format


def _calendar_payment_mode(day_type: str) -> str:
    if day_type == WorkDayType.WORKING_SATURDAY.value:
        return "КТУ суббота"
    if day_type in {WorkDayType.DAY_OFF.value, WorkDayType.HOLIDAY.value, WorkDayType.WORKING_HOLIDAY.value}:
        return "КТУ воскр./празд."
    return "Обычная ставка"


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


def _sort_products(items: list[ProductItem], mode: str) -> list[ProductItem]:
    if mode == "manual":
        return items
    if mode.startswith("serial_"):
        with_serial = [item for item in items if item.serial_number.strip()]
        without_serial = [item for item in items if not item.serial_number.strip()]
        ordered = sorted(
            with_serial,
            key=lambda item: (
                _natural_sort_key(item.serial_number),
                _natural_sort_key(item.name),
            ),
            reverse=mode.endswith("_desc"),
        )
        return ordered + without_serial
    else:
        ordered = sorted(
            items,
            key=lambda item: (
                item.readiness_percent,
                _natural_sort_key(item.serial_number),
            ),
        )
    return list(reversed(ordered)) if mode.endswith("_desc") else ordered


def _natural_sort_key(value: str) -> tuple[tuple[int, object], ...]:
    parts = re.split(r"(\d+)", value.casefold().strip())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
        if part
    )


HELP_HTML = f"""
<h2>{APP_NAME}</h2>
<p><b>Назначение:</b> ежедневный сбор и унификация записей о выполненных работах.</p>
<h3>Основной порядок работы</h3>
<ol>
  <li>При первом запуске зарегистрируйте организацию, руководителя и пользователя. Далее вход выполняется по паролю.</li>
  <li>При первом запуске пройдите первичную настройку: сотрудники, должности, виды работ и объекты.</li>
  <li>Выберите сотрудника слева и дату в рабочей области.</li>
  <li>Заполните объект, вид работ, описание, часы и комментарий.</li>
  <li>Сохраните запись. Ниже появится список выполненных работ выбранного сотрудника.</li>
  <li>Используйте меню <b>Экспорт</b> для формирования отчета или сменного задания.</li>
</ol>
<h3>Производственный календарь и аналитика</h3>
<p>В меню <b>Настройки - Справочники</b> можно открыть производственный календарь:
рабочие дни, выходные, праздники и рабочие субботы. Эти настройки используются
во вкладке <b>Аналитика</b> при расчете часов и оплаты.</p>
<h3>Импорт сотрудников</h3>
<p>Excel-файл должен содержать колонки: №, ФИО, Должность, Разряд.</p>
<h3>Импорт старых отчетов</h3>
<p>После завершения мастера настройки используйте <b>Файл - Импорт старых отчетов Excel</b>.
Перед переносом программа покажет ошибки, пропущенные дни, неизвестных сотрудников и новые объекты.</p>
<h3>Версия</h3>
<p>{APP_VERSION}</p>
"""
