"""Reusable dialogs."""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QIntValidator, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
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
from category_rules import NO_CATEGORY, STUDENT_CATEGORY
from models import AppSettings, DirectoryItem, Employee, ObjectStatus, PayRate, ProductItem, ProductStatus, WorkCalendarDay, WorkDayType
from requisites import RequisitesOptions
from services import category_values_from_rule
from update_checker import UpdateChecker


PAY_CATEGORY_COLUMNS = (STUDENT_CATEGORY, "1", "2", "3")


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
            group = position.group or "Рабочие"
            self.group.setCurrentText(group if group in self.GROUPS else "Рабочие")
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
        layout.addRow("Категории", self.category)
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
            self.group.currentText().strip() or "Рабочие",
            self.is_active,
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
        self.status_label = QLabel()
        self.toggle_active_button = QPushButton()
        self.toggle_active_button.clicked.connect(self._toggle_active)
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
        self._sync_active_controls()

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
        layout.addRow("Статус", self.status_label)
        layout.addRow("", self.toggle_active_button)
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

    def _toggle_active(self) -> None:
        self.is_active = not self.is_active
        self._sync_active_controls()

    def _sync_active_controls(self) -> None:
        self.status_label.setText("Активно" if self.is_active else "Отключено")
        self.toggle_active_button.setText("Отключить" if self.is_active else "Активировать")

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
        self.setMinimumWidth(760)
        self.group = group
        self.position = QLineEdit(group.position_name)
        self.position.setReadOnly(True)
        self.salary_type = QComboBox()
        self.salary_type.addItem("Ставка", "hourly")
        self.salary_type.addItem("Зарплата", "monthly")
        index = self.salary_type.findData(group.salary_type)
        self.salary_type.setCurrentIndex(index if index >= 0 else 0)
        self.category_amounts: dict[str, QLineEdit] = {}
        for category in PAY_CATEGORY_COLUMNS:
            edit = QLineEdit()
            edit.setPlaceholderText("Например: 100000")
            rate = group.rates.get(category)
            if rate:
                edit.setText(rate.salary)
            else:
                edit.setText("—")
                edit.setReadOnly(True)
                edit.setEnabled(False)
            self.category_amounts[category] = edit
        self.far_trip_coeff = QLineEdit(group.far_trip_coeff)
        self.near_trip_coeff = QLineEdit(group.near_trip_coeff)
        self.holiday_coeff = QLineEdit(group.holiday_coeff)
        self.saturday_coeff = QLineEdit(group.saturday_coeff)
        for coeff in (self.far_trip_coeff, self.near_trip_coeff, self.holiday_coeff, self.saturday_coeff):
            coeff.setPlaceholderText("Например: 1,5")

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
        layout.addRow("Тип оплаты", self.salary_type)
        for category, edit in self.category_amounts.items():
            layout.addRow(_pay_category_title(category), edit)
        layout.addRow("КД (дальняя командировка)", self.far_trip_coeff)
        layout.addRow("КТУ КБ (ближняя командировка)", self.near_trip_coeff)
        layout.addRow("КТУ воскр. и праздники", self.holiday_coeff)
        layout.addRow("КТУ суббота", self.saturday_coeff)
        layout.addRow(buttons)

    def values(self) -> tuple[str, dict[str, str], str, str, str, str]:
        return (
            str(self.salary_type.currentData() or "hourly"),
            {
                category: edit.text().strip()
                for category, edit in self.category_amounts.items()
                if category in self.group.rates
            },
            self.far_trip_coeff.text().strip(),
            self.near_trip_coeff.text().strip(),
            self.holiday_coeff.text().strip(),
            self.saturday_coeff.text().strip(),
        )


