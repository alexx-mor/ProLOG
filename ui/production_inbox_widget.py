"""P11 production photo-report queue and human review workspace."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from production.models import ProductionEventType
from production.review_models import RejectionCode, ReviewFilter, ReviewStatus
from ui.production_inbox_photo_viewer import ProductionInboxPhotoViewer


FILTER_LABELS = (
    ("Требуют проверки", ReviewFilter.REQUIRES_REVIEW),
    ("Подтверждены", ReviewFilter.CONFIRMED),
    ("Отклонены", ReviewFilter.REJECTED),
    ("Источник изменен", ReviewFilter.SOURCE_CHANGED),
    ("Без описания", ReviewFilter.NEEDS_DESCRIPTION),
    ("Только текст", ReviewFilter.TEXT_ONLY),
    ("Все", ReviewFilter.ALL),
)

STATUS_LABELS = {
    ReviewStatus.REQUIRES_REVIEW: "Требует проверки",
    ReviewStatus.CONFIRMING: "Подтверждение не завершено",
    ReviewStatus.CONFIRMED: "Подтвержден",
    ReviewStatus.REJECTED: "Отклонен",
    ReviewStatus.SOURCE_CHANGED: "Источник изменен",
    ReviewStatus.KEPT_EXISTING: "Оставлено без изменения",
    ReviewStatus.FAILED: "Ошибка подтверждения",
}


class _RefreshWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.controller.refresh_source())
        except Exception as exc:
            self.failed.emit(str(exc))


class ProductionInboxWidget(QWidget):
    product_open_requested = Signal(int)
    event_open_requested = Signal(int)
    status_message = Signal(str)

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.items = []
        self.current_item = None
        self.current_detail = None
        self._thread = None
        self._worker = None
        self._build_ui()
        self._connect()
        self.reload_directories()
        self.refresh_queue()

    def _build_ui(self) -> None:
        header = QHBoxLayout()
        self.filter_combo = QComboBox()
        for label, value in FILTER_LABELS:
            self.filter_combo.addItem(label, value)
        self.period_enabled = QCheckBox("Период")
        self.date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit(QDate.currentDate())
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setEnabled(False)
        self.sender_filter = QComboBox()
        self.object_filter = QComboBox()
        self.product_filter = QComboBox()
        self.refresh_button = QPushButton("Обновить")
        self.last_refresh = QLabel("Последнее обновление: -")
        header.addWidget(self.filter_combo)
        header.addWidget(self.period_enabled)
        header.addWidget(self.date_from)
        header.addWidget(self.date_to)
        header.addWidget(self.sender_filter)
        header.addWidget(self.object_filter)
        header.addWidget(self.product_filter)
        header.addStretch(1)
        header.addWidget(self.refresh_button)

        self.counters = QLabel("Требуют проверки: 0 | Изменены после проверки: 0")
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels((
            "Дата/время", "Отправитель", "Объект", "Изделие", "Этап",
            "Готовность", "Фото", "Распознавание", "Проверка",
        ))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.addWidget(self.counters)
        left_layout.addWidget(self.table, 1)
        grouping = QHBoxLayout()
        self.split_button = QPushButton("Разделить")
        self.merge_button = QPushButton("Объединить")
        grouping.addWidget(self.split_button)
        grouping.addWidget(self.merge_button)
        grouping.addStretch(1)
        left_layout.addLayout(grouping)

        self.source_messages = QListWidget()
        self.source_messages.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.source_text = QPlainTextEdit()
        self.source_text.setReadOnly(True)
        self.source_text.setMaximumHeight(145)
        self.photos = QListWidget()
        self.photos.setViewMode(QListWidget.ViewMode.IconMode)
        self.photos.setIconSize(QPixmap(132, 96).size())
        self.photos.setMinimumHeight(120)
        self.photos.setMaximumHeight(180)

        source_box = QGroupBox("Исходный фотоотчет")
        source_layout = QVBoxLayout(source_box)
        source_layout.addWidget(self.source_messages)
        source_layout.addWidget(self.source_text)
        source_layout.addWidget(self.photos)

        self.product_combo = QComboBox()
        self.stage_combo = QComboBox()
        self.readiness = QSpinBox()
        self.readiness.setRange(-1, 100)
        self.readiness.setSpecialValueText("Не указана")
        self.observed_at = QDateTimeEdit()
        self.observed_at.setCalendarPopup(True)
        self.reporter_combo = QComboBox()
        self.event_type = QComboBox()
        self.event_type.addItem("Наблюдение", ProductionEventType.OBSERVATION)
        self.event_type.addItem("Возврат / переработка", ProductionEventType.REWORK)
        self.event_type.addItem("Исправление", ProductionEventType.CORRECTION)
        self.change_reason = QPlainTextEdit()
        self.change_reason.setPlaceholderText("Причина снижения готовности или исправления")
        self.change_reason.setMaximumHeight(60)
        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(105)
        form = QFormLayout()
        form.addRow("Изделие", self.product_combo)
        form.addRow("Этап", self.stage_combo)
        form.addRow("Готовность, %", self.readiness)
        form.addRow("Фактическая дата", self.observed_at)
        form.addRow("Сообщил", self.reporter_combo)
        form.addRow("Тип события", self.event_type)
        form.addRow("Причина", self.change_reason)
        form.addRow("Описание", self.description)

        self.remember_product = QCheckBox("Запомнить соответствие изделия")
        self.product_alias = QLineEdit()
        self.product_alias.setPlaceholderText("Короткое обозначение, например ШУ-1")
        self.remember_stage = QCheckBox("Запомнить соответствие этапа")
        self.stage_alias = QLineEdit()
        self.stage_alias.setPlaceholderText("Короткий термин, например электромонтаж")
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMaximumHeight(120)
        decision_box = QGroupBox("Решение")
        decision_layout = QVBoxLayout(decision_box)
        decision_layout.addLayout(form)
        decision_layout.addWidget(self.remember_product)
        decision_layout.addWidget(self.product_alias)
        decision_layout.addWidget(self.remember_stage)
        decision_layout.addWidget(self.stage_alias)
        decision_layout.addWidget(QLabel("Почему так определено"))
        decision_layout.addWidget(self.evidence)
        buttons = QHBoxLayout()
        self.confirm_button = QPushButton("Подтвердить")
        self.reject_button = QPushButton("Отклонить")
        self.keep_button = QPushButton("Оставить событие")
        self.open_button = QPushButton("Открыть в производстве")
        buttons.addWidget(self.confirm_button)
        buttons.addWidget(self.reject_button)
        buttons.addWidget(self.keep_button)
        buttons.addWidget(self.open_button)
        decision_layout.addLayout(buttons)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.addWidget(source_box, 1)
        right_layout.addWidget(decision_box, 2)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([760, 620])

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.last_refresh)
        layout.addWidget(splitter, 1)

    def _connect(self) -> None:
        self.refresh_button.clicked.connect(self.request_refresh)
        self.filter_combo.currentIndexChanged.connect(self.refresh_queue)
        self.period_enabled.toggled.connect(self.date_from.setEnabled)
        self.period_enabled.toggled.connect(self.date_to.setEnabled)
        self.period_enabled.toggled.connect(self.refresh_queue)
        self.date_from.dateChanged.connect(self.refresh_queue)
        self.date_to.dateChanged.connect(self.refresh_queue)
        for combo in (self.sender_filter, self.object_filter, self.product_filter):
            combo.currentIndexChanged.connect(self.refresh_queue)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.photos.itemDoubleClicked.connect(self._open_photo)
        self.confirm_button.clicked.connect(self._confirm)
        self.reject_button.clicked.connect(self._reject)
        self.keep_button.clicked.connect(self._keep_existing)
        self.open_button.clicked.connect(self._open_production)
        self.split_button.clicked.connect(self._split)
        self.merge_button.clicked.connect(self._merge)

    def reload_directories(self) -> None:
        products = self.controller.products()
        objects = self.controller.objects()
        self.product_combo.clear()
        self.product_filter.clear()
        self.product_filter.addItem("Все изделия", None)
        for product in products:
            label = _product_label(product)
            self.product_combo.addItem(label, product.id)
            self.product_filter.addItem(label, product.id)
        self.object_filter.clear()
        self.object_filter.addItem("Все объекты", None)
        for item in objects:
            self.object_filter.addItem(item.name, item.id)
        self.stage_combo.clear()
        self.stage_combo.addItem("Не определен", None)
        for stage in self.controller.active_stages():
            self.stage_combo.addItem(stage.name, stage.id)
        self.reporter_combo.clear()
        self.reporter_combo.addItem("Не указан", None)
        for employee in self.controller.employees_for_reporting():
            self.reporter_combo.addItem(employee.full_name, employee.id)

    def refresh_queue(self) -> None:
        selected = ReviewFilter(
            self.filter_combo.currentData() or ReviewFilter.REQUIRES_REVIEW.value
        )
        filters = {
            "sender_max_user_id": self.sender_filter.currentData(),
            "object_id": self.object_filter.currentData(),
            "product_id": self.product_filter.currentData(),
        }
        if self.period_enabled.isChecked():
            filters["date_from"] = self.date_from.date().toPython()
            filters["date_to"] = self.date_to.date().toPython()
        all_rows = self.controller.list_items(ReviewFilter.ALL)
        senders = sorted({
            (row.sender_max_user_id, row.sender_display_snapshot or str(row.sender_max_user_id))
            for row in all_rows if row.sender_max_user_id is not None
        }, key=lambda value: value[1].casefold())
        current_sender = self.sender_filter.currentData()
        self.sender_filter.blockSignals(True)
        self.sender_filter.clear()
        self.sender_filter.addItem("Все отправители", None)
        for sender_id, label in senders:
            self.sender_filter.addItem(label, sender_id)
        index = self.sender_filter.findData(current_sender)
        self.sender_filter.setCurrentIndex(max(index, 0))
        self.sender_filter.blockSignals(False)
        self.items = self.controller.list_items(selected, **filters)
        self.table.setRowCount(len(self.items))
        products = {item.id: item for item in self.controller.products()}
        stages = {item.id: item for item in self.controller.active_stages()}
        objects = {item.id: item for item in self.controller.objects()}
        for row_index, item in enumerate(self.items):
            product = products.get(item.product_id)
            values = (
                self.controller.local_observed_at(item).strftime("%d.%m.%Y %H:%M"),
                item.sender_display_snapshot or "Неизвестный отправитель",
                objects.get(item.object_id).name if item.object_id in objects else "Не определен",
                product.name if product else "Не определено",
                stages.get(item.stage_id).name if item.stage_id in stages else "Не определен",
                "-" if item.readiness_percent is None else f"{item.readiness_percent}%",
                str(item.attachment_count), item.match_quality,
                STATUS_LABELS[item.review_status],
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item)
                self.table.setItem(row_index, column, cell)
        pending = sum(row.review_status is ReviewStatus.REQUIRES_REVIEW for row in all_rows)
        changed = sum(row.review_status is ReviewStatus.SOURCE_CHANGED for row in all_rows)
        self.counters.setText(
            f"Требуют проверки: {pending} | Изменены после проверки: {changed}"
        )
        if self.items:
            self.table.selectRow(0)
            self._selection_changed()
        else:
            self._clear_detail()

    def request_refresh(self) -> None:
        if self._thread is not None:
            return
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Обновление...")
        thread = QThread(self)
        worker = _RefreshWorker(self.controller)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._refresh_complete)
        worker.failed.connect(self._refresh_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._refresh_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    @Slot(object)
    def _refresh_complete(self, summary) -> None:
        timestamp = summary.refreshed_at_utc or datetime.now().astimezone()
        self.last_refresh.setText(
            f"Последнее обновление: {timestamp.astimezone().strftime('%d.%m.%Y %H:%M:%S')} | "
            f"Новых сообщений: {summary.imported_messages}; новых фотоотчетов: "
            f"{summary.new_bundles}; требуют проверки: {summary.requires_review}"
        )
        self.reload_directories()
        self.refresh_queue()

    @Slot(str)
    def _refresh_failed(self, message: str) -> None:
        self.status_message.emit(f"Не удалось обновить фотоотчеты: {message}")

    @Slot()
    def _refresh_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Обновить")

    def _selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self.current_item = item
        try:
            self.current_detail = self.controller.detail(item)
        except Exception as exc:
            self.status_message.emit(str(exc))
            return
        self._show_detail()

    def _show_detail(self) -> None:
        detail = self.current_detail
        item = detail.item
        self.source_messages.clear()
        exact_texts = []
        for message in detail.messages:
            label = self.controller.local_datetime(
                message.message_timestamp_utc
            ).strftime("%d.%m.%Y %H:%M")
            widget_item = QListWidgetItem(
                f"{message.bundle_order + 1}. {label} | {message.message_role}"
            )
            widget_item.setData(Qt.ItemDataRole.UserRole, message.id)
            self.source_messages.addItem(widget_item)
            if message.source_text:
                exact_texts.append(message.source_text)
        current_source = "\n\n".join(exact_texts)
        if detail.previous_source_text:
            self.source_text.setPlainText(
                "Было:\n" + detail.previous_source_text
                + "\n\nСтало:\n" + current_source
            )
        else:
            self.source_text.setPlainText(current_source)
        self.photos.clear()
        for index, attachment in enumerate(detail.attachments):
            photo = QListWidgetItem(attachment.original_name or f"Фото {index + 1}")
            photo.setData(Qt.ItemDataRole.UserRole, index)
            try:
                data = self.controller.source_attachment_bytes(item, attachment.id)
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    photo.setIcon(QIcon(pixmap.scaled(
                        132, 96, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )))
            except Exception:
                photo.setText(f"{photo.text()}\nНедоступно")
            self.photos.addItem(photo)
        self._select_combo(self.product_combo, item.product_id)
        self._select_combo(self.stage_combo, item.stage_id)
        self._select_combo(self.reporter_combo, detail.reported_by_employee_id)
        self.readiness.setValue(item.readiness_percent if item.readiness_percent is not None else -1)
        self.observed_at.setDateTime(QDateTime(self.controller.local_observed_at(item)))
        self.description.setPlainText(item.description_text)
        evidence = [entry.explanation for entry in detail.evidence]
        evidence.extend(f"Требует проверки: {issue.message}" for issue in detail.issues)
        self.evidence.setPlainText("\n".join(evidence) or "Автоматических совпадений нет")
        changed = item.review_status is ReviewStatus.SOURCE_CHANGED
        changed_event_id = (
            self.controller.source_changed_event_id(item) if changed else None
        )
        self.keep_button.setVisible(changed_event_id is not None)
        self.confirm_button.setEnabled(item.review_status not in {ReviewStatus.CONFIRMED, ReviewStatus.REJECTED, ReviewStatus.KEPT_EXISTING})
        self.reject_button.setEnabled(self.confirm_button.isEnabled())
        self.open_button.setEnabled(
            item.production_event_id is not None or changed_event_id is not None
        )
        if changed_event_id is not None:
            self.event_type.setCurrentIndex(self.event_type.findData(ProductionEventType.CORRECTION))

    def _confirm(self) -> None:
        if self.current_item is None:
            return
        product_id = self.product_combo.currentData()
        if product_id is None:
            self._warning("Выберите изделие")
            return
        readiness = self.readiness.value() if self.readiness.value() >= 0 else None
        event_type = self.event_type.currentData()
        current = self.controller.current_readiness(product_id)
        if readiness is not None and current is not None and readiness < current:
            if event_type is ProductionEventType.OBSERVATION and not self.change_reason.toPlainText().strip():
                self._warning(
                    f"Новое значение ниже текущего: {current}% -> {readiness}%. "
                    "Выберите переработку/исправление или укажите причину."
                )
                return
        correction_id = (
            self.controller.source_changed_event_id(self.current_item)
            if event_type is ProductionEventType.CORRECTION else None
        )
        try:
            self.controller.confirm(
                self.current_item,
                product_id=product_id,
                stage_id=self.stage_combo.currentData(),
                readiness_percent=readiness,
                description=self.description.toPlainText(),
                observed_at_local=self.observed_at.dateTime().toPython(),
                reported_by_employee_id=self.reporter_combo.currentData(),
                event_type=event_type,
                change_reason=self.change_reason.toPlainText(),
                correction_source_event_id=correction_id,
            )
        except Exception as exc:
            self._warning(str(exc))
            return
        alias_errors = []
        try:
            if self.remember_product.isChecked():
                self.controller.remember_product_alias(
                    self.product_alias.text(), product_id
                )
            if self.remember_stage.isChecked() and self.stage_combo.currentData() is not None:
                self.controller.remember_stage_alias(
                    self.stage_alias.text(), self.stage_combo.currentData()
                )
        except Exception as exc:
            alias_errors.append(str(exc))
        self.status_message.emit(
            "Фотоотчёт подтверждён"
            + (f"; алиас не сохранен: {alias_errors[0]}" if alias_errors else "")
        )
        self.refresh_queue()

    def _reject(self) -> None:
        if self.current_item is None:
            return
        labels = {
            "Не относится к производству": RejectionCode.NOT_PRODUCTION,
            "Ошибочное сообщение": RejectionCode.ERRONEOUS_MESSAGE,
            "Дубликат": RejectionCode.DUPLICATE,
            "Недостаточно данных": RejectionCode.INSUFFICIENT_DATA,
            "Другое": RejectionCode.OTHER,
        }
        label, ok = QInputDialog.getItem(self, "Отклонить фотоотчет", "Причина", list(labels), 0, False)
        if not ok:
            return
        comment, ok = QInputDialog.getText(self, "Комментарий", "Краткий комментарий")
        if not ok:
            return
        try:
            self.controller.reject(self.current_item, labels[label], comment)
            self.status_message.emit("Фотоотчёт отклонён")
            self.refresh_queue()
        except Exception as exc:
            self._warning(str(exc))

    def _keep_existing(self) -> None:
        if self.current_item is None:
            return
        try:
            self.controller.keep_existing(self.current_item)
            self.status_message.emit("Подтвержденное событие оставлено без изменения")
            self.refresh_queue()
        except Exception as exc:
            self._warning(str(exc))

    def _open_photo(self, list_item) -> None:
        if self.current_detail is None:
            return
        ProductionInboxPhotoViewer(
            self.controller, self.current_item, self.current_detail.attachments,
            int(list_item.data(Qt.ItemDataRole.UserRole) or 0), self,
        ).exec()

    def _open_production(self) -> None:
        product_id = self.product_combo.currentData()
        if product_id is not None:
            self.product_open_requested.emit(product_id)

    def _split(self) -> None:
        if self.current_detail is None:
            return
        selected = {item.data(Qt.ItemDataRole.UserRole) for item in self.source_messages.selectedItems()}
        all_ids = tuple(message.id for message in self.current_detail.messages)
        first = tuple(value for value in all_ids if value in selected)
        second = tuple(value for value in all_ids if value not in selected)
        if not first or not second:
            self._warning("Выберите сообщения, которые должны войти в первый новый пакет")
            return
        try:
            self.controller.split(self.current_item, (first, second))
            self.refresh_queue()
        except Exception as exc:
            self._warning(str(exc))

    def _merge(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        selected = tuple(self.table.item(row.row(), 0).data(Qt.ItemDataRole.UserRole) for row in rows)
        if len(selected) < 2:
            self._warning("Выберите не менее двух фотоотчетов")
            return
        details = tuple(self.controller.detail(item) for item in selected)
        message_ids = tuple(message.id for detail in details for message in detail.messages)
        mixed = len({item.sender_max_user_id for item in selected}) > 1
        if mixed and QMessageBox.question(
            self, "Разные отправители",
            "Объединить сообщения разных отправителей в один фотоотчет?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.merge(selected, message_ids, allow_mixed_senders=mixed)
            self.refresh_queue()
        except Exception as exc:
            self._warning(str(exc))

    def _clear_detail(self) -> None:
        self.current_item = None
        self.current_detail = None
        self.source_messages.clear()
        self.source_text.clear()
        self.photos.clear()
        self.description.clear()
        self.evidence.clear()

    @staticmethod
    def _select_combo(combo, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _warning(self, message: str) -> None:
        QMessageBox.warning(self, "Фотоотчёты", message)


def _product_label(product) -> str:
    parts = [product.object_name, product.name]
    if product.serial_number:
        parts.append(f"зав. № {product.serial_number}")
    if product.code:
        parts.append(product.code)
    return " — ".join(part for part in parts if part)
