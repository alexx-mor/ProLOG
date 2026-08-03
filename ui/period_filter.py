"""Reusable date-range and month filter for reports and analytics."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QWidget,
)

from ui.worklog_widget import CalendarDateEdit


MONTH_NAMES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


class PeriodFilterWidget(QWidget):
    """Selects either an optional custom range or a complete calendar month."""

    RANGE_MODE = "range"
    MONTH_MODE = "month"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        today = QDate.currentDate()
        self.mode = QComboBox()
        self.mode.addItem("Диапазон дат", self.RANGE_MODE)
        self.mode.addItem("Месяц и год", self.MONTH_MODE)
        self.date_from_enabled = QCheckBox("С даты")
        self.date_to_enabled = QCheckBox("По дату")
        self.date_from = CalendarDateEdit(today)
        self.date_to = CalendarDateEdit(today)
        self.month = QComboBox()
        for month_number, month_name in enumerate(MONTH_NAMES, start=1):
            self.month.addItem(month_name, month_number)
        self.month.setCurrentIndex(today.month() - 1)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(today.year())
        self.stack = QStackedWidget()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setMaximumHeight(125)
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.stack.setMaximumHeight(82)
        self._setup_dates()
        self._build_layout()
        self._connect()
        self._sync_mode()

    def date_from_value(self) -> date | None:
        if self.mode.currentData() == self.MONTH_MODE:
            return date(self.year.value(), int(self.month.currentData()), 1)
        return self.date_from.date().toPython() if self.date_from_enabled.isChecked() else None

    def date_to_value(self) -> date | None:
        if self.mode.currentData() == self.MONTH_MODE:
            year = self.year.value()
            month = int(self.month.currentData())
            return date(year, month, monthrange(year, month)[1])
        return self.date_to.date().toPython() if self.date_to_enabled.isChecked() else None

    def clear(self) -> None:
        self.mode.setCurrentIndex(self.mode.findData(self.RANGE_MODE))
        self.date_from_enabled.setChecked(False)
        self.date_to_enabled.setChecked(False)

    def _setup_dates(self) -> None:
        for date_edit in (self.date_from, self.date_to):
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("dd.MM.yyyy")
            date_edit.lineEdit().setReadOnly(True)
            date_edit.setEnabled(False)

    def _build_layout(self) -> None:
        range_panel = QWidget()
        range_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        range_layout = QGridLayout(range_panel)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self.date_from_enabled, 0, 0)
        range_layout.addWidget(self.date_from, 0, 1)
        range_layout.addWidget(self.date_to_enabled, 1, 0)
        range_layout.addWidget(self.date_to, 1, 1)

        month_panel = QWidget()
        month_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        month_layout = QHBoxLayout(month_panel)
        month_layout.setContentsMargins(0, 0, 0, 0)
        month_layout.addWidget(self.month, 1)
        month_layout.addWidget(QLabel("Год"))
        month_layout.addWidget(self.year)

        self.stack.addWidget(range_panel)
        self.stack.addWidget(month_panel)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Режим"), 0, 0)
        layout.addWidget(self.mode, 0, 1)
        layout.addWidget(self.stack, 1, 0, 1, 2)

    def _connect(self) -> None:
        self.mode.currentIndexChanged.connect(self._sync_mode)
        self.date_from_enabled.toggled.connect(self.date_from.setEnabled)
        self.date_to_enabled.toggled.connect(self.date_to.setEnabled)

    def _sync_mode(self) -> None:
        self.stack.setCurrentIndex(1 if self.mode.currentData() == self.MONTH_MODE else 0)
