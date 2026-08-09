"""Dedicated UI for the ProductionStage directory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from production.models import ProductionStage

if TYPE_CHECKING:
    from production.service import ProductionStageService


class ProductionStageEditDialog(QDialog):
    """Create a stage or rename it without exposing UID changes."""

    def __init__(self, stage: ProductionStage | None = None, parent=None) -> None:
        super().__init__(parent)
        self.stage = stage
        self.setWindowTitle("Добавить этап" if stage is None else "Редактировать этап")
        self.setFixedWidth(560)
        self.name = QLineEdit(stage.name if stage else "")
        self.name.setPlaceholderText("Например: Контроль монтажа")
        self.code = QLineEdit(stage.code if stage else "")
        self.code.setPlaceholderText("Например: INSTALLATION_CONTROL")
        self.code.setReadOnly(stage is not None)
        code_hint = QLabel(
            "Стабильный технический код латиницей. После создания он не изменяется."
        )
        code_hint.setObjectName("WizardSubtitle")
        code_hint.setWordWrap(True)
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Название", self.name)
        form.addRow("Машинный код", self.code)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(code_hint)
        layout.addLayout(buttons)

    def values(self) -> tuple[str, str]:
        return self.code.text().strip(), self.name.text().strip()

    def accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.information(self, "Этапы производства", "Укажите название этапа")
            return
        if self.stage is None and not self.code.text().strip():
            QMessageBox.information(self, "Этапы производства", "Укажите машинный код")
            return
        super().accept()


class ProductionStageWidget(QWidget):
    """Manage production stages through ProductionStageService only."""

    def __init__(self, service: ProductionStageService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._items: list[ProductionStage] = []
        self._search = ""
        self._show_inactive = True
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Машинный код", "Порядок", "Статус"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        for column in range(4):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Interactive,
            )
        self.table.setColumnWidth(0, 390)
        self.table.setColumnWidth(1, 260)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 130)
        self.table.horizontalHeader().setStretchLastSection(False)

        self.add_button = QPushButton("Добавить")
        self.edit_button = QPushButton("Редактировать")
        self.disable_button = QPushButton("Отключить")
        self.restore_button = QPushButton("Активировать")
        self.move_up_button = QPushButton("Вверх")
        self.move_down_button = QPushButton("Вниз")

        buttons = QHBoxLayout()
        for button in (
            self.add_button,
            self.edit_button,
            self.disable_button,
            self.restore_button,
            self.move_up_button,
            self.move_down_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addLayout(buttons)

        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.disable_button.clicked.connect(lambda: self._set_active(False))
        self.restore_button.clicked.connect(lambda: self._set_active(True))
        self.move_up_button.clicked.connect(lambda: self._move(-1))
        self.move_down_button.clicked.connect(lambda: self._move(1))
        self.table.doubleClicked.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self._update_buttons()

    def refresh(self, search: str = "", show_inactive: bool = True) -> None:
        selected_id = self.selected_id()
        self._search = search.strip().casefold()
        self._show_inactive = show_inactive
        stages = self.service.list_all() if show_inactive else self.service.list_active()
        self._items = [
            stage
            for stage in stages
            if not self._search
            or self._search in stage.name.casefold()
            or self._search in stage.code.casefold()
        ]
        self.table.setRowCount(len(self._items))
        for row, stage in enumerate(self._items):
            name = QTableWidgetItem(stage.name)
            name.setData(Qt.ItemDataRole.UserRole, stage.id)
            name.setToolTip(stage.name)
            code = QTableWidgetItem(stage.code)
            code.setToolTip(stage.code)
            order = QTableWidgetItem(str(stage.sort_order))
            order.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status = _status_item(stage.is_active)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, code)
            self.table.setItem(row, 2, order)
            self.table.setItem(row, 3, status)
            if not stage.is_active:
                self._mark_disabled_row(row)
        if selected_id is not None:
            self.select_stage(selected_id)
        self._update_buttons()

    def column_widths(self) -> list[int]:
        return [self.table.columnWidth(column) for column in range(self.table.columnCount())]

    def apply_column_widths(self, widths: list[int]) -> None:
        if len(widths) != self.table.columnCount():
            return
        for column, width in enumerate(widths):
            if width > 0:
                self.table.setColumnWidth(column, width)

    def selected_id(self) -> int | None:
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        value = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        return int(value) if value is not None else None

    def selected_stage(self) -> ProductionStage | None:
        stage_id = self.selected_id()
        return next((stage for stage in self._items if stage.id == stage_id), None)

    def select_stage(self, stage_id: int) -> None:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell and cell.data(Qt.ItemDataRole.UserRole) == stage_id:
                self.table.selectRow(row)
                self.table.scrollToItem(cell)
                return

    def _add(self) -> None:
        dialog = ProductionStageEditDialog(parent=self)
        if not dialog.exec():
            return
        code, name = dialog.values()
        try:
            stage = self.service.create(code, name)
        except ValueError as error:
            self._show_error(str(error))
            return
        self.refresh(self._search, self._show_inactive)
        if stage.id is not None:
            self.select_stage(stage.id)

    def _edit(self) -> None:
        stage = self.selected_stage()
        if stage is None or stage.id is None:
            self._show_error("Выберите производственный этап")
            return
        dialog = ProductionStageEditDialog(stage, self)
        if not dialog.exec():
            return
        _code, name = dialog.values()
        try:
            self.service.rename(stage.id, name)
        except ValueError as error:
            self._show_error(str(error))
            return
        self.refresh(self._search, self._show_inactive)
        self.select_stage(stage.id)

    def _set_active(self, is_active: bool) -> None:
        stage = self.selected_stage()
        if stage is None or stage.id is None:
            self._show_error("Выберите производственный этап")
            return
        try:
            if is_active:
                self.service.restore(stage.id)
            else:
                self.service.deactivate(stage.id)
        except ValueError as error:
            self._show_error(str(error))
            return
        self.refresh(self._search, self._show_inactive)
        self.select_stage(stage.id)

    def _move(self, direction: int) -> None:
        stage_id = self.selected_id()
        if stage_id is None:
            self._show_error("Выберите производственный этап")
            return
        try:
            self.service.move(
                stage_id,
                direction,
                active_only=not self._show_inactive,
            )
        except ValueError as error:
            self._show_error(str(error))
            return
        self.refresh(self._search, self._show_inactive)
        self.select_stage(stage_id)

    def _update_buttons(self) -> None:
        stage = self.selected_stage()
        has_stage = stage is not None
        self.edit_button.setEnabled(has_stage)
        self.disable_button.setEnabled(has_stage and bool(stage and stage.is_active))
        self.restore_button.setEnabled(has_stage and bool(stage and not stage.is_active))
        self.move_up_button.setEnabled(has_stage)
        self.move_down_button.setEnabled(has_stage)

    def _show_context_menu(self, position) -> None:
        menu = QMenu(self)
        add_action = QAction("Добавить", self)
        edit_action = QAction("Редактировать", self)
        disable_action = QAction("Отключить", self)
        restore_action = QAction("Активировать", self)
        up_action = QAction("Переместить вверх", self)
        down_action = QAction("Переместить вниз", self)
        stage = self.selected_stage()
        for action in (edit_action, disable_action, restore_action, up_action, down_action):
            action.setEnabled(stage is not None)
        disable_action.setEnabled(bool(stage and stage.is_active))
        restore_action.setEnabled(bool(stage and not stage.is_active))
        add_action.triggered.connect(self._add)
        edit_action.triggered.connect(self._edit)
        disable_action.triggered.connect(lambda: self._set_active(False))
        restore_action.triggered.connect(lambda: self._set_active(True))
        up_action.triggered.connect(lambda: self._move(-1))
        down_action.triggered.connect(lambda: self._move(1))
        menu.addAction(add_action)
        menu.addAction(edit_action)
        menu.addSeparator()
        menu.addAction(up_action)
        menu.addAction(down_action)
        menu.addSeparator()
        menu.addAction(disable_action)
        menu.addAction(restore_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _mark_disabled_row(self, row: int) -> None:
        background = QColor("#edf0f3")
        foreground = QColor("#6f7882")
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item:
                item.setBackground(background)
                item.setForeground(foreground)

    def _show_error(self, message: str) -> None:
        QMessageBox.information(self, "Этапы производства", message)


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
