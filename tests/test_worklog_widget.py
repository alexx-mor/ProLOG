"""Tests for opening saved work log entries from the editor table."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QApplication

from models import WorkLogEntry
from ui.worklog_widget import WorkLogWidget


def test_double_click_handler_requests_saved_entry() -> None:
    app = QApplication.instance() or QApplication([])
    widget = WorkLogWidget()
    opened: list[int] = []
    widget.entry_open_requested.connect(opened.append)
    widget.set_employee_entries(
        [
            WorkLogEntry(
                id=42,
                employee_id=7,
                work_date=date(2026, 8, 4),
                location_id=None,
                object_id=None,
                work_type_id=None,
                description="Монтаж шкафа",
                hours=8,
            )
        ]
    )
    widget.employee_entries.selectRow(0)

    widget._open_selected_entry()

    assert opened == [42]
    widget.close()
    app.processEvents()
