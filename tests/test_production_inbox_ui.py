"""Qt/offscreen tests for the P11 photo-report review workspace."""

from __future__ import annotations

import ast
import base64
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from production.review_models import (
    InboxSourceAttachmentView,
    InboxSourceMessageView,
    ProductionInboxReviewDetail,
    ProductionInboxReviewItem,
    RefreshSummary,
    ReviewFilter,
    ReviewStatus,
)
from ui.production_inbox_widget import ProductionInboxWidget


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
NOW = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)


class FakeController:
    def __init__(self, *, unavailable=False) -> None:
        self.item = ProductionInboxReviewItem(
            1, uuid4(), "a" * 64, "complete", "deterministic", 1,
            -77703766302910, 42, "Мастер", NOW, 1, "production-matcher-v1",
            "b" * 64, 1, 0, "Исходный текст без изменений", 11, 7, 3, 70,
            "Исходный текст без изменений", "exact", False, "", True, 2,
            None, ReviewStatus.REQUIRES_REVIEW, None,
        )
        messages = (
            InboxSourceMessageView(1, 0, "photo_source", "m1", 1, 1, "", NOW, 42, "Мастер"),
            InboxSourceMessageView(2, 1, "closing_text", "m2", 2, 1,
                                   "Исходный текст без изменений", NOW, 42, "Мастер"),
        )
        state = "missing" if unavailable else "available"
        attachments = tuple(
            InboxSourceAttachmentView(
                index + 1, 1, 0, index, "m1", f"a{index}", f"{index}.png",
                "image/png", "c" * 64, f"internal/{index}", state, "downloaded",
            )
            for index in range(2)
        )
        self.detail_value = ProductionInboxReviewDetail(
            self.item, messages, attachments, (), (), (), (), (), None,
        )
        self.refresh_called = 0

    def list_items(self, selected=ReviewFilter.REQUIRES_REVIEW, **_filters):
        if selected in {ReviewFilter.ALL, ReviewFilter.REQUIRES_REVIEW}:
            return [self.item]
        return []

    def detail(self, _item):
        return self.detail_value

    def products(self):
        return [SimpleNamespace(
            id=11, object_id=7, object_name="Объект", name="ШУ1",
            serial_number="3075", code="CODE",
        )]

    def objects(self):
        return [SimpleNamespace(id=7, name="Объект")]

    def active_stages(self):
        return [SimpleNamespace(id=3, name="Электромонтаж")]

    def employees_for_reporting(self):
        return []

    def local_observed_at(self, item):
        return item.observed_at_utc

    def local_datetime(self, value):
        return value

    def source_attachment_bytes(self, _item, attachment_id):
        attachment = next(row for row in self.detail_value.attachments if row.id == attachment_id)
        if attachment.media_state != "available":
            raise FileNotFoundError("missing")
        return PNG

    def refresh_source(self):
        self.refresh_called += 1
        time.sleep(0.03)
        return RefreshSummary(1, 0, 0, 1, 0, 1, 1, 0, NOW)

    def current_readiness(self, _product_id):
        return 60


@pytest.fixture(scope="module", autouse=True)
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_photo_reports_widget_opens_with_requires_review_default() -> None:
    widget = ProductionInboxWidget(FakeController())
    assert ReviewFilter(widget.filter_combo.currentData()) is ReviewFilter.REQUIRES_REVIEW
    assert widget.table.rowCount() == 1
    assert "Требуют проверки: 1" in widget.counters.text()


def test_exact_source_text_and_photo_order_are_shown() -> None:
    widget = ProductionInboxWidget(FakeController())
    widget.table.selectRow(0)
    QApplication.processEvents()
    assert widget.source_text.toPlainText() == "Исходный текст без изменений"
    assert [widget.photos.item(i).text() for i in range(2)] == ["0.png", "1.png"]
    assert widget.product_combo.currentData() == 11
    assert widget.stage_combo.currentData() == 3
    assert widget.readiness.value() == 70


def test_unavailable_source_photo_has_placeholder() -> None:
    widget = ProductionInboxWidget(FakeController(unavailable=True))
    widget.table.selectRow(0)
    QApplication.processEvents()
    assert all("Недоступно" in widget.photos.item(i).text() for i in range(2))


def test_refresh_runs_off_ui_thread(application: QApplication) -> None:
    controller = FakeController()
    widget = ProductionInboxWidget(controller)
    widget.request_refresh()
    assert not widget.refresh_button.isEnabled()
    deadline = time.monotonic() + 2
    while widget._thread is not None and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    assert controller.refresh_called == 1
    assert widget.refresh_button.isEnabled()
    assert "Новых сообщений: 1" in widget.last_refresh.text()


@pytest.mark.parametrize("size", [(1600, 900), (3840, 2160)])
def test_fhd_and_4k_layout_has_no_root_overflow(size, application: QApplication) -> None:
    widget = ProductionInboxWidget(FakeController())
    widget.resize(*size)
    widget.show()
    application.processEvents()
    assert widget.layout().geometry().right() <= widget.rect().right()
    assert widget.layout().geometry().bottom() <= widget.rect().bottom()
    widget.close()


def test_navigation_order_in_main_window_source() -> None:
    source = Path("ui/main_window.py").read_text(encoding="utf-8")
    labels = [
        '"Заполнение отчетов"', '"Входящие отчеты"', '"Фотоотчёты"',
        '"Производство"', '"Просмотр отчетов"', '"Аналитика"',
    ]
    offsets = [source.index(label) for label in labels]
    assert offsets == sorted(offsets)


def test_p11_ui_has_no_sqlite_repository_store_or_workbot_parser_imports() -> None:
    forbidden = {
        "sqlite3", "production.review_repository", "production.local_attachment_store",
        "workbot", "integrations.workbot", "integrations.workbot.service",
    }
    for path in (
        Path("ui/production_inbox_controller.py"),
        Path("ui/production_inbox_widget.py"),
        Path("ui/production_inbox_photo_viewer.py"),
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        imported.update(
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert all(
            not any(name == blocked or name.startswith(blocked + ".") for blocked in forbidden)
            for name in imported
        ), path
