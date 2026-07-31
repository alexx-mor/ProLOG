"""Business rules for matching and importing WorkBot reports."""

from __future__ import annotations

from pathlib import Path

from hours import normalize_hours
from integrations.workbot.models import (
    STATUS_INVALID_HOURS,
    STATUS_NEEDS_EMPLOYEE,
    STATUS_NEEDS_LOCATION,
    STATUS_NEEDS_OBJECT,
    STATUS_NEEDS_WORK_TYPE,
    STATUS_READY,
    STATUS_SOURCE_ERROR,
    WorkBotCandidate,
    WorkBotInboxRow,
    WorkBotSyncResult,
)
from integrations.workbot.repository import WorkBotRepository, normalize_alias
from integrations.workbot.source import WorkBotSource
from models import DirectoryItem, Employee, WorkLogEntry
from services import DirectoryService, EmployeeService, NON_WORK_LOCATIONS, WorkLogService


class WorkBotIntegrationService:
    def __init__(
        self,
        repository: WorkBotRepository,
        employees: EmployeeService,
        directories: DirectoryService,
        worklogs: WorkLogService,
    ) -> None:
        self.repository = repository
        self.employees = employees
        self.directories = directories
        self.worklogs = worklogs
        self.source = WorkBotSource()

    def sync(self, source_path: Path) -> WorkBotSyncResult:
        candidates = self.source.read_candidates(source_path)
        employees = self.employees.list()
        objects = self.directories.list_all("objects")
        locations = self.directories.list_all("locations")
        work_types = self.directories.list_all("work_types")
        bindings = self.repository.employee_bindings()
        aliases = {
            alias_type: self.repository.alias_targets(alias_type)
            for alias_type in ("employee", "object", "location")
        }
        for candidate in candidates:
            self._resolve(candidate, employees, objects, locations, work_types, bindings, aliases)
        return self.repository.sync(candidates)

    def list_rows(self, status: str = "") -> list[WorkBotInboxRow]:
        return self.repository.list_rows(status)

    def import_row(
        self,
        row_id: int,
        *,
        employee_id: int,
        work_date,
        location_id: int | None,
        object_id: int | None,
        work_type_id: int | None,
        description: str,
        hours: float,
        remember: bool,
        reviewer: str = "",
    ) -> int:
        row = self.repository.get(row_id)
        if row is None:
            raise ValueError("Входящий отчет не найден")
        location = _by_id(self.directories.list_all("locations"), location_id)
        entry = WorkLogEntry(
            employee_id=employee_id,
            work_date=work_date,
            location_id=location_id,
            object_id=object_id,
            work_type_id=work_type_id,
            description=description.strip(),
            hours=normalize_hours(hours),
            comment=f"Импортировано из WorkBot, сообщение {row.max_message_id}",
            location_name=location.name if location else "",
        )
        self.worklogs.validate(entry)
        return self.repository.import_entry(
            row_id,
            entry,
            remember_aliases=remember,
            remember_sender=remember,
            reviewer=reviewer,
        )

    def reject(self, row_id: int, reason: str = "", reviewer: str = "") -> None:
        self.repository.reject(row_id, reason or "Отклонено пользователем", reviewer)

    def _resolve(
        self,
        candidate: WorkBotCandidate,
        employees: list[Employee],
        objects: list[DirectoryItem],
        locations: list[DirectoryItem],
        work_types: list[DirectoryItem],
        bindings: dict[int, int],
        aliases: dict[str, dict[str, int]],
    ) -> None:
        if candidate.source_kind == "unparsed":
            candidate.status = STATUS_SOURCE_ERROR
            candidate.error_message = candidate.error_message or "WorkBot не смог распознать сообщение"
            return
        candidate.employee_id = self._employee_id(candidate, employees, bindings, aliases["employee"])
        candidate.object_id = self._directory_id(candidate.object_text, objects, aliases["object"])
        candidate.location_id = self._directory_id(candidate.location_text, locations, aliases["location"])
        candidate.work_type_id = _exact_id(candidate.work_types, work_types)
        if candidate.employee_id is None:
            candidate.status = STATUS_NEEDS_EMPLOYEE
            candidate.error_message = "Сопоставьте сотрудника"
            return
        if candidate.location_id is None:
            candidate.status = STATUS_NEEDS_LOCATION
            candidate.error_message = "Сопоставьте местонахождение"
            return
        is_non_work = candidate.location_text in NON_WORK_LOCATIONS
        if candidate.object_text and candidate.object_id is None and not is_non_work:
            candidate.status = STATUS_NEEDS_OBJECT
            candidate.error_message = "Сопоставьте объект или добавьте подтвержденный алиас"
            return
        if candidate.work_types and candidate.work_type_id is None and not is_non_work:
            candidate.status = STATUS_NEEDS_WORK_TYPE
            candidate.error_message = "Сопоставьте вид работ"
            return
        if candidate.hours < 0 or candidate.hours > 24 or (candidate.hours == 0 and not is_non_work):
            candidate.status = STATUS_INVALID_HOURS
            candidate.error_message = "Проверьте количество часов"
            return
        candidate.status = STATUS_READY
        candidate.error_message = ""

    def _employee_id(
        self,
        candidate: WorkBotCandidate,
        employees: list[Employee],
        bindings: dict[int, int],
        aliases: dict[str, int],
    ) -> int | None:
        if candidate.source_kind == "strict":
            bound = bindings.get(candidate.sender_id)
            if bound is not None:
                return bound
        alias = aliases.get(normalize_alias(candidate.employee_text))
        return alias if alias is not None else _exact_id(candidate.employee_text, employees, "full_name")

    def _directory_id(
        self,
        value: str,
        items: list[DirectoryItem],
        aliases: dict[str, int],
    ) -> int | None:
        alias = aliases.get(normalize_alias(value))
        return alias if alias is not None else _exact_id(value, items)


def _exact_id(value: str, items, name_attr: str = "name") -> int | None:
    key = normalize_alias(value)
    if not key:
        return None
    for item in items:
        if normalize_alias(str(getattr(item, name_attr))) == key:
            return item.id
    return None


def _by_id(items: list[DirectoryItem], item_id: int | None) -> DirectoryItem | None:
    return next((item for item in items if item.id == item_id), None)
