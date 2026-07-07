"""Business logic for importing legacy Excel reports into WorkLogEntry."""

from __future__ import annotations

import logging
from pathlib import Path

from database import Database
from legacy_import.excel_secretary_reports import parse_secretary_report
from legacy_import.models import (
    ImportCommitResult,
    ImportIssue,
    ImportRowStatus,
    IssueSeverity,
    LegacyImportPreview,
    LegacyReportRow,
    ResolvedLegacyRow,
)
from legacy_import.normalizers import (
    WORK_TYPE_DEFAULT,
    classify_non_work,
    infer_location,
    is_weekend,
    make_import_comment,
    normalize_key,
    normalize_position,
    person_match_keys,
)
from legacy_import.repository import LegacyImportAuditRepository
from models import Employee, WorkLogEntry
from services import DirectoryService, EmployeeService, WorkLogService

logger = logging.getLogger(__name__)

MAX_IMPORT_HOURS = 24


class LegacyExcelImportService:
    def __init__(
        self,
        database: Database,
        employees: EmployeeService,
        directories: DirectoryService,
        worklogs: WorkLogService,
    ) -> None:
        self.database = database
        self.employees = employees
        self.directories = directories
        self.worklogs = worklogs
        self.audit = LegacyImportAuditRepository(database)

    def analyze(self, path: Path) -> LegacyImportPreview:
        source_file = Path(path)
        if not source_file.exists():
            raise ValueError("Файл старого отчета не найден")
        file_hash = self.audit.file_hash(source_file)
        duplicate_batch_id = self.audit.find_completed_batch(file_hash)
        parse_result = parse_secretary_report(source_file)
        employee_lookup, ambiguous_keys = self._employee_lookup()
        object_names = {normalize_key(item.name) for item in self.directories.list_all("objects")}
        issues = list(parse_result.issues)
        rows = [
            self._resolve_row(row, employee_lookup, ambiguous_keys, object_names)
            for row in parse_result.rows
        ]
        for row in rows:
            issues.extend(row.issues)
        if duplicate_batch_id is not None:
            issues.insert(
                0,
                ImportIssue(
                    severity=IssueSeverity.ERROR,
                    code="DUPLICATE_FILE",
                    message=f"Этот файл уже был импортирован в партии №{duplicate_batch_id}",
                ),
            )
        if not rows:
            issues.append(
                ImportIssue(
                    severity=IssueSeverity.ERROR,
                    code="NO_ROWS",
                    message="В файле не найдены строки отчетов с датами",
                )
            )
        return LegacyImportPreview(
            source_file=source_file,
            file_hash=file_hash,
            rows=rows,
            issues=issues,
            duplicate_batch_id=duplicate_batch_id,
        )

    def commit(self, preview: LegacyImportPreview) -> ImportCommitResult:
        if preview.duplicate_batch_id is not None:
            raise ValueError("Файл уже импортировался ранее")
        if preview.has_blocking_errors:
            raise ValueError("Сначала исправьте ошибки, показанные в предпросмотре импорта")

        batch_id = self.audit.create_batch(preview)
        imported_count = 0
        skipped_count = 0
        try:
            for row in preview.rows:
                if row.status == ImportRowStatus.SKIPPED:
                    skipped_count += 1
                    self.audit.add_row(batch_id, row, row.status.value, _row_message(row))
                    continue
                if not row.can_import:
                    self.audit.add_row(batch_id, row, row.status.value, _row_message(row))
                    continue
                entry_id = self._save_worklog(row)
                imported_count += 1
                self.audit.add_row(batch_id, row, ImportRowStatus.IMPORTED.value, _row_message(row), entry_id)
            self.audit.complete_batch(
                batch_id,
                imported_count=imported_count,
                skipped_count=skipped_count,
                error_count=preview.error_count,
                message="Импорт завершен",
            )
        except Exception as exc:
            logger.exception("Legacy report import failed")
            self.audit.fail_batch(batch_id, str(exc))
            raise
        return ImportCommitResult(
            batch_id=batch_id,
            imported_count=imported_count,
            skipped_count=skipped_count,
            error_count=preview.error_count,
        )

    def _resolve_row(
        self,
        row: LegacyReportRow,
        employee_lookup: dict[str, Employee | None],
        ambiguous_keys: set[str],
        object_names: set[str],
    ) -> ResolvedLegacyRow:
        issues: list[ImportIssue] = []
        employee, ambiguous = self._find_employee(row.employee_text, employee_lookup, ambiguous_keys)
        if ambiguous:
            issues.append(_issue(row, IssueSeverity.ERROR, "EMPLOYEE_AMBIGUOUS", "Сотрудник найден неоднозначно"))
        elif employee is None:
            issues.append(_issue(row, IssueSeverity.ERROR, "EMPLOYEE_NOT_FOUND", "Сотрудник не найден в ProLOG"))

        combined_absence_text = f"{row.description} {row.legacy_location_text}"
        if is_weekend(combined_absence_text):
            return ResolvedLegacyRow(
                source=row,
                status=ImportRowStatus.SKIPPED,
                employee=employee,
                skip_reason="Выходной: строка не переносится в журнал работ",
                issues=[*issues, _issue(row, IssueSeverity.INFO, "WEEKEND_SKIPPED", "Выходной пропущен")],
            )

        non_work_location = classify_non_work(combined_absence_text)
        if row.hours > MAX_IMPORT_HOURS:
            issues.append(
                _issue(
                    row,
                    IssueSeverity.ERROR,
                    "HOURS_TOO_HIGH",
                    "В старом отчете указано больше 24 часов за день",
                )
            )
        if row.hours > 0 and not row.description:
            issues.append(
                _issue(
                    row,
                    IssueSeverity.ERROR,
                    "EMPTY_DESCRIPTION_WITH_HOURS",
                    "Есть часы, но не заполнено описание работ",
                )
            )

        if any(issue.severity == IssueSeverity.ERROR for issue in issues):
            return ResolvedLegacyRow(source=row, status=ImportRowStatus.ERROR, employee=employee, issues=issues)

        if non_work_location:
            return ResolvedLegacyRow(
                source=row,
                status=ImportRowStatus.READY,
                employee=employee,
                current_location=non_work_location,
                description=row.description or non_work_location,
                hours=0,
                comment=make_import_comment(row.sheet_name, row.row_number, row.position_text, row.legacy_location_text),
                issues=issues,
            )

        if row.hours == 0:
            code = "REPORT_MISSING" if not row.description else "ZERO_HOURS_SKIPPED"
            message = "Отчет отсутствует или строка пустая" if not row.description else "Строка с нулевыми часами пропущена"
            severity = IssueSeverity.INFO if not row.description else IssueSeverity.WARNING
            issues.append(_issue(row, severity, code, message))
            return ResolvedLegacyRow(
                source=row,
                status=ImportRowStatus.SKIPPED,
                employee=employee,
                skip_reason=message,
                issues=issues,
            )

        if not row.object_text:
            issues.append(
                _issue(
                    row,
                    IssueSeverity.WARNING,
                    "EMPTY_OBJECT",
                    "Есть часы, но не указан объект; запись будет импортирована без объекта",
                )
            )
        elif normalize_key(row.object_text) not in object_names:
            issues.append(
                _issue(
                    row,
                    IssueSeverity.INFO,
                    "NEW_OBJECT",
                    "Объект будет добавлен в справочник при импорте",
                )
            )
        self._add_position_warning(row, employee, issues)

        return ResolvedLegacyRow(
            source=row,
            status=ImportRowStatus.READY,
            employee=employee,
            current_location=infer_location(row.legacy_location_text, row.object_text),
            object_name=row.object_text,
            work_type=WORK_TYPE_DEFAULT,
            description=row.description,
            hours=row.hours,
            comment=make_import_comment(row.sheet_name, row.row_number, row.position_text, row.legacy_location_text),
            issues=issues,
        )

    def _save_worklog(self, row: ResolvedLegacyRow) -> int:
        if row.employee is None or row.employee.id is None:
            raise ValueError("Нельзя импортировать строку без сотрудника")
        location_id = self.directories.ensure("locations", row.current_location)
        object_id = self.directories.ensure("objects", row.object_name)
        work_type_id = self.directories.ensure("work_types", row.work_type)
        entry = WorkLogEntry(
            employee_id=row.employee.id,
            work_date=row.source.work_date,
            location_id=location_id,
            object_id=object_id,
            work_type_id=work_type_id,
            description=row.description,
            hours=row.hours,
            comment=row.comment,
            location_name=row.current_location,
            object_name=row.object_name,
            work_type_name=row.work_type,
        )
        return self.worklogs.save(entry)

    def _employee_lookup(self) -> tuple[dict[str, Employee | None], set[str]]:
        lookup: dict[str, Employee | None] = {}
        ambiguous: set[str] = set()
        for employee in self.employees.list():
            for key in person_match_keys(employee.full_name):
                existing = lookup.get(key)
                if existing is not None and existing.id != employee.id:
                    lookup[key] = None
                    ambiguous.add(key)
                elif key not in lookup:
                    lookup[key] = employee
        return lookup, ambiguous

    def _find_employee(
        self,
        value: str,
        employee_lookup: dict[str, Employee | None],
        ambiguous_keys: set[str],
    ) -> tuple[Employee | None, bool]:
        for key in person_match_keys(value):
            if key in ambiguous_keys:
                return None, True
            employee = employee_lookup.get(key)
            if employee is not None:
                return employee, False
        return None, False

    def _add_position_warning(
        self,
        row: LegacyReportRow,
        employee: Employee | None,
        issues: list[ImportIssue],
    ) -> None:
        if employee is None or not row.position_text:
            return
        legacy_position = normalize_position(row.position_text)
        if legacy_position and normalize_key(legacy_position) != normalize_key(employee.position):
            issues.append(
                _issue(
                    row,
                    IssueSeverity.WARNING,
                    "POSITION_MISMATCH",
                    f"Должность в Excel: {row.position_text}; в ProLOG: {employee.position}",
                )
            )


def _issue(row: LegacyReportRow, severity: IssueSeverity, code: str, message: str) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        sheet_name=row.sheet_name,
        row_number=row.row_number,
        work_date=row.work_date,
        employee_text=row.employee_text,
    )


def _row_message(row: ResolvedLegacyRow) -> str:
    messages = [issue.message for issue in row.issues]
    if row.skip_reason:
        messages.insert(0, row.skip_reason)
    return "; ".join(dict.fromkeys(message for message in messages if message))
