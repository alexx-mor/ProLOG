"""Persistent sizing tests for directory tables."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QHeaderView

from database import Database, DirectoryRepository
from services import DirectoryService
from ui.dialogs import DirectoryDialog


def test_product_columns_are_resizable_and_persisted(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "prolog.sqlite3")
    database.initialize()
    directories = DirectoryService(DirectoryRepository(database))

    first = DirectoryDialog(
        directories,
        initial_key="products",
        can_edit_databases=False,
    )
    header = first.table.horizontalHeader()
    assert all(
        header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
        for column in range(first.table.columnCount())
    )
    first.table.setColumnWidth(1, 177)
    first.accept()

    second = DirectoryDialog(
        directories,
        initial_key="products",
        can_edit_databases=False,
    )
    assert second.table.columnWidth(1) == 177
    second.close()
    app.processEvents()
