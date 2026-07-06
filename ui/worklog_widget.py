"""Work log editor widget."""

from __future__ import annotations

from datetime import date
from html import escape

from PySide6.QtCore import QDate, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QApplication,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from models import Employee, WorkLogEntry


class CalendarDateEdit(QDateEdit):
    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            QTimer.singleShot(0, self.open_calendar)

    def open_calendar(self) -> None:
        self.setFocus()
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        QApplication.sendEvent(self, press)
        QApplication.sendEvent(self, release)


class WorkLogWidget(QWidget):
    save_requested = Signal(object)
    validation_failed = Signal(str)
    object_text_submitted = Signal(str)
    objects_directory_requested = Signal()

    ADD_OBJECT_ACTION = "__add_object__"

    def __init__(self) -> None:
        super().__init__()
        self.employee: Employee | None = None
        self.entry_id: int | None = None
        self.date_edit = CalendarDateEdit(QDate.currentDate())
        self._setup_date_edit(self.date_edit)
        self.today_button = QPushButton("Сегодня")
        self.today_button.setMinimumWidth(110)
        self.employee_name = QLabel()
        self.employee_position = QLabel()
        self.employee_category = QLabel()
        self.location = QComboBox()
        self.object = QComboBox()
        self.object.setEditable(False)
        self.work_type = QComboBox()
        for combo in (self.location, self.object, self.work_type):
            combo.setView(QListView())
        self.description = QTextEdit()
        self.description.setMinimumHeight(60)
        self.description.setFixedHeight(110)
        self.hours = QLineEdit("0")
        self.hours.setValidator(QIntValidator(0, 24, self.hours))
        self.hours.setPlaceholderText("0")
        self.comment = QTextEdit()
        self.comment.setMaximumHeight(70)
        self.save_button = QPushButton("Сохранить запись")
        self.clear_button = QPushButton("Очистить форму")
        self.save_button.setMinimumWidth(170)
        self.clear_button.setMinimumWidth(160)
        self.employee_entries_title = QLabel("Выполненные работы сотрудника")
        self.employee_entries_title.setObjectName("SectionTitle")
        self.employee_entries = QTableWidget(0, 5)
        self.employee_entries.setHorizontalHeaderLabels(["Дата", "Объект", "Вид работ", "Описание", "Часы"])
        self.employee_entries.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.employee_entries.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.employee_entries.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.employee_entries.horizontalHeader().setStretchLastSection(True)
        self.employee_entries.setColumnWidth(0, 95)
        self.employee_entries.setColumnWidth(1, 160)
        self.employee_entries.setColumnWidth(2, 180)
        self.employee_entries.setColumnWidth(3, 360)
        self.employee_entries.setColumnWidth(4, 70)

        self.employee_context = QFrame()
        self.employee_context.setObjectName("EmployeeContext")
        employee_info = QHBoxLayout(self.employee_context)
        employee_info.setContentsMargins(14, 10, 14, 10)
        employee_info.setSpacing(28)
        for label in (self.employee_name, self.employee_position, self.employee_category):
            label.setObjectName("EmployeeInfo")
            label.setTextFormat(Qt.TextFormat.RichText)
            employee_info.addWidget(label)
        employee_info.addStretch()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        date_layout = QHBoxLayout()
        date_layout.setSpacing(14)
        date_layout.addWidget(self.date_edit)
        date_layout.addWidget(self.today_button)
        date_layout.addStretch()
        form.addRow("Дата", date_layout)
        form.addRow("Местонахождение", self.location)
        form.addRow("Объект", self.object)
        form.addRow("Вид работ", self.work_type)
        form.addRow("Описание работ", self.description)
        form.addRow("Часы", self.hours)
        form.addRow("Комментарий", self.comment)
        buttons = QHBoxLayout()
        buttons.setSpacing(18)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()
        form.addRow("", buttons)
        form_group = QGroupBox()
        form_group_layout = QVBoxLayout(form_group)
        form_group_layout.setContentsMargins(22, 20, 22, 20)
        form_group_layout.setSpacing(14)
        form_group_layout.addWidget(self.employee_context)
        form_group_layout.addLayout(form)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.addWidget(form_group)
        layout.addWidget(self.employee_entries_title)
        layout.addWidget(self.employee_entries, 1)

        self.set_employee(None)
        self.save_button.clicked.connect(self._emit_save)
        self.clear_button.clicked.connect(self.clear_form)
        self.today_button.clicked.connect(self.set_today)
        self.object.activated.connect(self._object_activated)

    def set_employee(self, employee: Employee | None) -> None:
        self.employee = employee
        if employee:
            self.employee_name.setText(self._employee_info_text("Сотрудник", employee.full_name))
            self.employee_position.setText(self._employee_info_text("Должность", employee.position or "-"))
            self.employee_category.setText(self._employee_info_text("Категория", employee.category or "-"))
        else:
            self.employee_name.setText(self._employee_info_text("Сотрудник", "не выбран"))
            self.employee_position.setText(self._employee_info_text("Должность", "-"))
            self.employee_category.setText(self._employee_info_text("Категория", "-"))

    def set_directories(self, locations, objects, work_types) -> None:
        self._fill(self.location, locations, include_empty=True)
        self._fill(self.object, objects, include_empty=True)
        self._fill(self.work_type, work_types, include_empty=True)

    def entry(self) -> WorkLogEntry:
        if self.employee is None or self.employee.id is None:
            raise ValueError("Выберите сотрудника")
        object_name = self.object.currentText().strip()
        object_index = self.object.findText(object_name)
        object_data = self.object.itemData(object_index) if object_index >= 0 else None
        object_id = object_data if isinstance(object_data, int) else None
        if object_data == self.ADD_OBJECT_ACTION:
            object_name = ""
        location_name = self.location.currentText().strip()
        work_type_name = self.work_type.currentText().strip()
        return WorkLogEntry(
            id=self.entry_id,
            employee_id=self.employee.id,
            work_date=self.entry_date(),
            location_id=self.location.currentData(),
            object_id=object_id,
            work_type_id=self.work_type.currentData(),
            description=self.description.toPlainText(),
            hours=int(self.hours.text() or 0),
            comment=self.comment.toPlainText(),
            location_name=location_name,
            object_name=object_name,
            work_type_name=work_type_name,
        )

    def selected_date(self) -> date:
        return self.entry_date()

    def entry_date(self) -> date:
        return self.date_edit.date().toPython()

    def set_today(self) -> None:
        self.date_edit.setDate(QDate.currentDate())

    def column_widths(self) -> list[int]:
        return []

    def apply_column_widths(self, widths: list[int]) -> None:
        return

    def load_entry(self, entry: WorkLogEntry) -> None:
        self.entry_id = entry.id
        self.date_edit.setDate(QDate(entry.work_date.year, entry.work_date.month, entry.work_date.day))
        self._select_or_add(self.location, entry.location_id, entry.location_name)
        self._select_or_add(self.object, entry.object_id, entry.object_name)
        self._select_or_add(self.work_type, entry.work_type_id, entry.work_type_name)
        self.description.setPlainText(entry.description)
        self.hours.setText(str(int(entry.hours)))
        self.comment.setPlainText(entry.comment)

    def clear_form(self) -> None:
        self.entry_id = None
        for combo in (self.location, self.object, self.work_type):
            combo.setCurrentIndex(0 if combo.count() else -1)
        self.description.clear()
        self.hours.setText("0")
        self.comment.clear()

    def set_employee_entries(self, entries: list[WorkLogEntry]) -> None:
        self.employee_entries.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                entry.work_date.strftime("%d.%m.%Y"),
                entry.object_name,
                entry.work_type_name,
                entry.description,
                str(int(entry.hours)),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.employee_entries.setItem(row, column, item)

    def select_object(self, object_id: int | None) -> None:
        self._select(self.object, object_id)

    def _fill(self, combo: QComboBox, items, include_empty: bool = False) -> None:
        combo.blockSignals(True)
        combo.clear()
        if include_empty:
            combo.addItem("", None)
        for item in items:
            combo.addItem(item.name, item.id)
        if combo is self.object:
            combo.insertSeparator(combo.count())
            combo.addItem("+ Добавить объект...", self.ADD_OBJECT_ACTION)
        combo.blockSignals(False)

    def _select(self, combo: QComboBox, value: int | None) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _select_or_add(self, combo: QComboBox, value: int | None, text: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
        if value is not None and text:
            combo.addItem(text, value)
            combo.setCurrentIndex(combo.count() - 1)
            return
        combo.setCurrentIndex(0)

    def _setup_date_edit(self, date_edit: QDateEdit) -> None:
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("dd.MM.yyyy")
        date_edit.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        date_edit.setFixedHeight(34)
        date_edit.lineEdit().setReadOnly(True)

    def _emit_save(self) -> None:
        try:
            self.save_requested.emit(self.entry())
        except ValueError as exc:
            self.validation_failed.emit(str(exc))

    def _object_activated(self, index: int) -> None:
        if self.object.itemData(index) == self.ADD_OBJECT_ACTION:
            self.object.setCurrentIndex(0)
            self.objects_directory_requested.emit()

    def _employee_info_text(self, label: str, value: str) -> str:
        return f"{escape(label)}: <u>{escape(value)}</u>"
