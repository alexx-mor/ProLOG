"""Модели данных WorkBot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class ParsedReport:
    work_date: date
    work_types: str
    hours: float
    object_name: str
    location: str


@dataclass(frozen=True, slots=True)
class ParsedEmployeeReport:
    employee_name: str
    report: ParsedReport
    confidence: float = 0.7
    source_fragment: str = ""


@dataclass(frozen=True, slots=True)
class StoredReport:
    id: int
    source_message_id: str
    sender_id: int
    employee_name: str
    work_date: date
    work_types: str
    hours: float
    object_name: str
    location: str
    created_at: datetime
