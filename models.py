"""Domain models for ProLOG.

The work log entry is the central business entity. Reports and assignments are
derived from it instead of being stored as independent primary concepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class EmployeeStatus(StrEnum):
    ACTIVE = "Активен"
    INACTIVE = "Неактивен"


class ObjectStatus(StrEnum):
    PLANNED = "Планируется"
    IN_PROGRESS = "В работе"
    PAUSED = "Приостановлен"
    DELIVERED = "Сдан"
    CLOSED = "Закрыт"


class ProductStatus(StrEnum):
    PLANNED = "Планируется"
    IN_PROGRESS = "В изготовлении"
    PAUSED = "Приостановлено"
    READY = "Готово"
    RELEASED = "Выпущено"


class WorkDayType(StrEnum):
    WORKDAY = "Рабочий день"
    SHORTENED_WORKDAY = "Сокращенный рабочий день"
    DAY_OFF = "Выходной"
    HOLIDAY = "Праздничный день"
    WORKING_SATURDAY = "Рабочая суббота"
    WORKING_HOLIDAY = "Рабочий воскр./праздник"


@dataclass(slots=True)
class Employee:
    full_name: str
    position: str = ""
    category: str = ""
    status: str = EmployeeStatus.ACTIVE.value
    id: int | None = None


@dataclass(slots=True)
class DirectoryItem:
    name: str
    id: int | None = None
    is_active: bool = True
    category: str = ""
    student_allowed: bool = False
    salary: str = ""
    salary_type: str = "hourly"
    group: str = ""
    project_number: str = ""
    contract_number: str = ""
    customer: str = ""
    contract_type: str = ""
    object_type: str = ""
    object_subtype: str = ""
    signed_date: str = ""
    due_date: str = ""
    object_status: str = ObjectStatus.IN_PROGRESS.value


@dataclass(slots=True)
class PayRate:
    position_id: int
    position_name: str
    category: str
    salary: str = ""
    salary_type: str = "hourly"
    far_trip_coeff: str = "1"
    near_trip_coeff: str = "1"
    holiday_coeff: str = "1"
    saturday_coeff: str = "1"
    id: int | None = None


@dataclass(slots=True)
class ProductItem:
    object_id: int
    name: str
    serial_number: str = ""
    code: str = ""
    product_status: str = ProductStatus.IN_PROGRESS.value
    readiness_percent: int = 0
    start_date: str = ""
    release_date: str = ""
    object_name: str = ""
    is_active: bool = True
    id: int | None = None


@dataclass(slots=True)
class WorkCalendarDay:
    work_date: date
    day_type: str = WorkDayType.WORKDAY.value
    note: str = ""
    id: int | None = None


@dataclass(slots=True)
class WorkLogEntry:
    employee_id: int
    work_date: date
    location_id: int | None
    object_id: int | None
    work_type_id: int | None
    description: str
    hours: int
    comment: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None
    employee_name: str = ""
    location_name: str = ""
    object_name: str = ""
    work_type_name: str = ""


@dataclass(slots=True)
class AppSettings:
    check_updates_on_startup: bool = True
    leader_full_name: str = ""
    department_name: str = ""
    organization_name: str = ""
    window_width: int = 1320
    window_height: int = 820
    splitter_sizes: list[int] = field(default_factory=lambda: [430, 890])
    employee_column_widths: list[int] = field(default_factory=list)
    worklog_column_widths: list[int] = field(default_factory=list)
    initial_setup_done: bool = False
    monthly_hours_norm: int = 168