class DirectoryDialog(QDialog):
    DIRECTORY_LABELS = {
        "locations": "Местонахождения",
        "objects": "Объекты",
        "products": "Изделия",
        "positions": "Должности",
        "pay_rates": "Оплата",
        "calendar": "Календарь рабочего времени",
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
                for item in _pay_rate_groups(self.directory_service.list_pay_rates())
                if not needle or needle in item.position_name.lower()
            ]
            self._refresh_pay_rates()
            return
        if self.current_key == "calendar":
            self._items = [
                item
                for item in self.directory_service.list_calendar_days()
                if (
                    not needle
                    or needle in _display_calendar_date(item.work_date).lower()
                    or needle in item.day_type.lower()
                    or needle in item.note.lower()
                )
            ]
            self._refresh_calendar_days()
            return
        if self.current_key == "products":
            self._items = [
                item
                for item in self.directory_service.list_products()
                if (
                    not needle
                    or needle in item.object_name.lower()
                    or needle in item.serial_number.lower()
                    or needle in item.name.lower()
                    or needle in item.code.lower()
                    or needle in item.product_status.lower()
                )
            ]
            self._refresh_products()
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
        self.table.setRowCount(len(self._items))
        for row, group in enumerate(self._items):
            position = QTableWidgetItem(group.position_name)
            position.setData(Qt.ItemDataRole.UserRole, group.position_id)
            position.setToolTip(group.position_name)
            salary_type = QTableWidgetItem("Зарплата" if group.salary_type == "monthly" else "Ставка")
            salary_type.setToolTip(salary_type.text())
            self.table.setItem(row, 0, position)
            self.table.setItem(row, 1, salary_type)
            for column, category in enumerate(PAY_CATEGORY_COLUMNS, start=2):
                rate = group.rates.get(category)
                value = _format_money(rate.salary) if rate else "—"
                cell = QTableWidgetItem(value)
                cell.setToolTip(cell.text())
                self.table.setItem(row, column, cell)
            coeffs = [group.far_trip_coeff, group.near_trip_coeff, group.holiday_coeff, group.saturday_coeff]
            for column, value in enumerate(coeffs, start=6):
                cell = QTableWidgetItem(_format_coefficient(value))
                cell.setToolTip(cell.text())
                self.table.setItem(row, column, cell)

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
        if self.current_key == "products":
            item = self._selected_item()
            return item.name if item else ""
        row = self.table.currentRow()
        return self.table.item(row, 0).text() if row >= 0 else ""

    def _selected_item(self) -> DirectoryItem | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _add_item(self) -> None:
        if self.current_key == "pay_rates":
            self._info("Строки оплаты формируются автоматически из активных должностей и их категорий")
            return
        if self.current_key == "calendar":
            dialog = CalendarDayDialog(parent=self)
            if dialog.exec():
                self._save_calendar_day(dialog.value())
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
            dialog = PositionDialog(parent=self)
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
            item = self._selected_item()
            if item is None:
                self._info("Выберите строку")
                return
            dialog = CalendarDayDialog(item, self)
            if dialog.exec():
                self._save_calendar_day(dialog.value())
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
            dialog = PositionDialog(item, self)
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
            self._info("У календарного дня нет признака активности. Его можно отредактировать или удалить.")
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
        values: tuple[str, dict[str, str], str, str, str, str],
    ) -> None:
        salary_type, amounts, far_trip, near_trip, holiday, saturday = values
        for category, rate in group.rates.items():
            if rate.id is None:
                continue
            salary = amounts.get(category, rate.salary)
            self.directory_service.update_pay_rate(
                rate.id,
                salary,
                salary_type,
                far_trip,
                near_trip,
                holiday,
                saturday,
            )

    def _delete_item(self) -> None:
        if self.current_key == "pay_rates":
            self._info("Строки оплаты удаляются из списка автоматически при изменении должностей или категорий")
            return
        if self.current_key == "calendar":
            item_id = self._selected_id()
            if item_id is None:
                self._info("Выберите строку")
                return
            if not self._ask(f"Удалить настройку календаря '{self._selected_name()}'?"):
                return
            self.directory_service.delete_calendar_day(item_id)
            self.refresh()
            return
        if self.current_key == "products":
            item_id = self._selected_id()
            if item_id is None:
                self._info("Выберите строку")
                return
            if not self._ask(f"Удалить изделие '{self._selected_name()}'?"):
                return
            self.directory_service.delete_product(item_id)
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

    def _toggle_selected_active(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._items):
            return
        if self.current_key in {"pay_rates", "positions", "objects", "calendar", "products"}:
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
        if self.current_key == "calendar":
            add_action.triggered.connect(self._add_item)
            rename_action.setEnabled(has_item)
            rename_action.triggered.connect(self._rename_item)
            delete_action.setEnabled(has_item)
            delete_action.triggered.connect(self._delete_item)
            menu.addAction(add_action)
            menu.addAction(rename_action)
            menu.addSeparator()
            menu.addAction(delete_action)
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
            self.table.setColumnCount(10)
            self.table.setHorizontalHeaderLabels(
                [
                    "Должность",
                    "Тип",
                    "Категория 0",
                    "Категория 1",
                    "Категория 2",
                    "Категория 3",
                    "КД",
                    "КТУ КБ",
                    "Воскр./празд.",
                    "Суббота",
                ]
            )
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            for column in range(1, 10):
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(0, 260)
            self.table.setColumnWidth(1, 82)
            for column in range(2, 6):
                self.table.setColumnWidth(column, 118)
            self.table.setColumnWidth(6, 64)
            self.table.setColumnWidth(7, 78)
            self.table.setColumnWidth(8, 112)
            self.table.setColumnWidth(9, 82)
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
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            for column in (1, 3, 4, 5, 6, 7, 8):
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
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
        else:
            self.table.setColumnCount(2)
            self.table.setHorizontalHeaderLabels(["Название", "Статус"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(1, 130)
        self.table.horizontalHeader().setStretchLastSection(False)

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
        self.add_button.setVisible(not is_pay_rates)
        self.disable_button.setVisible(not is_pay_rates and not is_calendar)
        self.restore_button.setVisible(not is_pay_rates and not is_calendar)
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


def _pay_rate_groups(rates: list[PayRate]) -> list[PayRateGroup]:
    groups: dict[int, PayRateGroup] = {}
    for rate in rates:
        group = groups.setdefault(rate.position_id, PayRateGroup(rate.position_id, rate.position_name, {}))
        group.rates[rate.category] = rate
    return sorted(groups.values(), key=lambda group: group.position_name.casefold())


def _pay_category_title(category: str) -> str:
    if category == STUDENT_CATEGORY:
        return "Категория 0"
    if category == NO_CATEGORY:
        return "Без категории"
    return f"Категория {category}"


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


HELP_HTML = f"""
<h2>{APP_NAME}</h2>
<p><b>Назначение:</b> ежедневный сбор и унификация записей о выполненных работах.</p>
<h3>Основной порядок работы</h3>
<ol>
  <li>Пройдите авторизацию через меню <b>Файл - Авторизация</b>.</li>
  <li>При первом запуске пройдите первичную настройку: сотрудники, должности, виды работ и объекты.</li>
  <li>Выберите сотрудника слева и дату в рабочей области.</li>
  <li>Заполните объект, вид работ, описание, часы и комментарий.</li>
  <li>Сохраните запись. Ниже появится список выполненных работ выбранного сотрудника.</li>
  <li>Используйте меню <b>Экспорт</b> для формирования отчета или сменного задания.</li>
</ol>
<h3>Календарь и аналитика</h3>
<p>В меню <b>Настройки - Справочники</b> можно вести календарь рабочего времени:
рабочие дни, выходные, праздники и рабочие субботы. Эти настройки используются
во вкладке <b>Аналитика</b> при расчете часов и оплаты.</p>
<h3>Импорт сотрудников</h3>
<p>Excel-файл должен содержать колонки: №, ФИО, Должность, Категория.</p>
<h3>Импорт старых отчетов</h3>
<p>После завершения мастера настройки используйте <b>Файл - Импорт старых отчетов Excel</b>.
Перед переносом программа покажет ошибки, пропущенные дни, неизвестных сотрудников и новые объекты.</p>
<h3>Версия</h3>
<p>{APP_VERSION}</p>
"""
