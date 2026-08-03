"""Editor for permanent MAX user to ProLOG employee bindings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
        self.resize(1320, 640)
        self.service = service
        self.employees = sorted(employees, key=lambda item: item.full_name.casefold())
        self.links = service.list_user_links(source_path)
        self.table = QTableWidget(len(self.links), 7)
        self.table.setHorizontalHeaderLabels(
            [
                "MAX ID",
                "Профиль MAX",
                "ФИО в WorkBot",
                "Подтвержденный телефон MAX",
                "Сотрудник ProLOG",
                "Телефон сотрудника",
                "Состояние",
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
            "Для надежной привязки сотрудник должен написать WorkBot команду /register и нажать "
            "кнопку передачи контакта. ProLOG автоматически сопоставляет только подтвержденный MAX "
            "номер, который встречается ровно в одной карточке сотрудника."
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
        for column, width in enumerate((105, 175, 180, 205, 245, 165, 220)):
            self.table.setColumnWidth(column, width)
        for row_index, link in enumerate(self.links):
            self.table.setItem(row_index, 0, QTableWidgetItem(str(link.max_user_id)))
            self.table.setItem(row_index, 1, QTableWidgetItem(link.profile_name))
            self.table.setItem(row_index, 2, QTableWidgetItem(link.employee_text))
            verified_phone = QTableWidgetItem(link.verified_phone or "Не подтвержден")
            verified_phone.setToolTip(
                "Номер подтвержден подписью MAX" if link.verified_phone else "Пользователь еще не выполнил /register"
            )
            self.table.setItem(row_index, 3, verified_phone)
            employee_combo = QComboBox()
            employee_combo.addItem("Не привязан", None)
            for employee in self.employees:
                employee_combo.addItem(employee.full_name, employee.id)
            employee_combo.setCurrentIndex(max(0, employee_combo.findData(link.employee_id)))
            phone = QLineEdit(link.mobile_phone)
            phone.setPlaceholderText("+7 999 123-45-67")
            self.table.setCellWidget(row_index, 4, employee_combo)
            self.table.setCellWidget(row_index, 5, phone)
            state = QTableWidgetItem(link.match_message)
            state.setToolTip(link.match_message)
            self.table.setItem(row_index, 6, state)
            employee_combo.currentIndexChanged.connect(
                lambda _index, row=row_index: self._employee_changed(row)
            )

    def _employee_changed(self, row_index: int) -> None:
        employee_combo = self.table.cellWidget(row_index, 4)
        phone = self.table.cellWidget(row_index, 5)
        employee_id = employee_combo.currentData()
        employee = next((item for item in self.employees if item.id == employee_id), None)
        employee_phone = employee.mobile_phone if employee else ""
        if employee and not employee_phone and self.links[row_index].verified_phone:
            employee_phone = self.links[row_index].verified_phone
        phone.setText(employee_phone)
        state = self.table.item(row_index, 6)
        if state:
            state.setText("Будет привязан" if employee else "Не привязан")

    def _save(self) -> None:
        updated: list[WorkBotUserLink] = []
        for row_index, link in enumerate(self.links):
            employee_combo = self.table.cellWidget(row_index, 4)
            phone = self.table.cellWidget(row_index, 5)
            updated.append(
                WorkBotUserLink(
                    max_user_id=link.max_user_id,
                    profile_name=link.profile_name,
                    employee_text=link.employee_text,
                    employee_id=employee_combo.currentData(),
                    mobile_phone=phone.text(),
                    verified_phone=link.verified_phone,
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
