"""Large source-photo viewer backed by the P11 controller."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class ProductionInboxPhotoViewer(QDialog):
    def __init__(self, controller, item, attachments, start_index=0, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.item = item
        self.attachments = tuple(attachments)
        self.index = max(0, min(start_index, len(self.attachments) - 1)) if self.attachments else 0
        self.setWindowTitle("Фотография из MAX")
        self.resize(980, 720)
        self.image = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(480, 360)
        self.image.setStyleSheet("background: #202327; color: #f4f5f6;")
        self.previous = QPushButton("Назад")
        self.next = QPushButton("Вперед")
        self.counter = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        controls = QHBoxLayout()
        controls.addWidget(self.previous)
        controls.addWidget(self.counter, 1)
        controls.addWidget(self.next)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image, 1)
        layout.addLayout(controls)
        self.previous.clicked.connect(lambda: self._move(-1))
        self.next.clicked.connect(lambda: self._move(1))
        self._show_current()

    def _move(self, step: int) -> None:
        if self.attachments:
            self.index = (self.index + step) % len(self.attachments)
        self._show_current()

    def _show_current(self) -> None:
        if not self.attachments:
            self.image.setText("Фотографии отсутствуют")
            self.counter.setText("")
            return
        attachment = self.attachments[self.index]
        try:
            content = self.controller.source_attachment_bytes(self.item, attachment.id)
            pixmap = QPixmap()
            if not pixmap.loadFromData(content):
                raise ValueError("Формат изображения не поддерживается")
            self.image.setPixmap(pixmap.scaled(
                self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        except Exception:
            self.image.setPixmap(QPixmap())
            self.image.setText("Оригинал фотографии недоступен")
        self.counter.setText(f"{self.index + 1} из {len(self.attachments)}")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._show_current()
