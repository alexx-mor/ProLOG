"""Business rules for matching and importing WorkBot reports."""

from __future__ import annotations

from pathlib import Path

from hours import normalize_hours
from integrations.workbot.matcher import detect_product
from integrations.workbot.models import (
    STATUS_INVALID_HOURS,
    STATUS_NEEDS_EMPLOYEE,
    STATUS_NEEDS_LOCATION,
    STATUS_NEEDS_OBJECT,
    STATUS_NEEDS_WORK_TYPE,
    STATUS_PRODUCT_CONFLICT,
    STATUS_READY,
    STATUS_SOURCE_ERROR,
    WorkBotCandidate,
    WorkBotInboxRow,
    WorkBotSyncResult,
    WorkBotUserLink,
)
from integrations.workbot.repository import WorkBotRepository, normalize_alias
from integrations.workbot.source import WorkBotSource
from models import DirectoryItem, Employee, ProductItem, WorkLogEntry
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
        products = self.directories.list_products(active_only=False)
        bindings = self.repository.employee_bindings()
        aliases = {
            alias_type: self.repository.alias_targets(alias_type)
            for alias_type in ("employee", "object", "location", "product")
        }
        for candidate in candidates:
            self._resolve(candidate, employees, objects, locations, work_types, products, bindings, aliases)
        return self.repository.sync(candidates)

    def list_rows(self, status: str = "") -> list[WorkBotInboxRow]:
        return self.repository.list_rows(status)

    def list_user_links(self, source_path: Path) -> list[WorkBotUserLink]:
        users = self.source.read_users(source_path)
        bindings = self.repository.employee_bindings()
        employees = {employee.id: employee for employee in self.employees.list() if employee.id is not None}
        employees_by_name = {
            normalize_alias(employee.full_name): employee
            for employee in employees.values()
        }
        result: list[WorkBotUserLink] = []
        for user in users:
            employee_id = bindings.get(user.max_user_id)
            employee = employees.get(employee_id)
            binding_saved = employee is not None
            if employee is None and user.employee_text:
                employee = employees_by_name.get(normalize_alias(user.employee_text))
                employee_id = employee.id if employee else None
            result.append(
                WorkBotUserLink(
                    max_user_id=user.max_user_id,
                    profile_name=user.profile_name,
                    employee_text=user.employee_text,
                    employee_id=employee_id if employee else None,
                    employee_name=employee.full_name if employee else "",
                    mobile_phone=employee.mobile_phone if employee else "",
                    binding_saved=binding_saved,
                )
            )
        return result

    def save_user_links(self, links: list[WorkBotUserLink]) -> None:
        selected = [link.employee_id for link in links if link.employee_id is not None]
        if len(selected) != len(set(selected)):
            raise ValueError("Один сотрудник не может быть привязан к нескольким MAX ID")
        for link in links:
            self.repository.save_employee_binding(
                link.max_user_id,
                link.employee_id,
                link.profile_name,
            )
            if link.employee_id is not None:
                self.employees.update_mobile_phone(link.employee_id, link.mobile_phone)

    def import_row(
        self,
        row_id: int,
        *,
        employee_id: int,
        work_date,
        location_id: int | None,
        object_id: int | None,
        work_type_id: int | None,
        product_id: int | None = None,
        product_alias_text: str = "",
        description: str,
        hours: float,
        remember: bool,
        reviewer: str = "",
    ) -> int:
        row = self.repository.get(row_id)
        if row is None:
            raise ValueError("Входящий отчет не найден")
        location = _by_id(self.directories.list_all("locations"), location_id)
        product = _by_id(self.directories.list_products(active_only=False), product_id)
        if product is not None:
            if object_id is None:
                object_id = product.object_id
            elif object_id != product.object_id:
                raise ValueError("Выбранное изделие относится к другому объекту")
        entry = WorkLogEntry(
            employee_id=employee_id,
            work_date=work_date,
            location_id=location_id,
            object_id=object_id,
            work_type_id=work_type_id,
            product_id=product_id,
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
            product_alias_text=product_alias_text,
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
        products: list[ProductItem],
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
        product_match = detect_product(
            "\n".join(
                value
                for value in (candidate.raw_text, candidate.source_fragment, candidate.work_types)
                if value
            ),
            products,
            aliases["product"],
        )
        candidate.product_id = product_match.product_id
        candidate.product_text = product_match.reference
        product = _by_id(products, candidate.product_id)
        if product is not None:
            if candidate.object_id is None:
                candidate.object_id = product.object_id
            elif candidate.object_id != product.object_id:
                candidate.status = STATUS_PRODUCT_CONFLICT
                candidate.error_message = "Проверьте изделие: оно относится к другому объекту"
                return
        elif product_match.ambiguous:
            candidate.error_message = "В сообщении найдено несколько возможных изделий; выберите изделие вручную"
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
        if not product_match.ambiguous:
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


def _by_id(items, item_id: int | None):
    return next((item for item in items if item.id == item_id), None)
