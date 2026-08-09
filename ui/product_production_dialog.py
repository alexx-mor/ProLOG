"""Manual production card for an existing product."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from production.models import ProductionEventStatus, ProductionEventType
from production.projection_models import (
    ProductionTimelineItem,
    ReadinessSource,
)
from ui.production_controller import ProductionUiController, production_error_message
from ui.production_event_dialog import ProductionEventDialog
from ui.production_photo_viewer import ProductionPhoto, ProductionPhotoViewer
from ui.production_timeline_widget import (
    EVENT_TYPE_LABELS,
    STATUS_LABELS,
    ProductionTimelineWidget,
)


logger = logging.getLogger(__name__)


class ProductProductionDialog(QDialog):
    """Responsive read/write view of event-derived production history."""

    def __init__(
        self,
        controller: ProductionUiController,
        product_id: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.product_id = product_id
        self.product = controller.product(product_id)
        self.timeline_items: list[ProductionTimelineItem] = []
        self.photos: list[ProductionPhoto] = []
        self.setWindowTitle(f"Производство: {self.product.name}")
        self.setMinimumSize(980, 650)
        self.resize(1320, 820)

        self.product_title = QLabel()
        self.product_title.setObjectName("DialogTitle")
        self.product_meta = QLabel()
        self.product_meta.setObjectName("WizardSubtitle")
        self.product_meta.setWordWrap(True)
        self.stage_value = QLabel("Не указан")
        self.readiness_value = QLabel("Не указана")
        self.last_observation_value = QLabel("Нет наблюдений")
        self.readiness_source_value = QLabel("Из карточки изделия")
        self.production_counts_value = QLabel("Событий: 0 · Фотографий: 0")
        self.readiness_source_value.setWordWrap(True)
        self.readiness_source_value.setMaximumWidth(430)
        for label in (
            self.stage_value,
            self.readiness_value,
            self.last_observation_value,
            self.readiness_source_value,
            self.production_counts_value,
        ):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setMaximumWidth(300)

        self.tabs = QTabWidget()
        self.general_tab = self._build_general_tab()
        self.timeline_widget = ProductionTimelineWidget(self.controller.utc_to_local)
        self.production_tab = self._build_production_tab()
        self.worklogs_tab = self._build_worklogs_tab()
        self.photos_tab = self._build_photos_tab()
        self.tabs.addTab(self.general_tab, "Общие сведения")
        self.tabs.addTab(self.production_tab, "Производство")
        self.tabs.addTab(self.worklogs_tab, "Выполненные работы")
        self.tabs.addTab(self.photos_tab, "Фотографии")
        self.tabs.setCurrentIndex(1)

        self.close_button = QPushButton("Закрыть")
        self.close_button.setMinimumWidth(120)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)
        layout.addWidget(self._build_header())
        layout.addWidget(self.tabs, 1)
        layout.addLayout(footer)

        self.close_button.clicked.connect(self.accept)
        self.add_event_button.clicked.connect(self._add_observation)
        self.add_first_button.clicked.connect(self._add_observation)
        self.rework_button.clicked.connect(self._add_rework)
        self.timeline_widget.audit_changed.connect(lambda _checked: self.refresh())
        self.timeline_widget.details_requested.connect(self._show_details)
        self.timeline_widget.correction_requested.connect(self._correct_event)
        self.timeline_widget.photos_requested.connect(self._open_event_photos)
        self.gallery.itemDoubleClicked.connect(self._open_gallery_item)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild all views from services after every successful command."""

        try:
            self.product = self.controller.product(self.product_id)
            state = self.controller.state(self.product_id)
            self.timeline_items = self.controller.timeline(
                self.product_id,
                include_audit=self.timeline_widget.audit_checkbox.isChecked(),
            )
            self._refresh_header(state)
            self._refresh_general()
            self.timeline_widget.set_items(self.timeline_items)
            self.add_first_button.setVisible(not self.timeline_items)
            self._refresh_worklogs()
            self._refresh_gallery()
        except Exception as exc:
            logger.exception("Не удалось обновить карточку производства")
            self._show_error(production_error_message(exc))

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("ProductionHeader")
        title_layout = QVBoxLayout()
        title_layout.addWidget(self.product_title)
        title_layout.addWidget(self.product_meta)
        state_form = QFormLayout()
        state_form.setContentsMargins(0, 0, 0, 0)
        state_form.addRow("Текущий этап", self.stage_value)
        state_form.addRow("Готовность", self.readiness_value)
        state_form.addRow("Источник готовности", self.readiness_source_value)
        state_form.addRow("Последнее наблюдение", self.last_observation_value)
        state_form.addRow("История", self.production_counts_value)
        right = QVBoxLayout()
        right.addLayout(state_form)
        right.addWidget(self.progress)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addLayout(title_layout, 1)
        layout.addLayout(right)
        return frame

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        self.general_values: dict[str, QLabel] = {}
        form = QFormLayout()
        fields = (
            ("object", "Объект"),
            ("serial", "Заводской номер"),
            ("code", "Шифр"),
            ("status", "Состояние изделия"),
            ("start", "Начало изготовления"),
            ("release", "Дата выпуска"),
            ("active", "Активность"),
        )
        for key, caption in fields:
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self.general_values[key] = value
            form.addRow(caption, value)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addLayout(form)
        layout.addStretch()
        return tab

    def _build_production_tab(self) -> QWidget:
        tab = QWidget()
        self.add_event_button = QPushButton("Добавить наблюдение")
        self.rework_button = QPushButton("Возврат / переработка")
        self.add_first_button = QPushButton("Добавить первое наблюдение")
        self.add_first_button.setMinimumWidth(220)
        actions = QHBoxLayout()
        actions.addWidget(self.add_event_button)
        actions.addWidget(self.rework_button)
        actions.addStretch()
        actions.addWidget(self.add_first_button)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(actions)
        layout.addWidget(self.timeline_widget, 1)
        return tab

    def _build_worklogs_tab(self) -> QWidget:
        tab = QWidget()
        self.worklogs_table = QTableWidget(0, 6)
        self.worklogs_table.setHorizontalHeaderLabels(
            ["Дата", "Сотрудник", "Вид работ", "Описание", "Часы", "Местонахождение"]
        )
        self.worklogs_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.worklogs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.worklogs_table.setAlternatingRowColors(True)
        self.worklogs_table.verticalHeader().setVisible(False)
        header = self.worklogs_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.worklogs_table.setColumnWidth(0, 100)
        self.worklogs_table.setColumnWidth(1, 210)
        self.worklogs_table.setColumnWidth(2, 170)
        self.worklogs_table.setColumnWidth(3, 370)
        self.worklogs_table.setColumnWidth(4, 70)
        self.worklog_summary = QLabel()
        self.worklog_summary.setObjectName("WizardSubtitle")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.worklogs_table, 1)
        layout.addWidget(self.worklog_summary)
        return tab

    def _build_photos_tab(self) -> QWidget:
        tab = QWidget()
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListView.ViewMode.IconMode)
        self.gallery.setIconSize(QSize(180, 125))
        self.gallery.setGridSize(QSize(210, 180))
        self.gallery.setResizeMode(QListView.ResizeMode.Adjust)
        self.gallery.setMovement(QListView.Movement.Static)
        self.gallery.setWordWrap(True)
        self.gallery_empty = QLabel("Фотографии пока не прикреплены")
        self.gallery_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gallery_empty.setObjectName("WizardSubtitle")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self.gallery_empty)
        layout.addWidget(self.gallery, 1)
        return tab

    def _refresh_header(self, state) -> None:
        serial = f" · зав. № {self.product.serial_number}" if self.product.serial_number else ""
        self.product_title.setText(f"{self.product.name}{serial}")
        self.product_meta.setText(
            f"{self.product.object_name or 'Объект не указан'}"
            + (f" · {self.product.code}" if self.product.code else "")
        )
        self.stage_value.setText(state.current_stage_name or "Не указан")
        if state.readiness_percent is None:
            readiness = "Не указана"
            self.progress.setValue(0)
            self.progress.setFormat("Нет данных")
        else:
            readiness = f"{state.readiness_percent}%"
            if state.readiness_source is ReadinessSource.LEGACY_SNAPSHOT:
                readiness += " (из карточки изделия)"
            self.progress.setValue(state.readiness_percent)
            self.progress.setFormat(f"{state.readiness_percent}%")
        self.readiness_value.setText(readiness)
        if state.readiness_source is ReadinessSource.LEGACY_SNAPSHOT:
            self.readiness_source_value.setText(
                "Текущее значение из карточки изделия. Производственная история "
                "еще не содержит подтвержденной оценки готовности."
            )
        else:
            self.readiness_source_value.setText("Подтвержденная производственная история")
        self.production_counts_value.setText(
            f"Событий: {state.event_count} · Фотографий: {state.attachment_count}"
        )
        if state.last_observed_at_utc:
            local = self.controller.utc_to_local(state.last_observed_at_utc)
            self.last_observation_value.setText(local.strftime("%d.%m.%Y %H:%M"))
        else:
            self.last_observation_value.setText("Нет наблюдений")

    def _refresh_general(self) -> None:
        values = {
            "object": self.product.object_name or "Не указан",
            "serial": self.product.serial_number or "Не указан",
            "code": self.product.code or "Не указан",
            "status": self.product.product_status or "Не указан",
            "start": self.product.start_date or "Не указана",
            "release": self.product.release_date or "Не указана",
            "active": "Активно" if self.product.is_active else "Отключено",
        }
        for key, value in values.items():
            self.general_values[key].setText(value)

    def _refresh_worklogs(self) -> None:
        entries = self.controller.worklogs_for_product(self.product_id)
        self.worklogs_table.setRowCount(len(entries))
        total_hours = 0.0
        for row, entry in enumerate(entries):
            total_hours += float(entry.hours)
            values = (
                entry.work_date.strftime("%d.%m.%Y"),
                entry.employee_name or f"ID {entry.employee_id}",
                entry.work_type_name or "Не указан",
                entry.description,
                _format_hours(entry.hours),
                entry.location_name or "Не указано",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                self.worklogs_table.setItem(row, column, cell)
        self.worklog_summary.setText(
            f"Записей: {len(entries)} · Сотрудников: "
            f"{len({entry.employee_id for entry in entries})} · "
            f"Человеко-часов: {_format_hours(total_hours)}"
        )

    def _refresh_gallery(self) -> None:
        self.gallery.clear()
        self.photos = []
        for timeline_item in self.timeline_items:
            local = self.controller.utc_to_local(timeline_item.event.observed_at_utc)
            readiness = (
                f"{timeline_item.event.readiness_percent}%"
                if timeline_item.event.readiness_percent is not None
                else "готовность не указана"
            )
            event_label = (
                f"{local.strftime('%d.%m.%Y %H:%M')} · "
                f"{timeline_item.stage.name if timeline_item.stage else 'этап не указан'} · "
                f"{readiness}"
                + (
                    f" · {timeline_item.event.description}"
                    if timeline_item.event.description
                    else ""
                )
            )
            for attachment in timeline_item.attachments:
                photo = ProductionPhoto(attachment, event_label)
                photo_index = len(self.photos)
                self.photos.append(photo)
                item = QListWidgetItem(
                    self._thumbnail_icon(attachment.attachment.id or 0),
                f"{attachment.attachment.original_name}\n{event_label}",
                )
                item.setData(Qt.ItemDataRole.UserRole, photo_index)
                item.setToolTip(
                    f"{attachment.attachment.original_name}\nСобытие: {event_label}"
                )
                self.gallery.addItem(item)
        self.gallery_empty.setVisible(not self.photos)
        self.gallery.setVisible(bool(self.photos))

    def _thumbnail_icon(self, attachment_id: int) -> QIcon:
        canvas = QPixmap(180, 125)
        canvas.fill(QColor("#eef1f4"))
        try:
            source = QPixmap()
            if source.loadFromData(self.controller.attachment_bytes(attachment_id)):
                scaled = source.scaled(
                    canvas.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = (canvas.width() - scaled.width()) // 2
                y = (canvas.height() - scaled.height()) // 2
                from PySide6.QtGui import QPainter

                painter = QPainter(canvas)
                painter.drawPixmap(x, y, scaled)
                painter.end()
        except Exception:
            logger.warning("Недоступна миниатюра Attachment %s", attachment_id)
        return QIcon(canvas)

    def _add_observation(self) -> None:
        self._open_event_dialog(ProductionEventType.OBSERVATION)

    def _add_rework(self) -> None:
        self._open_event_dialog(ProductionEventType.REWORK)

    def _open_event_dialog(
        self,
        event_type: ProductionEventType,
        source_item: ProductionTimelineItem | None = None,
    ) -> None:
        dialog = ProductionEventDialog(
            self.controller,
            self.product,
            event_type=event_type,
            source_event=source_item.event if source_item else None,
            source_attachments=source_item.attachments if source_item else (),
            parent=self,
        )
        accepted = dialog.exec()
        if accepted:
            self.refresh()
            if dialog.saved_event and dialog.saved_event.id:
                self.timeline_widget.select_event(dialog.saved_event.id)
            return
        if dialog.correction_requested:
            source = self._latest_effective_readiness_item()
            if source is None:
                self._show_error("Нет подтвержденной записи готовности для исправления")
                return
            self._open_event_dialog(ProductionEventType.CORRECTION, source)

    def _latest_effective_readiness_item(self) -> ProductionTimelineItem | None:
        return next(
            (
                item
                for item in reversed(self.timeline_items)
                if item.is_effective and item.event.readiness_percent is not None
            ),
            None,
        )

    def _correct_event(self, event_id: int) -> None:
        item = self._timeline_item(event_id)
        if item is not None:
            self._open_event_dialog(ProductionEventType.CORRECTION, item)

    def _show_details(self, event_id: int) -> None:
        item = self._timeline_item(event_id)
        if item is None:
            return
        event = item.event
        local_observed = self.controller.utc_to_local(event.observed_at_utc)
        lines = [
            ("UID", str(event.uid)),
            ("Статус", STATUS_LABELS.get(event.status, event.status.value)),
            ("Тип", EVENT_TYPE_LABELS.get(event.event_type, event.event_type.value)),
            ("Наблюдалось", local_observed.strftime("%d.%m.%Y %H:%M")),
            ("Зафиксировано UTC", event.recorded_at_utc.isoformat()),
            ("Этап", item.stage.name if item.stage else "Не указан"),
            (
                "Готовность",
                f"{event.readiness_percent}%"
                if event.readiness_percent is not None
                else "Не указана",
            ),
            ("Сообщил", item.reported_employee_name or "Не указан"),
            ("Создал", event.created_by.display_name),
            (
                "Подтвердил",
                event.confirmed_by.display_name if event.confirmed_by else "Не подтверждено",
            ),
            ("Источник", event.source_type.value),
            ("Ссылка источника", event.source_ref or "Нет"),
            ("Исправляет событие", str(event.supersedes_event_id or "Нет")),
            ("Исправлено событием", str(item.superseded_by_event_id or "Нет")),
            ("Причина изменения", event.change_reason or "Не указана"),
            ("Ключ идемпотентности", event.idempotency_key or "Не задан"),
            ("Фотографий", str(len(item.attachments))),
            ("Связанных работ", str(len(item.worklogs))),
        ]
        dialog = QDialog(self)
        dialog.setWindowTitle("Производственная запись")
        dialog.resize(720, 580)
        browser = QTextBrowser()
        browser.setHtml(
            "<table cellspacing='7'>"
            + "".join(
                f"<tr><td><b>{_escape(label)}</b></td><td>{_escape(value)}</td></tr>"
                for label, value in lines
            )
            + "</table><hr><b>Описание</b><p>"
            + _escape(event.description or "Описание отсутствует")
            + "</p><hr><b>Вложения</b><p>"
            + (
                "<br>".join(
                    _escape(
                        f"{attachment.attachment.original_name}; "
                        f"{attachment.attachment.mime_type}; "
                        f"{attachment.attachment.size_bytes} байт; "
                        f"SHA-256 {attachment.attachment.sha256}"
                    )
                    for attachment in item.attachments
                )
                or "Вложения отсутствуют"
            )
            + "</p>"
        )
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.accept)
        layout = QVBoxLayout(dialog)
        layout.addWidget(browser)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _open_event_photos(self, event_id: int) -> None:
        item = self._timeline_item(event_id)
        if item is None:
            return
        local = self.controller.utc_to_local(item.event.observed_at_utc)
        photos = [
            ProductionPhoto(attachment, local.strftime("%d.%m.%Y %H:%M"))
            for attachment in item.attachments
        ]
        ProductionPhotoViewer(self.controller, photos, self).exec()

    def _open_gallery_item(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int):
            ProductionPhotoViewer(
                self.controller,
                self.photos,
                self,
                initial_index=index,
            ).exec()

    def _timeline_item(self, event_id: int) -> ProductionTimelineItem | None:
        return next(
            (item for item in self.timeline_items if item.event.id == event_id),
            None,
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Производство", message)


def _format_hours(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _escape(value: object) -> str:
    import html

    return html.escape(str(value)).replace("\n", "<br>")
