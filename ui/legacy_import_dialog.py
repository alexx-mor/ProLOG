"""Dialog for importing old secretary-maintained Excel reports."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QDialog,
    QHeaderView,
)

from legacy_import.models import ImportRowStatus, IssueSeverity, LegacyImportPreview, ResolvedLegacyRow
from legacy_import.service import LegacyExcelImportService


class LegacyImportDialog(QDialog):
    def __init__(self, import_service: LegacyExcelImportService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Импорт старых отчетов Excel")
        self.resize(1180, 680)
        self.import_service = import_service
        self.preview: LegacyImportPreview | None = None

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
        self.import_button = QPushButton("Импортировать")
        self.close_button = QPushButton("Закрыть")
        self.import_button.setEnabled(False)
        self.summary = QLabel("Готово к проверке")
        self.summary.setObjectName("WizardSubtitle")
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

        summary_frame = QFrame()
        summary_frame.setObjectName("EmployeeContext")
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.addWidget(self.summary)

        self.tabs.addTab(self.issues_table, "Замечания")
        self.tabs.addTab(self.rows_table, "Строки импорта")

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.note)
        layout.addLayout(file_row)
        layout.addWidget(summary_frame)
        layout.addWidget(self.tabs)
        layout.addLayout(buttons)

    def _connect(self) -> None:
        self.browse_button.clicked.connect(self._select_file)
        self.analyze_button.clicked.connect(self._analyze)
        self.import_button.clicked.connect(self._commit)
        self.close_button.clicked.connect(self.reject)

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
            self.import_button.setEnabled(False)
            self.summary.setText("Файл выбран. Нажмите «Проверить».")

    def _analyze(self) -> None:
        if not self.file_path.text().strip():
            self._message("Выберите Excel-файл старого отчета", QMessageBox.Icon.Information)
            return
        try:
            self.preview = self.import_service.analyze(Path(self.file_path.text().strip()))
        except Exception as exc:
            self.preview = None
            self.import_button.setEnabled(False)
            self._message(str(exc), QMessageBox.Icon.Warning)
            return
        self._fill_preview(self.preview)
        self.import_button.setEnabled(not self.preview.has_blocking_errors and self.preview.importable_count > 0)
        if self.preview.has_blocking_errors:
            self.tabs.setCurrentWidget(self.issues_table)

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
