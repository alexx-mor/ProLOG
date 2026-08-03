"""Editor for permanent MAX user to ProLOG employee bindings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from integrations.workbot.models import WorkBotUserLink
from integrations.workbot.service import WorkBotIntegrationService
from models import Employee


class NoWheelComboBox(QComboBox):
    """Prevents accidental selection changes while the table is scrolled."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class WorkBotBindingsDialog(QDialog):
    def __init__(
        self,
        service: WorkBotIntegrationService,
        source_path: Path,
        employees: list[Employee],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Привязки пользователей WorkBot")
        self.resize(1180, 640)
        self.service = service
        self.employees = sorted(employees, key=lambda item: item.full_name.casefold())
        self.links = service.list_user_links(source_path)
        self.table = QTableWidget(len(self.links), 5)
        self.table.setHorizontalHeaderLabels(
            [
                "MAX ID",
                "Профиль MAX",
                "Сотрудник ProLOG",
                "Состояние",
                "Действие",
            ]
        )
        self.save_button = QPushButton("Сохранить привязки")
        self.close_button = QPushButton("Отмена")
        self._build_layout()
        self._fill_table()
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.reject)

    def _build_layout(self) -> None:
        title = QLabel("Привязка пользователей MAX к сотрудникам")
        title.setObjectName("DialogTitle")
        note = QLabel(
            "Проверьте профиль MAX и выберите соответствующего сотрудника ProLOG. Кнопка открытия "
            "использует нативный протокол приложения MAX. Зеленые строки уже привязаны, "
            "светлые строки требуют проверки, а измененные выделяются отдельно."
        )
        note.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(note)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

    def _fill_table(self) -> None:
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((120, 230, 330, 220, 210)):
            self.table.setColumnWidth(column, width)
        for row_index, link in enumerate(self.links):
            self.table.setItem(row_index, 0, QTableWidgetItem(str(link.max_user_id)))
            self.table.setItem(row_index, 1, QTableWidgetItem(link.profile_name))
            employee_combo = NoWheelComboBox()
            employee_combo.addItem("Не привязан", None)
            for employee in self.employees:
                employee_combo.addItem(employee.full_name, employee.id)
            employee_combo.setCurrentIndex(max(0, employee_combo.findData(link.employee_id)))
            self.table.setCellWidget(row_index, 2, employee_combo)
            state = QTableWidgetItem(link.match_message)
            state.setToolTip(link.match_message)
            self.table.setItem(row_index, 3, state)
            open_button = QPushButton("Открыть диалог в MAX")
            open_button.setToolTip("Открыть пользователя в установленном приложении MAX")
            open_button.clicked.connect(
                lambda _checked=False, user_id=link.max_user_id: self._open_max_dialog(user_id)
            )
            self.table.setCellWidget(row_index, 4, open_button)
            employee_combo.currentIndexChanged.connect(
                lambda _index, row=row_index: self._employee_changed(row)
            )
            self._paint_row(row_index, employee_combo.currentData() is not None, False)

    def _open_max_dialog(self, user_id: int) -> None:
        QApplication.clipboard().setText(str(user_id))
        if QDesktopServices.openUrl(QUrl(f"max://user/{user_id}")):
            return
        self._message(
            "Не удалось открыть приложение MAX. Числовой MAX ID скопирован в буфер обмена.",
            QMessageBox.Icon.Warning,
        )

    def _employee_changed(self, row_index: int) -> None:
        employee_combo = self.table.cellWidget(row_index, 2)
        employee_id = employee_combo.currentData()
        employee = next((item for item in self.employees if item.id == employee_id), None)
        changed = employee_id != self.links[row_index].employee_id
        state = self.table.item(row_index, 3)
        if state:
            if changed:
                state.setText(
                    "Изменено: будет привязан" if employee else "Изменено: привязка будет удалена"
                )
            else:
                state.setText("Привязка сохранена" if employee else "Не привязан")
        self._paint_row(row_index, employee is not None, changed)

    def _paint_row(self, row_index: int, is_bound: bool, is_changed: bool) -> None:
        color = QColor("#fff5d9" if is_changed else ("#edf7f0" if is_bound else "#fff6f4"))
        for column in range(self.table.columnCount()):
            item = self.table.item(row_index, column)
            if item is not None:
                item.setBackground(color)
        employee_combo = self.table.cellWidget(row_index, 2)
        if employee_combo is not None:
            employee_combo.setStyleSheet(
                "QComboBox { background-color: %s; }" % color.name()
            )

    def _save(self) -> None:
        updated: list[WorkBotUserLink] = []
        for row_index, link in enumerate(self.links):
            employee_combo = self.table.cellWidget(row_index, 2)
            updated.append(
                WorkBotUserLink(
                    max_user_id=link.max_user_id,
                    profile_name=link.profile_name,
                    employee_id=employee_combo.currentData(),
                )
            )
        try:
            self.service.save_user_links(updated)
        except Exception as exc:
            self._message(str(exc), QMessageBox.Icon.Warning)
            return
        self.links = updated
        self.accept()

    def _message(self, text: str, icon: QMessageBox.Icon) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Привязки WorkBot")
        box.setText(text)
        box.setIcon(icon)
        box.addButton("ОК", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
