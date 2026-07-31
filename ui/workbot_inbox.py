"""Administrator review screen for reports received from WorkBot."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QObject, QRegularExpression, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hours import format_hours, parse_hours
from integrations.workbot.models import STATUS_LABELS, WorkBotInboxRow, WorkBotSyncResult
from integrations.workbot.service import WorkBotIntegrationService
from models import DirectoryItem, Employee

ROW_ID_ROLE = Qt.ItemDataRole.UserRole


class WorkBotInboxWidget(QWidget):
    source_path_changed = Signal(str)
    imported = Signal()
    status_message = Signal(str)

    def __init__(self, service: WorkBotIntegrationService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.rows: list[WorkBotInboxRow] = []
        self.employees: list[Employee] = []
        self.locations: list[DirectoryItem] = []
        self.objects: list[DirectoryItem] = []
        self.work_types: list[DirectoryItem] = []
        self._sync_thread: QThread | None = None
        self._sync_worker: WorkBotSyncWorker | None = None
        self.reviewer = ""

        self.source_path = QLineEdit()
        self.source_path.setReadOnly(True)
        self.source_path.setPlaceholderText("Выберите файл workbot.sqlite3")
        self.choose_source = QPushButton("Выбрать базу")
        self.sync_button = QPushButton("Проверить новые")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        self.filter = QComboBox()
        self.filter.addItem("Все статусы", "")
        for status, label in STATUS_LABELS.items():
            self.filter.addItem(label, status)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Статус", "Дата", "Сотрудник", "Объект", "Местонахождение", "Часы", "Источник", "Версия"]
        )

        self.employee = QComboBox()
        self.work_date = QDateEdit()
        self.work_date.setCalendarPopup(True)
        self.work_date.setDisplayFormat("dd.MM.yyyy")
        self.location = QComboBox()
        self.object = QComboBox()
        self.work_type = QComboBox()
        self.hours = QLineEdit()
        self.hours.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"^(?:|(?:[0-9]|1[0-9]|2[0-3])(?:[.,][0-9]{0,2})?|24(?:[.,]0{0,2})?)$")
            )
        )
        self.description = QTextEdit()
        self.description.setMaximumHeight(100)
        self.issue = QLabel()
        self.issue.setWordWrap(True)
        self.issue.setStyleSheet("color: #a63b2b;")
        self.source_text = QTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setMaximumHeight(120)
        self.remember = QCheckBox("Запомнить подтвержденные соответствия")
        self.remember.setChecked(True)
        self.import_button = QPushButton("Подтвердить и импортировать")
        self.reject_button = QPushButton("Отклонить")
        self.import_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self._build_layout()
        self._configure_table()
        self._connect()

    def _build_layout(self) -> None:
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("База WorkBot"))
        source_row.addWidget(self.source_path, 1)
        source_row.addWidget(self.choose_source)
        source_row.addWidget(self.sync_button)
        source_row.addWidget(self.progress)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Показать"))
        filter_row.addWidget(self.filter)
        filter_row.addStretch()

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(filter_row)
        left_layout.addWidget(self.table)

        editor = QWidget()
        form = QFormLayout(editor)
        form.setContentsMargins(12, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Сотрудник", self.employee)
        form.addRow("Дата", self.work_date)
        form.addRow("Местонахождение", self.location)
        form.addRow("Объект", self.object)
        form.addRow("Вид работ", self.work_type)
        form.addRow("Часы", self.hours)
        form.addRow("Описание работ", self.description)
        form.addRow("Проверка", self.issue)
        form.addRow("Исходное сообщение", self.source_text)
        form.addRow("", self.remember)
        buttons = QHBoxLayout()
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.reject_button)
        buttons.addStretch()
        form.addRow("", buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(editor)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([760, 500])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        title = QLabel("Входящие отчеты WorkBot")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        layout.addLayout(source_row)
        layout.addWidget(splitter)

    def _configure_table(self) -> None:
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for column, width in enumerate((175, 90, 190, 160, 175, 70, 90, 65)):
            self.table.setColumnWidth(column, width)

    def _connect(self) -> None:
        self.choose_source.clicked.connect(self._choose_source)
        self.sync_button.clicked.connect(self._start_sync)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._load_selected)
        self.import_button.clicked.connect(self._import_selected)
        self.reject_button.clicked.connect(self._reject_selected)

    def set_source_path(self, value: str) -> None:
        self.source_path.setText(value)

    def set_reviewer(self, value: str) -> None:
        self.reviewer = value.strip()

    def set_reference_data(
        self,
        employees: list[Employee],
        locations: list[DirectoryItem],
        objects: list[DirectoryItem],
        work_types: list[DirectoryItem],
    ) -> None:
        self.employees = employees
        self.locations = locations
        self.objects = objects
        self.work_types = work_types
        self._fill_combo(self.employee, employees, "full_name")
        self._fill_combo(self.location, locations)
        self._fill_combo(self.object, objects)
        self._fill_combo(self.work_type, work_types)

    def refresh(self) -> None:
        selected_id = self._selected_row_id()
        self.rows = self.service.list_rows(str(self.filter.currentData() or ""))
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            values = [
                STATUS_LABELS.get(row.status, row.status),
                row.work_date.strftime("%d.%m.%Y"),
                row.employee_text,
                row.object_text,
                row.location_text,
                format_hours(row.hours),
                {"strict": "MAX", "historical": "История", "unparsed": "Ошибка"}.get(
                    row.source_kind, row.source_kind
                ),
                row.revision,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(ROW_ID_ROLE, row.id)
                item.setToolTip(str(value))
                if row.status == "imported":
                    item.setForeground(QColor("#26834a"))
                elif row.status in {
                    "needs_employee",
                    "needs_location",
                    "needs_object",
                    "invalid_hours",
                    "source_error",
                    "changed_after_import",
                }:
                    item.setForeground(QColor("#b54532"))
                self.table.setItem(row_index, column, item)
            if row.id == selected_id:
                self.table.selectRow(row_index)
        if self.table.currentRow() < 0 and self.rows:
            self.table.selectRow(0)

    def _choose_source(self) -> None:
        current = str(Path(self.source_path.text()).parent) if self.source_path.text() else ""
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите базу WorkBot",
            current,
            "База SQLite (*.sqlite3 *.db);;Все файлы (*)",
        )
        if file_name:
            self.source_path.setText(file_name)
            self.source_path_changed.emit(file_name)

    def _start_sync(self) -> None:
        value = self.source_path.text().strip()
        if not value:
            self._message("Сначала выберите базу WorkBot", QMessageBox.Icon.Information)
            return
        self._set_syncing(True)
        thread = QThread(self)
        worker = WorkBotSyncWorker(self.service, Path(value))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._sync_finished)
        worker.failed.connect(self._sync_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._sync_thread_finished)
        self._sync_thread = thread
        self._sync_worker = worker
        thread.start()

    @Slot(object)
    def _sync_finished(self, result: WorkBotSyncResult) -> None:
        self._set_syncing(False)
        self.refresh()
        self.status_message.emit(
            f"WorkBot: добавлено {result.added_rows}, без изменений {result.unchanged_messages}, "
            f"новых версий {result.revised_messages}"
        )

    @Slot(str)
    def _sync_failed(self, message: str) -> None:
        self._set_syncing(False)
        self._message(message, QMessageBox.Icon.Warning)
        self.status_message.emit(f"Ошибка WorkBot: {message}")

    def _sync_thread_finished(self) -> None:
        self._sync_thread = None
        self._sync_worker = None

    def _set_syncing(self, active: bool) -> None:
        self.progress.setVisible(active)
        self.sync_button.setEnabled(not active)
        self.choose_source.setEnabled(not active)

    def _load_selected(self) -> None:
        row = self._selected_row()
        enabled = row is not None and row.status != "imported"
        self.import_button.setEnabled(enabled)
        self.reject_button.setEnabled(enabled)
        if row is None:
            return
        self._select_combo(self.employee, row.employee_id)
        self._select_combo(self.location, row.location_id)
        self._select_combo(self.object, row.object_id)
        self._select_combo(self.work_type, row.work_type_id)
        self.work_date.setDate(QDate(row.work_date.year, row.work_date.month, row.work_date.day))
        self.hours.setText(format_hours(row.hours))
        self.description.setPlainText(row.work_types)
        self.issue.setText(row.error_message or "Соответствия найдены автоматически")
        self.source_text.setPlainText(row.source_fragment or row.raw_text)

    def _import_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        try:
            employee_id = self.employee.currentData()
            if employee_id is None:
                raise ValueError("Выберите сотрудника")
            qdate = self.work_date.date()
            self.service.import_row(
                row.id,
                employee_id=int(employee_id),
                work_date=date(qdate.year(), qdate.month(), qdate.day()),
                location_id=self.location.currentData(),
                object_id=self.object.currentData(),
                work_type_id=self.work_type.currentData(),
                description=self.description.toPlainText(),
                hours=parse_hours(self.hours.text()),
                remember=self.remember.isChecked(),
                reviewer=self.reviewer,
            )
        except Exception as exc:
            self._message(str(exc), QMessageBox.Icon.Warning)
            return
        self.refresh()
        self.imported.emit()
        self.status_message.emit("Отчет WorkBot добавлен в журнал работ")

    def _reject_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self.service.reject(row.id, reviewer=self.reviewer)
        self.refresh()
        self.status_message.emit("Входящий отчет отклонен")

    def _selected_row(self) -> WorkBotInboxRow | None:
        row_id = self._selected_row_id()
        return next((row for row in self.rows if row.id == row_id), None)

    def _selected_row_id(self) -> int | None:
        row_index = self.table.currentRow()
        item = self.table.item(row_index, 0) if row_index >= 0 else None
        return int(item.data(ROW_ID_ROLE)) if item and item.data(ROW_ID_ROLE) is not None else None

    def _fill_combo(self, combo: QComboBox, items, text_attr: str = "name") -> None:
        current = combo.currentData()
        combo.clear()
        combo.addItem("", None)
        for item in items:
            combo.addItem(str(getattr(item, text_attr)), item.id)
        self._select_combo(combo, current)

    def _select_combo(self, combo: QComboBox, item_id: int | None) -> None:
        index = combo.findData(item_id)
        combo.setCurrentIndex(max(0, index))

    def _message(self, text: str, icon: QMessageBox.Icon) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("WorkBot")
        box.setText(text)
        box.setIcon(icon)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class WorkBotSyncWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, service: WorkBotIntegrationService, path: Path) -> None:
        super().__init__()
        self.service = service
        self.path = path

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self.service.sync(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))
