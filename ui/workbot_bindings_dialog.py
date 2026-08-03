"""Editor for permanent MAX user to ProLOG employee bindings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
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
        self.resize(1120, 640)
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
        self.close_button = QPushButton("Закрыть")
        self._build_layout()
        self._fill_table()
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.reject)

    def _build_layout(self) -> None:
        title = QLabel("Привязка пользователей MAX к сотрудникам")
        title.setObjectName("DialogTitle")
        note = QLabel(
            "Проверьте профиль MAX и выберите соответствующего сотрудника ProLOG. Публичная "
            "ссылка на пользователя по числовому ID в MAX отсутствует; кнопка копирует точный ID. "
            "Зеленые строки уже привязаны, красные требуют проверки."
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
        for column, width in enumerate((120, 230, 330, 210, 145)):
            self.table.setColumnWidth(column, width)
        for row_index, link in enumerate(self.links):
            self.table.setItem(row_index, 0, QTableWidgetItem(str(link.max_user_id)))
            self.table.setItem(row_index, 1, QTableWidgetItem(link.profile_name))
            employee_combo = QComboBox()
            employee_combo.addItem("Не привязан", None)
            for employee in self.employees:
                employee_combo.addItem(employee.full_name, employee.id)
            employee_combo.setCurrentIndex(max(0, employee_combo.findData(link.employee_id)))
            self.table.setCellWidget(row_index, 2, employee_combo)
            state = QTableWidgetItem(link.match_message)
            state.setToolTip(link.match_message)
            self.table.setItem(row_index, 3, state)
            open_button = QPushButton("Копировать ID")
            open_button.setToolTip("Скопировать числовой MAX ID")
            open_button.clicked.connect(
                lambda _checked=False, user_id=link.max_user_id: self._copy_max_id(user_id)
            )
            self.table.setCellWidget(row_index, 4, open_button)
            employee_combo.currentIndexChanged.connect(
                lambda _index, row=row_index: self._employee_changed(row)
            )
            self._paint_row(row_index, employee_combo.currentData() is not None)

    def _copy_max_id(self, user_id: int) -> None:
        QApplication.clipboard().setText(str(user_id))

    def _employee_changed(self, row_index: int) -> None:
        employee_combo = self.table.cellWidget(row_index, 2)
        employee_id = employee_combo.currentData()
        employee = next((item for item in self.employees if item.id == employee_id), None)
        state = self.table.item(row_index, 3)
        if state:
            state.setText("Будет привязан" if employee else "Не привязан")
        self._paint_row(row_index, employee is not None)

    def _paint_row(self, row_index: int, is_bound: bool) -> None:
        color = QColor("#dff3e4" if is_bound else "#fde8e7")
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
