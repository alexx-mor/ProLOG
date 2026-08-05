from PySide6.QtWidgets import QApplication, QTableWidget

from ui.table_headers import WordWrapHeaderView, install_table_header_support


def test_table_header_wraps_when_column_is_narrowed() -> None:
    app = QApplication.instance() or QApplication([])
    installer = install_table_header_support(app)
    table = QTableWidget(0, 1)
    table.setHorizontalHeaderLabels(["Разряд/категория сотрудника"])
    table.setColumnWidth(0, 100)
    table.resize(300, 200)
    table.show()
    app.processEvents()
    header = table.horizontalHeader()

    assert isinstance(table.horizontalHeader(), WordWrapHeaderView)
    assert header.height() > header.MINIMUM_HEIGHT
    table.close()
    app.removeEventFilter(installer)
    app.processEvents()
