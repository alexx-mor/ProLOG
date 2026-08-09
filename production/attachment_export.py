"""Application use-cases for exporting attachment originals."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from production.attachment_service import AttachmentService


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/tiff": ".tif",
}


@dataclass(frozen=True, slots=True)
class AttachmentExportRequest:
    attachment_id: int
    observed_at_utc: datetime
    product_label: str
    stage_name: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class AttachmentExportFailure:
    attachment_id: int
    original_name: str
    message: str


@dataclass(frozen=True, slots=True)
class AttachmentExportReport:
    exported_paths: tuple[Path, ...]
    failures: tuple[AttachmentExportFailure, ...]

    @property
    def is_successful(self) -> bool:
        return not self.failures


class AttachmentExportService:
    """Copy verified originals outside ProLOG without exposing storage layout."""

    def __init__(self, attachments: AttachmentService) -> None:
        self.attachments = attachments

    def export_one(self, attachment_id: int, destination: Path) -> Path:
        attachment = self.attachments.get_attachment(attachment_id)
        target = Path(destination)
        if not target.suffix:
            target = target.with_suffix(self._extension(attachment.original_name, attachment.mime_type))
        target = target.with_name(sanitize_filename(target.name, fallback="Фотография"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target = _available_path(target)
        content = self.attachments.read_bytes(attachment_id)
        _write_exclusive(target, content)
        return target

    def export_batch(
        self,
        requests: list[AttachmentExportRequest],
        destination_root: Path,
        *,
        product_subdirectory: bool = True,
    ) -> AttachmentExportReport:
        root = Path(destination_root)
        root.mkdir(parents=True, exist_ok=True)
        exported: list[Path] = []
        failures: list[AttachmentExportFailure] = []
        for request in requests:
            original_name = f"Attachment {request.attachment_id}"
            try:
                attachment = self.attachments.get_attachment(request.attachment_id)
                original_name = attachment.original_name
                target_dir = root
                if product_subdirectory:
                    target_dir /= sanitize_filename(
                        request.product_label,
                        fallback="Изделие",
                    )
                target_dir /= request.observed_at_utc.date().isoformat()
                target_dir.mkdir(parents=True, exist_ok=True)
                extension = self._extension(
                    attachment.original_name,
                    attachment.mime_type,
                )
                filename = sanitize_filename(
                    "_".join(
                        (
                            request.observed_at_utc.date().isoformat(),
                            request.product_label,
                            request.stage_name or "Этап не указан",
                            f"{request.sort_order + 1:02d}",
                        )
                    ),
                    fallback=f"Фотография_{request.sort_order + 1:02d}",
                )
                exported.append(
                    self.export_one(
                        request.attachment_id,
                        target_dir / f"{filename}{extension}",
                    )
                )
            except Exception as exc:
                failures.append(
                    AttachmentExportFailure(
                        request.attachment_id,
                        original_name,
                        str(exc),
                    )
                )
        return AttachmentExportReport(tuple(exported), tuple(failures))

    @staticmethod
    def _extension(original_name: str, mime_type: str) -> str:
        suffix = Path(original_name).suffix.lower()
        if suffix and re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            return suffix
        return _MIME_EXTENSIONS.get(mime_type.casefold(), ".bin")


def sanitize_filename(value: str, *, fallback: str) -> str:
    """Return a readable Windows-safe filename component."""

    cleaned = _INVALID_FILENAME.sub("_", value).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:120].rstrip(". ")
    if not cleaned:
        cleaned = fallback
    stem = Path(cleaned).stem.upper()
    if stem in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _available_path(target: Path) -> Path:
    if not target.exists():
        return target
    for number in range(2, 100_000):
        candidate = target.with_name(f"{target.stem}_{number}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("Не удалось подобрать свободное имя файла")


def _write_exclusive(target: Path, content: bytes) -> None:
    try:
        with target.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
