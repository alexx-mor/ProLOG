"""Business logic layer."""

from __future__ import annotations

from datetime import date

from analytics import AnalyticsResult, build_analytics
from category_rules import category_values_from_rule, normalize_employee_category
from directory_files import load_position_category_map
from database import DirectoryRepository, EmployeeRepository, WorkLogRepository
from models import Employee, ProductItem, WorkCalendarDay, WorkLogEntry


class DirectoryService:
    def __init__(self, repository: DirectoryRepository) -> None:
        self.repository = repository

    def list(self, key: str):
        return self.repository.list_items(key)

    def list_all(self, key: str):
        return self.repository.list_items(key, active_only=False)

    def ensure(self, key: str, name: str) -> int | None:
        value = name.strip()
        return self.repository.upsert(key, value) if value else None

    def rename(self, key: str, item_id: int, name: str) -> None:
        self.repository.rename(key, item_id, name)

    def set_active(self, key: str, item_id: int, is_active: bool) -> None:
        self.repository.set_active(key, item_id, is_active)

    def set_position_category(self, item_id: int, category: str) -> None:
        self.repository.set_position_category(item_id, category)

    def update_position_details(
        self,
        item_id: int,
        name: str,
        category: str,
        student_allowed: bool,
        salary: str,
        salary_type: str,
        group: str,
    ) -> None:
        self.repository.update_position_details(item_id, name, category, student_allowed, salary, salary_type, group)

    def update_object_details(
        self,
        item_id: int,
        name: str,
        project_number: str,
        contract_number: str,
        customer: str,
        contract_type: str,
        object_type: str,
        object_subtype: str,
        signed_date: str,
        due_date: str,
        object_status: str,
    ) -> None:
        self.repository.update_object_details(
            item_id,
            name,
            project_number,
            contract_number,
            customer,
            contract_type,
            object_type,
            object_subtype,
            signed_date,
            due_date,
            object_status,
        )

    def delete(self, key: str, item_id: int) -> None:
        self.repository.delete(key, item_id)

    def list_calendar_days(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[WorkCalendarDay]:
        return self.repository.list_calendar_days(date_from, date_to)

    def save_calendar_day(self, calendar_day: WorkCalendarDay) -> int:
        return self.repository.save_calendar_day(calendar_day)

    def delete_calendar_day(self, item_id: int) -> None:
        self.repository.delete_calendar_day(item_id)

    def list_products(self, active_only: bool = False) -> list[ProductItem]:
        return self.repository.list_products(active_only)

    def save_product(self, product: ProductItem) -> int:
        return self.repository.save_product(product)

    def set_product_active(self, product_id: int, is_active: bool) -> None:
        self.repository.set_product_active(product_id, is_active)

    def delete_product(self, product_id: int) -> None:
        self.repository.delete_product(product_id)

    def list_pay_rates(self):
        return self.repository.list_pay_rates()

    def update_pay_rate(
        self,
        item_id: int,
        salary: str,
        salary_type: str,
        far_trip_coeff: str,
        near_trip_coeff: str,
        holiday_coeff: str,
        saturday_coeff: str,
    ) -> None:
        self.repository.update_pay_rate(
            item_id,
            salary,
            salary_type,
            far_trip_coeff,
            near_trip_coeff,
            holiday_coeff,
            saturday_coeff,
        )

    def category_for_position(self, position: str) -> str:
        return self.repository.category_for_position(position)

    def student_allowed_for_position(self, position: str) -> bool:
        return self.repository.student_allowed_for_position(position)

    def apply_department_defaults(self, department: str) -> None:
        self.repository.apply_department_defaults(department)


class EmployeeService:
    def __init__(self, employees: EmployeeRepository, directories: DirectoryService) -> None:
        self.employees = employees
        self.directories = directories

    def list(self, search: str = "", position: str = "", group: str = "") -> list[Employee]:
        return self.employees.list(search, position, group)

    def get(self, employee_id: int) -> Employee | None:
        return self.employees.get(employee_id)

    def save(self, employee: Employee, validate_category: bool = True) -> int:
        if not employee.full_name.strip():
            raise ValueError("Укажите ФИО сотрудника")
        employee.position = _uppercase_first(employee.position)
        employee.category = _normalize_employee_category(employee.category)
        known_category_rule = self.directories.category_for_position(employee.position)
        position_id = self.directories.ensure("positions", employee.position)
        json_position_categories = load_position_category_map()
        is_custom_position = employee.position not in json_position_categories
        if position_id and employee.position and not employee.category and (not known_category_rule or is_custom_position):
            self.directories.set_position_category(position_id, "—")
        has_categories = bool(category_values_from_rule(self.directories.category_for_position(employee.position)))
        has_student_category = self.directories.student_allowed_for_position(employee.position)
        if not has_categories and not has_student_category:
            employee.category = ""
        if validate_category:
            self._validate_category(employee)
        return self.employees.save(employee)

    def import_employee(self, full_name: str, position: str, category: str) -> int:
        return self.save(
            Employee(
                full_name=full_name,
                position=position,
                category=category,
            ),
            validate_category=False,
        )

    def delete(self, employee_id: int) -> None:
        self.employees.delete(employee_id)

    def _validate_category(self, employee: Employee) -> None:
        rule = self.directories.category_for_position(employee.position)
        allowed = category_values_from_rule(rule)
        if self.directories.student_allowed_for_position(employee.position):
            allowed = ["0 (студент)", *allowed]
        if not allowed:
            return
        category = employee.category.strip()
        if not category:
            raise ValueError(f"Укажите категорию сотрудника для должности '{employee.position}'")
        if category not in allowed:
            allowed_text = ", ".join(allowed)
            raise ValueError(
                f"Категория '{category}' не подходит для должности '{employee.position}'. "
                f"Допустимо: {allowed_text}"
            )


def _uppercase_first(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    return stripped[0].upper() + stripped[1:]


def _normalize_employee_category(value: str) -> str:
    return normalize_employee_category(value)


def is_student_category_allowed(position: str) -> bool:
    normalized = position.strip().casefold()
    allowed_positions = (
        "электромонтажник",
        "слесарь",
        "инженер асутп",
        "слесарь-электромонтажник",
        "слесарь кипиа",
    )
    return normalized in allowed_positions


class WorkLogService:
    def __init__(self, worklogs: WorkLogRepository, directories: DirectoryService) -> None:
        self.worklogs = worklogs
        self.directories = directories

    def save(self, entry: WorkLogEntry) -> int:
        if not entry.employee_id:
            raise ValueError("Выберите сотрудника")
        is_non_work_location = entry.location_name in NON_WORK_LOCATIONS
        if not is_non_work_location and not entry.description.strip():
            raise ValueError("Заполните описание работ")
        if entry.hours < 0:
            raise ValueError("Часы не могут быть отрицательными")
        if entry.hours > 24:
            raise ValueError("За день можно указать не более 24 часов")
        if entry.hours == 0 and not is_non_work_location:
            raise ValueError("Для выбранного местонахождения укажите часы больше нуля")
        return self.worklogs.save(entry)

    def for_employee_date(self, employee_id: int, work_date: date) -> list[WorkLogEntry]:
        return self.worklogs.list_for_employee_date(employee_id, work_date)

    def get(self, entry_id: int) -> WorkLogEntry | None:
        return self.worklogs.get(entry_id)

    def duplicate_last(self, employee_id: int, target_date: date) -> WorkLogEntry | None:
        last = self.worklogs.last_for_employee(employee_id)
        if not last:
            return None
        last.id = None
        last.work_date = target_date
        last.hours = 0
        last.comment = ""
        return last

    def report_entries(
        self,
        work_date: date,
        object_id: int | None = None,
    ) -> list[WorkLogEntry]:
        return self.worklogs.list_entries(
            date_from=work_date,
            date_to=work_date,
            object_id=object_id,
        )

    def search_entries(
        self,
        employee_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        object_id: int | None = None,
    ) -> list[WorkLogEntry]:
        return self.worklogs.list_entries(
            employee_id=employee_id,
            date_from=date_from,
            date_to=date_to,
            object_id=object_id,
        )


class AnalyticsService:
    def __init__(
        self,
        worklogs: WorkLogService,
        employees: EmployeeService,
        directories: DirectoryService,
    ) -> None:
        self.worklogs = worklogs
        self.employees = employees
        self.directories = directories

    def build(
        self,
        employee_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        object_id: int | None = None,
        monthly_hours_norm: int = 168,
    ) -> AnalyticsResult:
        entries = self.worklogs.search_entries(
            employee_id=employee_id,
            date_from=date_from,
            date_to=date_to,
            object_id=object_id,
        )
        return build_analytics(
            entries=entries,
            employees=self.employees.list(),
            pay_rates=self.directories.list_pay_rates(),
            calendar_days=self.directories.list_calendar_days(date_from, date_to),
            monthly_hours_norm=monthly_hours_norm,
        )


NON_WORK_LOCATIONS = {
    "Отпуск",
    "Без содержания",
    "Прогул",
    "Больничный",
    "Учебный отпуск",
}
