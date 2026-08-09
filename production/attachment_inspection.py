"""Content-based attachment inspection without modifying original bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image


@dataclass(frozen=True, slots=True)
class AttachmentInspection:
    mime_type: str
    width: int | None
    height: int | None
    captured_at_utc: datetime | None


def inspect_attachment(content: bytes) -> AttachmentInspection:
    """Inspect supported images and degrade safely for unknown file types."""

    try:
        with Image.open(BytesIO(content)) as image:
            image_format = str(image.format or "").upper()
            mime_type = Image.MIME.get(image_format) or _mime_from_signature(content)
            width, height = image.size
            captured_at_utc = _captured_at_utc(image)
    except Exception:
        return AttachmentInspection(_mime_from_signature(content), None, None, None)
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
    except Exception:
        return AttachmentInspection(mime_type, None, None, None)
    return AttachmentInspection(mime_type, int(width), int(height), captured_at_utc)


def _captured_at_utc(image: Image.Image) -> datetime | None:
    try:
        exif = image.getexif()
        captured = _text_value(exif.get(36867))
        offset = _text_value(exif.get(36881))
        if not captured or not offset:
            return None
        local = datetime.strptime(captured, "%Y:%m:%d %H:%M:%S")
        normalized_offset = offset.replace(":", "")
        zone = datetime.strptime(normalized_offset, "%z").tzinfo
        if zone is None:
            return None
        return local.replace(tzinfo=zone).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _text_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="ignore").strip("\x00 ")
    return str(value).strip() if value is not None else ""


def _mime_from_signature(content: bytes) -> str:
    signatures = (
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"BM", "image/bmp"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"%PDF-", "application/pdf"),
    )
    for signature, mime_type in signatures:
        if content.startswith(signature):
            return mime_type
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"
