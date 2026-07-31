"""Domain models for legacy Excel report import."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

from models import Employee


class IssueSeverity(StrEnum):
    ERROR = "Ошибка"
    WARNING = "Предупреждение"
    INFO = "Информация"


class ImportRowStatus(StrEnum):
    READY = "Готово к импорту"
    SKIPPED = "Пропущено"
    ERROR = "Ошибка"
    IMPORTED = "Импортировано"


@dataclass(slots=True)
class LegacyReportRow:
    sheet_name: str
    row_number: int
    work_date: date
    description: str
    position_text: str
    employee_text: str
    hours: float
    object_text: str
    legacy_location_text: str


@dataclass(slots=True)
class LegacyParseResult:
    source_file: Path
    rows: list[LegacyReportRow]
    issues: list["ImportIssue"]


@dataclass(slots=True)
class ImportIssue:
    severity: IssueSeverity
    code: str
    message: str
    sheet_name: str = ""
    row_number: int | None = None
    work_date: date | None = None
    employee_text: str = ""


@dataclass(slots=True)
class ResolvedLegacyRow:
    source: LegacyReportRow
    status: ImportRowStatus
    employee: Employee | None = None
    current_location: str = ""
    object_name: str = ""
    work_type: str = ""
    description: str = ""
    hours: float = 0.0
    comment: str = ""
    skip_reason: str = ""
    issues: list[ImportIssue] = field(default_factory=list)

    @property
    def can_import(self) -> bool:
        return self.status == ImportRowStatus.READY and self.employee is not None


@dataclass(slots=True)
class LegacyImportPreview:
    source_file: Path
    file_hash: str
    rows: list[ResolvedLegacyRow]
    issues: list[ImportIssue]
    duplicate_batch_id: int | None = None

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def importable_count(self) -> int:
        return sum(row.can_import for row in self.rows)

    @property
    def skipped_count(self) -> int:
        return sum(row.status == ImportRowStatus.SKIPPED for row in self.rows)

    @property
    def error_count(self) -> int:
        return sum(row.status == ImportRowStatus.ERROR for row in self.rows)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == IssueSeverity.WARNING for issue in self.issues)

    @property
    def has_blocking_errors(self) -> bool:
        return self.duplicate_batch_id is not None or any(issue.severity == IssueSeverity.ERROR for issue in self.issues)


@dataclass(slots=True)
class ImportCommitResult:
    batch_id: int
    imported_count: int
    skipped_count: int
    error_count: int
