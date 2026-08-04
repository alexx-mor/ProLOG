"""Business rules for matching and importing WorkBot reports."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from hours import normalize_hours
from integrations.workbot.matcher import ProductMatch, detect_product, detect_products
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
from integrations.workbot.source import WorkBotSource, assign_candidate_identity
from models import DirectoryItem, Employee, ProductItem, WorkLogEntry
from services import (
    DirectoryService,
    EmployeeService,
    NON_WORK_LOCATIONS,
    WorkLogService,
)


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
            for alias_type in ("employee", "object", "location", "work_type", "product")
        }
        candidates = self._expand_employee_candidates(candidates, employees, aliases["employee"])
        candidates = self._expand_product_candidates(candidates, products, aliases["product"])
        assign_candidate_identity(candidates)
        for candidate in candidates:
            self._resolve(candidate, employees, objects, locations, work_types, products, bindings, aliases)
        return self.repository.sync(candidates)

    def list_rows(self, status: str = "") -> list[WorkBotInboxRow]:
        return self.repository.list_rows(status)

    def list_user_links(self, source_path: Path) -> list[WorkBotUserLink]:
        users = self.source.read_users(source_path)
        bindings = self.repository.employee_bindings()
        employees = {employee.id: employee for employee in self.employees.list() if employee.id is not None}
        result: list[WorkBotUserLink] = []
        for user in users:
            employee_id = bindings.get(user.max_user_id)
            employee = employees.get(employee_id)
            binding_saved = employee is not None
            result.append(
                WorkBotUserLink(
                    max_user_id=user.max_user_id,
                    profile_name=user.profile_name,
                    employee_id=employee_id if employee else None,
                    employee_name=employee.full_name if employee else "",
                    binding_saved=binding_saved,
                    match_source="saved" if binding_saved else "none",
                    match_message="Привязка сохранена" if binding_saved else "Не привязан",
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
        if candidate.object_id is None:
            candidate.object_id = _mentioned_id(
                candidate.source_fragment or candidate.raw_text,
                objects,
                aliases["object"],
            )
            matched_object = _by_id(objects, candidate.object_id)
            if matched_object is not None and not candidate.object_text:
                candidate.object_text = matched_object.name
        candidate.location_id = self._directory_id(candidate.location_text, locations, aliases["location"])
        candidate.work_type_id = self._directory_id(
            candidate.work_types,
            work_types,
            aliases["work_type"],
        )
        if candidate.work_type_id is None:
            candidate.work_type_id = _mentioned_id(
                candidate.work_types or candidate.source_fragment,
                work_types,
                aliases["work_type"],
            )
        product_sources = [candidate.source_fragment, candidate.work_types]
        if "segmented" not in candidate.source_kind:
            product_sources.append(candidate.raw_text)
        if candidate.product_id is not None:
            product_match = ProductMatch(candidate.product_id, candidate.product_text, False)
        else:
            product_match = detect_product(
                "\n".join(value for value in product_sources if value),
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
        location = _by_id(locations, candidate.location_id)
        is_non_work = bool(location and location.name in NON_WORK_LOCATIONS)
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
            candidate.error_message = candidate.error_message or "Проверьте количество часов"
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
        if candidate.employee_id is not None:
            return candidate.employee_id
        alias = aliases.get(normalize_alias(candidate.employee_text))
        explicit = alias if alias is not None else _exact_id(candidate.employee_text, employees, "full_name")
        if explicit is not None:
            return explicit
        return bindings.get(candidate.sender_id)

    def _expand_product_candidates(
        self,
        candidates: list[WorkBotCandidate],
        products: list[ProductItem],
        aliases: dict[str, int],
    ) -> list[WorkBotCandidate]:
        expanded: list[WorkBotCandidate] = []
        for candidate in candidates:
            text = candidate.source_fragment or candidate.work_types or candidate.raw_text
            matches = detect_products(text, products, aliases)
            if len(matches) <= 1 or any(match.ambiguous for match in matches):
                expanded.append(candidate)
                continue
            allocations = _product_hour_allocations(text, matches)
            allocation_is_known = allocations is not None
            for match_index, match in enumerate(matches):
                if allocation_is_known:
                    hours = allocations[match_index]
                    issue = candidate.error_message
                else:
                    hours = 0.0
                    total = f" Общие часы сообщения: {candidate.hours:g}." if candidate.hours > 0 else ""
                    issue = "Укажите часы по каждому изделию вручную." + total
                expanded.append(
                    replace(
                        candidate,
                        source_kind="product_segmented",
                        product_id=match.product_id,
                        product_text=match.reference,
                        object_id=None,
                        object_text="",
                        hours=hours,
                        status="new",
                        error_message=issue,
                        content_hash="",
                    )
                )
        return expanded

    def _expand_employee_candidates(
        self,
        candidates: list[WorkBotCandidate],
        employees: list[Employee],
        aliases: dict[str, int],
    ) -> list[WorkBotCandidate]:
        expanded: list[WorkBotCandidate] = []
        employees_by_id = {employee.id: employee for employee in employees if employee.id is not None}
        for candidate in candidates:
            if candidate.source_kind.startswith("historical"):
                expanded.append(candidate)
                continue
            employee_ids = _mentioned_employee_ids(candidate.raw_text, employees, aliases)
            if len(employee_ids) <= 1:
                expanded.append(candidate)
                continue
            for employee_id in employee_ids:
                employee = employees_by_id[employee_id]
                expanded.append(
                    replace(
                        candidate,
                        source_kind="employee_segmented",
                        employee_text=employee.full_name,
                        employee_id=employee_id,
                        work_types=candidate.work_types or candidate.source_fragment,
                        status="new",
                        error_message=candidate.error_message,
                        content_hash="",
                    )
                )
        return expanded

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


def _mentioned_id(value: str, items, aliases: dict[str, int]) -> int | None:
    normalized = normalize_alias(value)
    if not normalized:
        return None
    references: list[tuple[int, int]] = []
    for item in items:
        if item.id is not None and _contains_reference(normalized, str(item.name)):
            references.append((len(_compact_reference(item.name)), int(item.id)))
    for alias, item_id in aliases.items():
        if _contains_reference(normalized, alias):
            references.append((len(_compact_reference(alias)), item_id))
    if not references:
        return None
    best_score = max(score for score, _item_id in references)
    best_ids = {item_id for score, item_id in references if score == best_score}
    return next(iter(best_ids)) if len(best_ids) == 1 else None


def _contains_reference(normalized_text: str, reference: str) -> bool:
    normalized_text = normalize_alias(normalized_text)
    tokens = re.findall(r"[0-9a-zа-я]+", normalize_alias(reference))
    if not tokens:
        return False
    body = r"[^0-9a-zа-я]*".join(re.escape(token) for token in tokens)
    return re.search(rf"(?<![0-9a-zа-я]){body}(?![0-9a-zа-я])", normalized_text) is not None


def _compact_reference(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", normalize_alias(value))


def _mentioned_employee_ids(
    text: str,
    employees: list[Employee],
    aliases: dict[str, int],
) -> list[int]:
    references: list[tuple[int, int]] = []
    for employee in employees:
        if employee.id is None:
            continue
        for reference in _employee_references(employee.full_name):
            if _contains_reference(text, reference):
                references.append((_reference_position(text, reference), employee.id))
                break
    for alias, employee_id in aliases.items():
        if len(re.findall(r"[0-9a-zа-я]+", normalize_alias(alias))) < 2:
            continue
        if _contains_reference(text, alias):
            references.append((_reference_position(text, alias), employee_id))
    result: list[int] = []
    for _position, employee_id in sorted(references):
        if employee_id not in result:
            result.append(employee_id)
    return result


def _employee_references(full_name: str) -> list[str]:
    parts = full_name.split()
    references = [full_name]
    if len(parts) >= 2:
        initials = " ".join(part[0] for part in parts[1:] if part)
        if initials:
            references.append(f"{parts[0]} {initials}")
    return references


_HOURS_MENTION_RE = re.compile(
    r"(?<!\d)(\d{1,2}(?:[.,]\d+)?)\s*(?:ч(?:ас(?:а|ов)?)?\.?)",
    re.IGNORECASE,
)


def _product_hour_allocations(text: str, matches: list[ProductMatch]) -> list[float] | None:
    hours = [normalize_hours(match.group(1)) for match in _HOURS_MENTION_RE.finditer(text)]
    if len(hours) != len(matches):
        return None
    ordered = sorted(
        enumerate(matches),
        key=lambda item: _reference_position(text, item[1].reference),
    )
    result = [0.0] * len(matches)
    for hour_value, (original_index, _match) in zip(hours, ordered):
        result[original_index] = hour_value
    return result


def _reference_position(text: str, reference: str) -> int:
    normalized_text = normalize_alias(text)
    tokens = re.findall(r"[0-9a-zа-я]+", normalize_alias(reference))
    if not tokens:
        return len(normalized_text)
    body = r"[^0-9a-zа-я]*".join(re.escape(token) for token in tokens)
    match = re.search(body, normalized_text)
    return match.start() if match else len(normalized_text)


def _by_id(items, item_id: int | None):
    return next((item for item in items if item.id == item_id), None)
