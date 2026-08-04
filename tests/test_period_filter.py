"""GUI checks for month-based report filtering and 4K WorkBot layout."""

from datetime import date

from PySide6.QtWidgets import QApplication

from models import DirectoryItem, ProductItem
from ui.period_filter import PeriodFilterWidget
from ui.workbot_inbox import WorkBotInboxWidget


class _FakeWorkBotService:
    def list_rows(self, _status: str = "") -> list:
        return []


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
    assert widget.description.height() >= 170
    assert widget.source_text.height() >= 200
    assert widget.source_bar.geometry().top() < 30
    assert widget.summary_bar.geometry().top() < 80
    assert widget.summary_bar.height() <= 30
    assert widget.splitter.geometry().top() < 120
    assert widget.splitter.height() > widget.height() * 0.85
    assert widget.splitter.geometry().bottom() >= widget.height() - 30
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
