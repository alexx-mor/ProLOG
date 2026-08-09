"""In-memory production photo viewer."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from production.projection_models import TimelineAttachment
from ui.production_controller import ProductionUiController


@dataclass(frozen=True, slots=True)
class ProductionPhoto:
    timeline_attachment: TimelineAttachment
    event_label: str


class ProductionPhotoViewer(QDialog):
    """Read verified attachment bytes through the application service boundary."""

    def __init__(
        self,
        controller: ProductionUiController,
        photos: list[ProductionPhoto],
        parent: QWidget | None = None,
        *,
        initial_index: int = 0,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.photos = photos
        self.index = min(max(initial_index, 0), max(len(photos) - 1, 0))
        self.setWindowTitle("Фотографии изделия")
        self.resize(1000, 760)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setStyleSheet(
            "QLabel { background: #202428; color: #ffffff; border: 1px solid #4b5157; }"
        )
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.previous_button = QPushButton("Назад")
        self.next_button = QPushButton("Далее")
        self.save_button = QPushButton("Сохранить оригинал...")
        self.close_button = QPushButton("Закрыть")

        actions = QHBoxLayout()
        actions.addWidget(self.previous_button)
        actions.addWidget(self.next_button)
        actions.addWidget(self.save_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.image_label, 1)
        layout.addWidget(self.caption)
        layout.addLayout(actions)

        self.previous_button.clicked.connect(lambda: self._move(-1))
        self.next_button.clicked.connect(lambda: self._move(1))
        self.save_button.clicked.connect(self._save_current)
        self.close_button.clicked.connect(self.accept)
        self._show_current()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show_current()

    def _move(self, delta: int) -> None:
        if not self.photos:
            return
        self.index = (self.index + delta) % len(self.photos)
        self._show_current()

    def _show_current(self) -> None:
        self.previous_button.setEnabled(len(self.photos) > 1)
        self.next_button.setEnabled(len(self.photos) > 1)
        self.save_button.setEnabled(bool(self.photos))
        if not self.photos:
            self.image_label.setText("Фотографии отсутствуют")
            self.caption.clear()
            return
        photo = self.photos[self.index]
        attachment = photo.timeline_attachment.attachment
        self.caption.setText(
            f"{self.index + 1} из {len(self.photos)} · {attachment.original_name} · "
            f"{photo.event_label}"
        )
        try:
            content = self.controller.attachment_bytes(attachment.id or 0)
            pixmap = QPixmap()
            if not pixmap.loadFromData(content):
                raise ValueError("Формат изображения не поддерживается")
            self.image_label.setPixmap(
                pixmap.scaled(
                    self.image_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except Exception as exc:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"Файл недоступен\n{exc}")

    def _save_current(self) -> None:
        if not self.photos:
            return
        attachment = self.photos[self.index].timeline_attachment.attachment
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить оригинал",
            attachment.original_name,
            "Все файлы (*.*)",
        )
        if not destination:
            return
        try:
            exported = self.controller.export_attachment(
                attachment.id or 0,
                destination,
            )
            QMessageBox.information(
                self,
                "Экспорт фотографии",
                f"Оригинал сохранен:\n{exported}",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Экспорт фотографии", str(exc))
