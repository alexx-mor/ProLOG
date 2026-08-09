"""Main-window overview of current product production state."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from production.projection_models import ReadinessSource
from ui.production_controller import (
    ProductProductionListItem,
    ProductionUiController,
    production_error_message,
)


logger = logging.getLogger(__name__)


class FilterComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ProductionOverviewWidget(QWidget):
    """Searchable Product + ProductionProjectionService read view."""

    card_requested = Signal(int)

    def __init__(
        self,
        controller: ProductionUiController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._rows: list[ProductProductionListItem] = []
        self._visible_rows: list[ProductProductionListItem] = []
        self.title = QLabel("Производство")
        self.title.setObjectName("SectionTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти изделие, объект, заводской номер или шифр")
        self.object_filter = FilterComboBox()
        self.object_filter.setMinimumWidth(220)
        self.stage_filter = FilterComboBox()
        self.stage_filter.setMinimumWidth(210)
        self.refresh_button = QPushButton("Обновить")
        self.open_button = QPushButton("Открыть карточку производства")
        self.open_button.setMinimumWidth(230)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Объект",
                "Изделие",
                "Заводской номер",
                "Код / шифр",
                "Текущий этап",
                "Готовность",
                "Источник",
                "Последнее наблюдение",
                "Фотографии",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for column, width in enumerate((230, 220, 135, 140, 180, 100, 135, 170, 95)):
            self.table.setColumnWidth(column, width)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(QLabel("Объект"))
        filters.addWidget(self.object_filter)
        filters.addWidget(QLabel("Этап"))
        filters.addWidget(self.stage_filter)
        filters.addWidget(self.refresh_button)
        actions = QHBoxLayout()
        actions.addWidget(self.open_button)
        actions.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(self.title)
        layout.addLayout(filters)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)

        self.search.textChanged.connect(self._apply_filters)
        self.object_filter.currentIndexChanged.connect(self._apply_filters)
        self.stage_filter.currentIndexChanged.connect(self._apply_filters)
        self.refresh_button.clicked.connect(self.refresh)
        self.open_button.clicked.connect(self._emit_selected)
        self.table.doubleClicked.connect(self._emit_selected)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self._update_actions()

    def refresh(self) -> None:
        selected_id = self.selected_product_id()
        try:
            self._rows = self.controller.production_list()
            self._populate_filters()
            self._apply_filters()
            if selected_id is not None:
                self.select_product(selected_id)
        except Exception as exc:
            logger.exception("Не удалось обновить рабочий список производства")
            QMessageBox.warning(self, "Производство", production_error_message(exc))

    def selected_product_id(self) -> int | None:
        row = self.table.currentRow()
        if not 0 <= row < len(self._visible_rows):
            return None
        return self._visible_rows[row].product.id

    def select_product(self, product_id: int) -> None:
        for row, item in enumerate(self._visible_rows):
            if item.product.id == product_id:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return

    def _populate_filters(self) -> None:
        object_id = self.object_filter.currentData()
        stage_id = self.stage_filter.currentData()
        self.object_filter.blockSignals(True)
        self.stage_filter.blockSignals(True)
        self.object_filter.clear()
        self.stage_filter.clear()
        self.object_filter.addItem("Все объекты", None)
        self.stage_filter.addItem("Все этапы", None)
        objects = sorted(
            {
                (item.product.object_id, item.product.object_name or "Объект не указан")
                for item in self._rows
            },
            key=lambda value: value[1].casefold(),
        )
        stages = sorted(
            {
                (item.state.current_stage_id, item.state.current_stage_name)
                for item in self._rows
                if item.state.current_stage_id is not None
                and item.state.current_stage_name is not None
            },
            key=lambda value: value[1].casefold(),
        )
        for item_id, name in objects:
            self.object_filter.addItem(name, item_id)
        for item_id, name in stages:
            self.stage_filter.addItem(name, item_id)
        self._restore_filter(self.object_filter, object_id)
        self._restore_filter(self.stage_filter, stage_id)
        self.object_filter.blockSignals(False)
        self.stage_filter.blockSignals(False)

    @staticmethod
    def _restore_filter(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _apply_filters(self) -> None:
        needle = self.search.text().strip().casefold()
        object_id = self.object_filter.currentData()
        stage_id = self.stage_filter.currentData()
        self._visible_rows = [
            item
            for item in self._rows
            if (object_id is None or item.product.object_id == object_id)
            and (stage_id is None or item.state.current_stage_id == stage_id)
            and (
                not needle
                or any(
                    needle in value.casefold()
                    for value in (
                        item.product.object_name,
                        item.product.name,
                        item.product.serial_number,
                        item.product.code,
                        item.state.current_stage_name or "",
                    )
                )
            )
        ]
        self.table.setRowCount(len(self._visible_rows))
        for row, item in enumerate(self._visible_rows):
            self._populate_row(row, item)
        if self._visible_rows:
            self.table.selectRow(0)
        self._update_actions()

    def _populate_row(self, row: int, item: ProductProductionListItem) -> None:
        product = item.product
        state = item.state
        readiness = (
            f"{state.readiness_percent}%"
            if state.readiness_percent is not None
            else "Не указана"
        )
        source = (
            "История"
            if state.readiness_source is ReadinessSource.PRODUCTION_EVENT
            else "Карточка изделия"
        )
        last_observation = (
            self.controller.utc_to_local(state.last_observed_at_utc).strftime(
                "%d.%m.%Y %H:%M"
            )
            if state.last_observed_at_utc is not None
            else "Нет наблюдений"
        )
        values = (
            product.object_name or "Объект не указан",
            product.name,
            product.serial_number or "Не указан",
            product.code or "Не указан",
            state.current_stage_name or "Не указан",
            readiness,
            source,
            last_observation,
            str(state.attachment_count),
        )
        for column, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setToolTip(value)
            cell.setData(Qt.ItemDataRole.UserRole, product.id)
            self.table.setItem(row, column, cell)

    def _update_actions(self) -> None:
        self.open_button.setEnabled(self.selected_product_id() is not None)

    def _emit_selected(self) -> None:
        product_id = self.selected_product_id()
        if product_id is not None:
            self.card_requested.emit(product_id)
