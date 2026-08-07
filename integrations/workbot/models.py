"""Data transfer models for the WorkBot integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


STATUS_NEW = "new"
STATUS_READY = "ready"
STATUS_NEEDS_EMPLOYEE = "needs_employee"
STATUS_NEEDS_LOCATION = "needs_location"
STATUS_NEEDS_OBJECT = "needs_object"
STATUS_NEEDS_WORK_TYPE = "needs_work_type"
STATUS_INVALID_HOURS = "invalid_hours"
STATUS_SOURCE_ERROR = "source_error"
STATUS_PRODUCT_CONFLICT = "product_conflict"
STATUS_CHANGED = "changed_after_import"
STATUS_IMPORTED = "imported"
STATUS_REJECTED = "rejected"

STATUS_LABELS = {
    STATUS_NEW: "Новый",
    STATUS_READY: "Готов к импорту",
    STATUS_NEEDS_EMPLOYEE: "Не найден сотрудник",
    STATUS_NEEDS_LOCATION: "Не найдено местонахождение",
    STATUS_NEEDS_OBJECT: "Не найден объект",
    STATUS_NEEDS_WORK_TYPE: "Не найден вид работ",
    STATUS_INVALID_HOURS: "Некорректные часы",
    STATUS_SOURCE_ERROR: "WorkBot не распознал отчет",
    STATUS_PRODUCT_CONFLICT: "Изделие относится к другому объекту",
    STATUS_CHANGED: "Сообщение изменено",
    STATUS_IMPORTED: "Импортирован",
    STATUS_REJECTED: "Отклонен",
}


@dataclass(slots=True)
class WorkBotCandidate:
    max_message_id: str
    source_index: int
    source_kind: str
    sender_id: int
    chat_id: int | None
    received_at: str
    raw_text: str
    employee_text: str
    work_date: date
    work_types: str
    hours: float
    object_text: str
    location_text: str
    confidence: float
    source_fragment: str
    product_text: str = ""
    sender_profile_names: tuple[str, ...] = ()
    content_hash: str = ""
    employee_id: int | None = None
    object_id: int | None = None
    location_id: int | None = None
    work_type_id: int | None = None
    product_id: int | None = None
    status: str = STATUS_NEW
    error_message: str = ""


@dataclass(slots=True)
class WorkBotInboxRow:
    id: int
    max_message_id: str
    revision: int
    source_index: int
    source_kind: str
    sender_id: int
    chat_id: int | None
    received_at: str
    raw_text: str
    source_fragment: str
    employee_text: str
    work_date: date
    work_types: str
    hours: float
    object_text: str
    location_text: str
    product_text: str
    confidence: float
    employee_id: int | None
    object_id: int | None
    location_id: int | None
    work_type_id: int | None
    product_id: int | None
    status: str
    error_message: str
    worklog_entry_id: int | None


@dataclass(slots=True)
class WorkBotSyncResult:
    source_rows: int
    added_rows: int
    unchanged_messages: int
    revised_messages: int


@dataclass(frozen=True, slots=True)
class WorkBotInboxStats:
    source_messages: int
    total_rows: int
    imported_rows: int
    error_rows: int


@dataclass(slots=True)
class WorkBotSourceUser:
    max_user_id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    employee_text: str = ""

    @property
    def profile_name(self) -> str:
        full_name = " ".join(part for part in (self.last_name, self.first_name) if part).strip()
        username = f"@{self.username}" if self.username else ""
        return " ".join(part for part in (full_name, username) if part) or f"MAX {self.max_user_id}"


@dataclass(slots=True)
class WorkBotUserLink:
    max_user_id: int
    profile_name: str
    employee_text: str = ""
    employee_id: int | None = None
    employee_name: str = ""
    binding_saved: bool = False
    match_source: str = "none"
    match_message: str = ""
