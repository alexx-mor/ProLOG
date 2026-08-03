"""GUI checks for month-based report filtering and 4K WorkBot layout."""

from datetime import date

from PySide6.QtWidgets import QApplication

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
    widget.close()
    app.processEvents()
