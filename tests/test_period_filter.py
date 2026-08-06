"""GUI checks for month-based report filtering and 4K WorkBot layout."""

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from integrations.workbot.models import WorkBotInboxRow, WorkBotInboxStats
from models import DirectoryItem, ProductItem
from ui.period_filter import PeriodFilterWidget
from ui.workbot_inbox import WorkBotInboxWidget


class _FakeWorkBotService:
    def __init__(self, rows: list[WorkBotInboxRow] | None = None) -> None:
        self.rows = rows or []

    def list_rows(self, _status: str = "") -> list:
        return self.rows

    def inbox_stats(self, _source_path) -> WorkBotInboxStats:
        return WorkBotInboxStats(0, len(self.rows), 0, 0)


def test_month_mode_returns_full_calendar_month() -> None:
    app = QApplication.instance() or QApplication([])
    widget = PeriodFilterWidget()
    widget.mode.setCurrentIndex(widget.mode.findData(widget.MONTH_MODE))
    widget.month.setCurrentIndex(widget.month.findData(2))
    widget.year.setValue(2028)

    assert widget.date_from_value() == date(2028, 2, 1)
    assert widget.date_to_value() == date(2028, 2, 29)
    widget.close()
    app.processEvents()


def test_workbot_check_status_stays_top_aligned_at_4k() -> None:
    app = QApplication.instance() or QApplication([])
    widget = WorkBotInboxWidget(_FakeWorkBotService())
    widget.resize(3840, 2160)
    widget.issue.setText("Первая строка проверки\nВторая строка проверки")
    widget.show()
    app.processEvents()

    assert abs(widget.issue_label.geometry().top() - widget.issue.geometry().top()) <= 2
    assert widget.issue.geometry().top() < 900
    assert widget.source_text.geometry().bottom() < 1300
    assert widget.description.height() >= 140
    assert widget.source_text.height() >= 160
    assert widget.source_bar.geometry().top() < 30
    assert widget.summary_bar.geometry().top() < 80
    assert widget.summary_bar.height() <= 30
    assert widget.splitter.geometry().top() < 120
    assert widget.splitter.height() > widget.height() * 0.85
    assert widget.splitter.geometry().bottom() >= widget.height() - 30
    widget.close()
    app.processEvents()


def test_workbot_editor_fits_full_hd_without_vertical_scroll() -> None:
    app = QApplication.instance() or QApplication([])
    widget = WorkBotInboxWidget(_FakeWorkBotService())
    widget.resize(1920, 900)
    widget.show()
    app.processEvents()

    assert widget.editor_scroll.verticalScrollBar().maximum() == 0
    widget.close()
    app.processEvents()


def test_workbot_refresh_loads_next_row_after_selected_row_disappears() -> None:
    app = QApplication.instance() or QApplication([])
    first = _workbot_row(1, "Первая работа", "Первое сообщение")
    second = _workbot_row(2, "Следующая работа", "Следующее сообщение")
    service = _FakeWorkBotService([first, second])
    widget = WorkBotInboxWidget(service)
    widget.refresh()
    widget.table.selectRow(0)
    app.processEvents()
    assert widget.description.toPlainText() == "Первая работа"

    service.rows = [second]
    widget.refresh()
    app.processEvents()

    assert widget.table.currentRow() == 0
    assert widget.table.item(0, 0).data(Qt.ItemDataRole.UserRole) == second.id
    assert widget.description.toPlainText() == "Следующая работа"
    assert widget.source_text.toPlainText() == "Следующее сообщение"

    service.rows = []
    widget.refresh()
    assert widget.description.toPlainText() == ""
    assert widget.source_text.toPlainText() == ""
    widget.close()
    app.processEvents()


def test_workbot_product_combo_contains_only_current_object_products() -> None:
    app = QApplication.instance() or QApplication([])
    widget = WorkBotInboxWidget(_FakeWorkBotService())
    objects = [DirectoryItem("Жигалово", 1), DirectoryItem("УНР", 2)]
    products = [
        ProductItem(object_id=1, name="ШУВ", id=11),
        ProductItem(object_id=2, name="ШУВ", id=22),
    ]
    widget.set_reference_data([], [], objects, [], products)

    widget.object.setCurrentIndex(widget.object.findData(1))
    assert [widget.product.itemData(index) for index in range(widget.product.count())] == [None, 11]

    widget.object.setCurrentIndex(widget.object.findData(2))
    assert [widget.product.itemData(index) for index in range(widget.product.count())] == [None, 22]
    widget.close()
    app.processEvents()


def _workbot_row(row_id: int, description: str, raw_text: str) -> WorkBotInboxRow:
    return WorkBotInboxRow(
        id=row_id,
        max_message_id=f"message-{row_id}",
        revision=1,
        source_index=0,
        source_kind="strict",
        sender_id=100 + row_id,
        chat_id=None,
        received_at="",
        raw_text=raw_text,
        source_fragment=raw_text,
        employee_text="",
        work_date=date(2026, 8, 6),
        work_types=description,
        hours=8,
        object_text="",
        location_text="",
        product_text="",
        confidence=1,
        employee_id=None,
        object_id=None,
        location_id=None,
        work_type_id=None,
        product_id=None,
        status="ready",
        error_message="",
        worklog_entry_id=None,
    )
