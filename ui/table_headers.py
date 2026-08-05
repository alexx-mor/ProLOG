"""Word-wrapped headers for every table in the application."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyleOptionHeader,
    QTableView,
)


class WordWrapHeaderView(QHeaderView):
    """A horizontal header that wraps labels at word boundaries."""

    MINIMUM_HEIGHT = 34
    HORIZONTAL_PADDING = 12
    VERTICAL_PADDING = 8

    def __init__(self, parent: QTableView) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.sectionResized.connect(lambda *_args: self._schedule_height_update())
        self.sectionCountChanged.connect(lambda *_args: self._schedule_height_update())
        self._height_update_pending = False

    def paintSection(self, painter, rect, logical_index: int) -> None:
        if not rect.isValid():
            return
        option = QStyleOptionHeader()
        self.initStyleOptionForIndex(option, logical_index)
        text = _wrappable_text(option.text)
        option.text = ""
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)

        right_padding = 6
        if option.sortIndicator != QStyleOptionHeader.SortIndicator.None_:
            right_padding += self.style().pixelMetric(
                QStyle.PixelMetric.PM_HeaderMarkSize,
                option,
                self,
            )
        text_rect = rect.adjusted(6, 4, -right_padding, -4)
        painter.save()
        painter.setPen(option.palette.buttonText().color())
        painter.setFont(self.font())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            text,
        )
        painter.restore()

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() in {
            QEvent.Type.FontChange,
            QEvent.Type.LayoutRequest,
            QEvent.Type.StyleChange,
        }:
            self._schedule_height_update()
        return result

    def _schedule_height_update(self) -> None:
        if self._height_update_pending:
            return
        self._height_update_pending = True
        QTimer.singleShot(0, self._update_height)

    def _update_height(self) -> None:
        self._height_update_pending = False
        required = self.MINIMUM_HEIGHT
        model = self.model()
        if model is not None:
            for section in range(self.count()):
                text = _wrappable_text(
                    str(model.headerData(section, Qt.Orientation.Horizontal) or "")
                )
                width = max(1, self.sectionSize(section) - self.HORIZONTAL_PADDING)
                bounds = self.fontMetrics().boundingRect(
                    QRect(0, 0, width, 1000),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    text,
                )
                required = max(required, bounds.height() + self.VERTICAL_PADDING)
        if self.height() != required:
            self.setFixedHeight(required)
            self.updateGeometry()


class TableHeaderInstaller(QObject):
    """Installs wrapped headers when current or future tables are polished by Qt."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(watched, QTableView):
            install_word_wrap_header(watched)
        return False


def install_word_wrap_header(table: QTableView) -> WordWrapHeaderView:
    current = table.horizontalHeader()
    if isinstance(current, WordWrapHeaderView):
        return current
    column_count = table.model().columnCount() if table.model() is not None else 0
    widths = [table.columnWidth(column) for column in range(column_count)]
    resize_modes = [current.sectionResizeMode(column) for column in range(column_count)]
    properties = {
        "stretch_last": current.stretchLastSection(),
        "clickable": current.sectionsClickable(),
        "movable": current.sectionsMovable(),
        "minimum": current.minimumSectionSize(),
        "default": current.defaultSectionSize(),
        "alignment": current.defaultAlignment(),
    }
    header = WordWrapHeaderView(table)
    table.setHorizontalHeader(header)
    model = table.model()
    if model is not None:
        model.headerDataChanged.connect(
            lambda orientation, _first, _last: (
                header._schedule_height_update()
                if orientation == Qt.Orientation.Horizontal
                else None
            )
        )
    header.setStretchLastSection(properties["stretch_last"])
    header.setSectionsClickable(properties["clickable"])
    header.setSectionsMovable(properties["movable"])
    header.setMinimumSectionSize(properties["minimum"])
    header.setDefaultSectionSize(properties["default"])
    header.setDefaultAlignment(properties["alignment"])
    for column, mode in enumerate(resize_modes):
        header.setSectionResizeMode(column, mode)
        if mode != QHeaderView.ResizeMode.Stretch:
            table.setColumnWidth(column, widths[column])
    header._schedule_height_update()
    return header


def install_table_header_support(app: QApplication) -> TableHeaderInstaller:
    installer = TableHeaderInstaller(app)
    app.installEventFilter(installer)
    return installer


def _wrappable_text(value: str) -> str:
    return value.replace("/", "/ ")
