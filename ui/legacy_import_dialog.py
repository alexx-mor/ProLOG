"""Dialog for importing old secretary-maintained Excel reports."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QDoubleSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QDialog,
    QHeaderView,
)

from legacy_import.models import ImportRowStatus, IssueSeverity, LegacyImportPreview, ResolvedLegacyRow
from legacy_import.service import LegacyExcelImportService
from models import DirectoryItem, Employee

ROW_ROLE = Qt.ItemDataRole.UserRole


class LegacyImportDialog(QDialog):
    def __init__(self, import_service: LegacyExcelImportService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Импорт старых отчетов Excel")
        self.resize(1180, 680)
        self.import_service = import_service
        self.preview: LegacyImportPreview | None = None
        self._analysis_thread: QThread | None = None
        self._analysis_worker: LegacyAnalyzeWorker | None = None
        self._is_analyzing = False

        self.title = QLabel("Импорт старых отчетов Excel")
        self.title.setObjectName("DialogTitle")
        self.note = QLabel(
            "Выберите файл старой формы отчетов. Перед импортом ProLOG покажет найденные строки, "
            "пропуски, неизвестных сотрудников и новые объекты."
        )
        self.note.setWordWrap(True)
        self.note.setObjectName("WizardSubtitle")
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText("Файл Excel не выбран")
        self.browse_button = QPushButton("Выбрать файл")
        self.analyze_button = QPushButton("Проверить")
        self.edit_button = QPushButton("Исправить строку")
        self.import_button = QPushButton("Импортировать")
        self.close_button = QPushButton("Закрыть")
        self.edit_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.summary = QLabel("Готово к проверке")
        self.summary.setObjectName("WizardSubtitle")
        self.progress_label = QLabel("Проверяем файл, это может занять несколько секунд...")
        self.progress_label.setObjectName("WizardSubtitle")
        self.progress_label.setVisible(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        self.issues_table = QTableWidget(0, 7)
        self.rows_table = QTableWidget(0, 10)
        self.tabs = QTabWidget()
        self._build_layout()
        self._connect()
        self._configure_tables()

    def _build_layout(self) -> None:
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_path)
        file_row.addWidget(self.browse_button)
        file_row.addWidget(self.analyze_button)

        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress_bar)

        summary_frame = QFrame()
        summary_frame.setObjectName("EmployeeContext")
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.addWidget(self.summary)

        self.tabs.addTab(self.issues_table, "Замечания")
        self.tabs.addTab(self.rows_table, "Строки импорта")

        buttons = QHBoxLayout()
        buttons.addWidget(self.edit_button)
        buttons.addStretch()
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.note)
        layout.addLayout(file_row)
        layout.addLayout(progress_row)
        layout.addWidget(summary_frame)
        layout.addWidget(self.tabs)
        layout.addLayout(buttons)

    def _connect(self) -> None:
        self.browse_button.clicked.connect(self._select_file)
        self.analyze_button.clicked.connect(self._analyze)
        self.edit_button.clicked.connect(self._edit_selected_row)
        self.import_button.clicked.connect(self._commit)
        self.close_button.clicked.connect(self.reject)
        self.rows_table.doubleClicked.connect(self._edit_selected_row)
        self.issues_table.doubleClicked.connect(self._edit_issue_row)
        self.rows_table.itemSelectionChanged.connect(self._update_actions)

    def _configure_tables(self) -> None:
        self.issues_table.setHorizontalHeaderLabels(
            ["Тип", "Код", "Лист", "Строка", "Дата", "Сотрудник", "Сообщение"]
        )
        self.rows_table.setHorizontalHeaderLabels(
            [
                "Статус",
                "Лист",
                "Строка",
                "Дата",
                "Сотрудник Excel",
                "Сотрудник ProLOG",
                "Объект",
                "Местонахождение",
                "Часы",
                "Сообщение",
            ]
        )
        for table in (self.issues_table, self.rows_table):
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
        self._fit_columns()

    def _select_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Импорт старых отчетов", "", "Excel (*.xlsx)")
        if file_name:
            self.file_path.setText(file_name)
            self.edit_button.setEnabled(False)
            self.import_button.setEnabled(False)
            self.summary.setText("Файл выбран. Нажмите «Проверить».")

    def _analyze(self) -> None:
        if not self.file_path.text().strip():
            self._message("Выберите Excel-файл старого отчета", QMessageBox.Icon.Information)
            return
        self._set_analyzing(True)
        self.preview = None
        self.summary.setText("Идет проверка старого Excel-файла...")
        self.issues_table.setRowCount(0)
        self.rows_table.setRowCount(0)

        thread = QThread(self)
        worker = LegacyAnalyzeWorker(self.import_service, Path(self.file_path.text().strip()))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._analysis_finished)
        worker.failed.connect(self._analysis_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._analysis_thread_finished)
        self._analysis_thread = thread
        self._analysis_worker = worker
        thread.start()

    def _analysis_finished(self, preview: LegacyImportPreview) -> None:
        self.preview = preview
        self._set_analyzing(False)
        self._fill_preview(preview)
        if preview.has_blocking_errors:
            self.tabs.setCurrentWidget(self.issues_table)

    def _analysis_failed(self, message: str) -> None:
        self.preview = None
        self._set_analyzing(False)
        self.summary.setText("Проверка не выполнена")
        self._message(message, QMessageBox.Icon.Warning)

    def _analysis_thread_finished(self) -> None:
        self._analysis_thread = None
        self._analysis_worker = None

    def _commit(self) -> None:
        if self.preview is None:
            return
        if not self._ask(
            "Импортировать проверенные строки?",
            f"Будет импортировано записей: {self.preview.importable_count}. Продолжить?",
        ):
            return
        try:
            result = self.import_service.commit(self.preview)
        except Exception as exc:
            self._message(str(exc), QMessageBox.Icon.Warning)
            return
        self._message(
            (
                f"Импорт завершен.\n"
                f"Партия: №{result.batch_id}\n"
                f"Импортировано: {result.imported_count}\n"
                f"Пропущено: {result.skipped_count}"
            ),
            QMessageBox.Icon.Information,
        )
        self.accept()

    def _fill_preview(self, preview: LegacyImportPreview) -> None:
        self.summary.setText(
            " | ".join(
                [
                    f"Файл: {preview.source_file.name}",
                    f"Строк: {preview.total_rows}",
                    f"К импорту: {preview.importable_count}",
                    f"Пропущено: {preview.skipped_count}",
                    f"Ошибок: {preview.error_count}",
                    f"Предупреждений: {preview.warning_count}",
                ]
            )
        )
        self._fill_issues(preview)
        self._fill_rows(preview.rows)
        self._update_actions()

    def _fill_issues(self, preview: LegacyImportPreview) -> None:
        self.issues_table.setRowCount(len(preview.issues))
        for row_index, issue in enumerate(preview.issues):
            values = [
                issue.severity.value,
                issue.code,
                issue.sheet_name,
                "" if issue.row_number is None else issue.row_number,
                issue.work_date.strftime("%d.%m.%Y") if issue.work_date else "",
                issue.employee_text,
                issue.message,
            ]
            for column, value in enumerate(values):
                item = _table_item(value)
                item.setData(ROW_ROLE, self._row_index_for_issue(issue))
                if column == 0:
                    item.setIcon(_severity_icon(issue.severity))
                item.setBackground(_issue_background(issue.severity))
                self.issues_table.setItem(row_index, column, item)
        self._fit_columns()

    def _fill_rows(self, rows: list[ResolvedLegacyRow]) -> None:
        self.rows_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.status.value,
                row.source.sheet_name,
                row.source.row_number,
                row.source.work_date.strftime("%d.%m.%Y"),
                row.source.employee_text,
                row.employee.full_name if row.employee else "",
                row.object_name,
                row.current_location,
                row.hours,
                _row_message(row),
            ]
            background = _row_background(row)
            for column, value in enumerate(values):
                item = _table_item(value)
                item.setData(ROW_ROLE, row_index)
                if column == 0:
                    item.setIcon(_row_icon(row.status))
                item.setBackground(background)
                self.rows_table.setItem(row_index, column, item)
        self._fit_columns()

    def _fit_columns(self) -> None:
        issue_widths = [130, 190, 140, 70, 90, 160]
        for column, width in enumerate(issue_widths):
            self.issues_table.setColumnWidth(column, width)
        row_widths = [140, 120, 65, 90, 155, 210, 190, 180, 65]
        for column, width in enumerate(row_widths):
            self.rows_table.setColumnWidth(column, width)

    def _edit_selected_row(self) -> None:
        row = self._current_row()
        if self.preview is None or row is None:
            self._message("Выберите строку импорта", QMessageBox.Icon.Information)
            return
        dialog = LegacyRowResolutionDialog(
            row=row,
            employees=self.import_service.employees.list(),
            locations=self.import_service.directories.list_all("locations"),
            objects=self.import_service.directories.list_all("objects"),
            work_types=self.import_service.directories.list_all("work_types"),
            parent=self,
        )
        if not dialog.exec():
            return
        dialog.apply_to(row)
        self.import_service.refresh_preview(self.preview)
        self._fill_preview(self.preview)
        self.tabs.setCurrentWidget(self.rows_table)

    def _edit_issue_row(self) -> None:
        index = self._row_index_from_table(self.issues_table)
        if index is None or self.preview is None:
            return
        self.tabs.setCurrentWidget(self.rows_table)
        self.rows_table.selectRow(index)
        self._edit_selected_row()

    def _current_row(self) -> ResolvedLegacyRow | None:
        if self.preview is None:
            return None
        index = self._row_index_from_table(self.rows_table)
        if index is None:
            return None
        return self.preview.rows[index] if 0 <= index < len(self.preview.rows) else None

    def _row_index_from_table(self, table: QTableWidget) -> int | None:
        item = table.item(table.currentRow(), 0)
        if item is None:
            return None
        value = item.data(ROW_ROLE)
        return int(value) if value is not None and int(value) >= 0 else None

    def _row_index_for_issue(self, issue) -> int:
        if self.preview is None or issue.row_number is None:
            return -1
        for index, row in enumerate(self.preview.rows):
            if (
                row.source.sheet_name == issue.sheet_name
                and row.source.row_number == issue.row_number
                and row.source.work_date == issue.work_date
            ):
                return index
        return -1

    def _update_actions(self) -> None:
        has_preview = self.preview is not None
        self.edit_button.setEnabled(not self._is_analyzing and has_preview and self._current_row() is not None)
        self.import_button.setEnabled(
            bool(
                not self._is_analyzing
                and has_preview
                and self.preview
                and not self.preview.has_blocking_errors
                and self.preview.importable_count > 0
            )
        )

    def _set_analyzing(self, is_analyzing: bool) -> None:
        self._is_analyzing = is_analyzing
        self.progress_label.setVisible(is_analyzing)
        self.progress_bar.setVisible(is_analyzing)
        self.browse_button.setEnabled(not is_analyzing)
        self.analyze_button.setEnabled(not is_analyzing)
        self.edit_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.close_button.setEnabled(not is_analyzing)

    def reject(self) -> None:
        if self._is_analyzing:
            self._message("Дождитесь окончания проверки файла", QMessageBox.Icon.Information)
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._is_analyzing:
            self._message("Дождитесь окончания проверки файла", QMessageBox.Icon.Information)
            event.ignore()
            return
        super().closeEvent(event)


    def _ask(self, title: str, message: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
        box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        box.exec()
        return box.clickedButton() == yes

    def _message(self, message: str, icon: QMessageBox.Icon) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Импорт старых отчетов")
        box.setText(message)
        box.setIcon(icon)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()


class LegacyAnalyzeWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, import_service: LegacyExcelImportService, path: Path) -> None:
        super().__init__()
        self.import_service = import_service
        self.path = path

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self.import_service.analyze(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class LegacyRowResolutionDialog(QDialog):
    def __init__(
        self,
        row: ResolvedLegacyRow,
        employees: list[Employee],
        locations: list[DirectoryItem],
        objects: list[DirectoryItem],
        work_types: list[DirectoryItem],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Решение разночтения")
        self.resize(820, 620)
        self.row = row
        self.employees = sorted(employees, key=lambda item: item.full_name.casefold())

        self.title = QLabel("Исправление строки импорта")
        self.title.setObjectName("DialogTitle")
        self.source = QLabel(_source_text(row))
        self.source.setWordWrap(True)
        self.source.setObjectName("WizardSubtitle")
        self.suggestion = QLabel(_suggestion_text(row, self.employees))
        self.suggestion.setWordWrap(True)

        self.status = QComboBox()
        self.status.addItem("Импортировать", ImportRowStatus.READY.value)
        self.status.addItem("Пропустить", ImportRowStatus.SKIPPED.value)
        self.employee = QComboBox()
        self.location = QComboBox()
        self.object = QComboBox()
        self.work_type = QComboBox()
        self.hours = QDoubleSpinBox()
        self.hours.setDecimals(2)
        self.hours.setSingleStep(0.25)
        self.hours.setRange(0.0, 24.0)
        self.hours.setSuffix(" ч")
        self.description = QTextEdit()
        self.comment = QTextEdit()
        self.skip_reason = QLineEdit(row.skip_reason)
        self.suggest_employee_button = QPushButton("Выбрать предложенного сотрудника")
        self.save_button = QPushButton("Применить")
        self.cancel_button = QPushButton("Отмена")

        self._employee_candidate = _employee_candidate(row, self.employees)
        self._configure_fields(locations, objects, work_types)
        self._build_layout()
        self._connect()
        self._sync_status_fields()

    def apply_to(self, row: ResolvedLegacyRow) -> None:
        status = str(self.status.currentData() or ImportRowStatus.READY.value)
        row.status = ImportRowStatus.SKIPPED if status == ImportRowStatus.SKIPPED.value else ImportRowStatus.READY
        row.employee = self.employee.currentData()
        row.current_location = self.location.currentText().strip()
        row.object_name = self.object.currentText().strip()
        row.work_type = self.work_type.currentText().strip()
        row.hours = float(self.hours.value())
        row.description = self.description.toPlainText().strip()
        row.comment = self.comment.toPlainText().strip()
        row.skip_reason = self.skip_reason.text().strip()
        row.issues = []

    def _configure_fields(
        self,
        locations: list[DirectoryItem],
        objects: list[DirectoryItem],
        work_types: list[DirectoryItem],
    ) -> None:
        self.status.setCurrentIndex(1 if self.row.status == ImportRowStatus.SKIPPED else 0)
        self._fill_employee_combo()
        self._fill_directory_combo(self.location, locations, self.row.current_location, editable=False)
        self._fill_directory_combo(self.object, objects, self.row.object_name, editable=True)
        self._fill_directory_combo(self.work_type, work_types, self.row.work_type, editable=True)
        self.hours.setRange(0, 24)
        self.hours.setValue(max(0.0, min(24.0, float(self.row.hours))))
        self.description.setPlainText(self.row.description or self.row.source.description)
        self.description.setMinimumHeight(110)
        self.comment.setPlainText(self.row.comment)
        self.comment.setMinimumHeight(70)
        self.suggest_employee_button.setEnabled(self._employee_candidate is not None)

    def _fill_employee_combo(self) -> None:
        self.employee.setView(QListView())
        self.employee.addItem("", None)
        for employee in self.employees:
            self.employee.addItem(employee.full_name, employee)
        current_id = self.row.employee.id if self.row.employee else None
        if current_id is None and self._employee_candidate is not None:
            current_id = self._employee_candidate.id
        for index in range(self.employee.count()):
            employee = self.employee.itemData(index)
            if employee is not None and employee.id == current_id:
                self.employee.setCurrentIndex(index)
                return

    def _fill_directory_combo(
        self,
        combo: QComboBox,
        items: list[DirectoryItem],
        current: str,
        editable: bool,
    ) -> None:
        combo.setView(QListView())
        combo.setEditable(editable)
        combo.addItem("")
        values = [item.name for item in sorted(items, key=lambda item: item.name.casefold())]
        for value in values:
            combo.addItem(value)
        if current and current not in values:
            combo.addItem(current)
        if current:
            index = combo.findText(current)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def _build_layout(self) -> None:
        source_frame = QFrame()
        source_frame.setObjectName("EmployeeContext")
        source_layout = QVBoxLayout(source_frame)
        source_layout.setContentsMargins(12, 10, 12, 10)
        source_layout.addWidget(self.source)
        source_layout.addWidget(self.suggestion)
        source_layout.addWidget(self.suggest_employee_button, 0, Qt.AlignmentFlag.AlignLeft)

        form = QFormLayout()
        form.addRow("Действие", self.status)
        form.addRow("Сотрудник ProLOG", self.employee)
        form.addRow("Местонахождение", self.location)
        form.addRow("Объект", self.object)
        form.addRow("Вид работ", self.work_type)
        form.addRow("Часы", self.hours)
        form.addRow("Описание работ", self.description)
        form.addRow("Комментарий", self.comment)
        form.addRow("Причина пропуска", self.skip_reason)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(source_frame)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def _connect(self) -> None:
        self.status.currentIndexChanged.connect(self._sync_status_fields)
        self.suggest_employee_button.clicked.connect(self._select_suggested_employee)
        self.save_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

    def _sync_status_fields(self) -> None:
        is_skipped = self.status.currentData() == ImportRowStatus.SKIPPED.value
        for widget in (
            self.employee,
            self.location,
            self.object,
            self.work_type,
            self.hours,
            self.description,
            self.comment,
        ):
            widget.setEnabled(not is_skipped)
        self.skip_reason.setEnabled(is_skipped)
        if is_skipped and not self.skip_reason.text().strip():
            self.skip_reason.setText("Пропущено пользователем при сверке импорта")

    def _select_suggested_employee(self) -> None:
        if self._employee_candidate is None:
            return
        for index in range(self.employee.count()):
            employee = self.employee.itemData(index)
            if employee is not None and employee.id == self._employee_candidate.id:
                self.employee.setCurrentIndex(index)
                return


def _source_text(row: ResolvedLegacyRow) -> str:
    values = [
        f"Лист: {row.source.sheet_name}, строка: {row.source.row_number}, дата: {row.source.work_date:%d.%m.%Y}",
        f"Сотрудник в Excel: {row.source.employee_text or 'не указан'}",
        f"Должность в Excel: {row.source.position_text or 'не указана'}",
        f"Объект в Excel: {row.source.object_text or 'не указан'}",
        f"Нахождение в Excel: {row.source.legacy_location_text or 'не указано'}",
        f"Часы в Excel: {row.source.hours}",
        f"Описание в Excel: {row.source.description or 'не заполнено'}",
    ]
    return "\n".join(values)


def _suggestion_text(row: ResolvedLegacyRow, employees: list[Employee]) -> str:
    suggestions: list[str] = []
    employee = _employee_candidate(row, employees)
    if employee is not None and row.employee is None:
        suggestions.append(f"Предложение: сопоставить строку с сотрудником «{employee.full_name}».")
    if any(issue.code in {"EMPLOYEE_NOT_FOUND", "EMPLOYEE_NOT_SELECTED"} for issue in row.issues):
        suggestions.append("Решение: выберите сотрудника ProLOG вручную или пропустите строку.")
    if any(issue.code == "EMPTY_DESCRIPTION_WITH_HOURS" for issue in row.issues):
        suggestions.append("Решение: заполните описание работ либо переведите строку в пропущенные.")
    if any(issue.code in {"HOURS_TOO_HIGH", "ZERO_HOURS", "NEGATIVE_HOURS"} for issue in row.issues):
        suggestions.append("Решение: исправьте часы в диапазоне от 0 до 24.")
    if any(issue.code == "EMPTY_OBJECT" for issue in row.issues):
        suggestions.append("Решение: выберите объект, впишите новый объект или оставьте пустым осознанно.")
    if row.status == ImportRowStatus.SKIPPED:
        suggestions.append("Строка сейчас пропускается. Можно переключить действие на импорт.")
    if not suggestions:
        suggestions.append("Строку можно уточнить вручную перед импортом.")
    return "\n".join(suggestions)


def _employee_candidate(row: ResolvedLegacyRow, employees: list[Employee]) -> Employee | None:
    surname = _surname(row.source.employee_text)
    if not surname:
        return None
    matches = [employee for employee in employees if _surname(employee.full_name) == surname]
    return matches[0] if len(matches) == 1 else None


def _surname(value: str) -> str:
    text = value.replace(".", " ").replace(",", " ").strip().casefold()
    return text.split()[0] if text.split() else ""


def _table_item(value: object) -> QTableWidgetItem:
    text = str(value)
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _row_message(row: ResolvedLegacyRow) -> str:
    messages = [row.skip_reason, *(issue.message for issue in row.issues)]
    return "; ".join(dict.fromkeys(message for message in messages if message))


def _issue_background(severity: IssueSeverity) -> QColor:
    if severity == IssueSeverity.ERROR:
        return QColor("#fdecec")
    if severity == IssueSeverity.WARNING:
        return QColor("#fff7dd")
    return QColor("#edf6ff")


def _row_background(row: ResolvedLegacyRow) -> QColor:
    if row.status == ImportRowStatus.ERROR:
        return QColor("#fdecec")
    if row.status == ImportRowStatus.SKIPPED:
        return QColor("#f0f3f6")
    if any(issue.severity == IssueSeverity.WARNING for issue in row.issues):
        return QColor("#fff7dd")
    return QColor("#eefaf1")


def _severity_icon(severity: IssueSeverity) -> QIcon:
    if severity == IssueSeverity.ERROR:
        return _dot_icon("#c62828")
    if severity == IssueSeverity.WARNING:
        return _dot_icon("#f29900")
    return _dot_icon("#1976d2")


def _row_icon(status: ImportRowStatus) -> QIcon:
    if status == ImportRowStatus.ERROR:
        return _dot_icon("#c62828")
    if status == ImportRowStatus.SKIPPED:
        return _dot_icon("#8a939d")
    return _dot_icon("#16833a")


def _dot_icon(color: str) -> QIcon:
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, 12, 12)
    painter.end()
    return QIcon(pixmap)
