"""Work log analytics calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from category_rules import normalize_pay_category
from models import Employee, PayRate, WorkCalendarDay, WorkDayType, WorkLogEntry

DEFAULT_MONTHLY_HOURS_NORM = 168
MONEY_QUANT = Decimal("0.01")


@dataclass(slots=True)
class AnalyticsSummary:
    employees_count: int = 0
    entries_count: int = 0
    total_hours: int = 0
    person_hours: int = 0
    payroll: Decimal = Decimal("0")


@dataclass(slots=True)
class ObjectAnalyticsRow:
    object_name: str
    employees_count: int
    entries_count: int
    total_hours: int
    person_hours: int
    payroll: Decimal


@dataclass(slots=True)
class EmployeeAnalyticsRow:
    employee_name: str
    position: str
    category: str
    objects_count: int
    entries_count: int
    total_hours: int
    payroll: Decimal


@dataclass(slots=True)
class WorkTypeAnalyticsRow:
    work_type_name: str
    employees_count: int
    entries_count: int
    total_hours: int
    payroll: Decimal


@dataclass(slots=True)
class DateAnalyticsRow:
    work_date: str
    day_type: str
    employees_count: int
    entries_count: int
    total_hours: int
    payroll: Decimal


@dataclass(slots=True)
class AnalyticsResult:
    summary: AnalyticsSummary
    by_object: list[ObjectAnalyticsRow]
    by_employee: list[EmployeeAnalyticsRow]
    by_work_type: list[WorkTypeAnalyticsRow]
    by_date: list[DateAnalyticsRow]


@dataclass(slots=True)
class _GroupAccumulator:
    name: str
    employees: set[int] = field(default_factory=set)
    entries_count: int = 0
    total_hours: int = 0
    payroll: Decimal = Decimal("0")

    def add(self, employee_id: int, hours: int, payroll: Decimal) -> None:
        self.employees.add(employee_id)
        self.entries_count += 1
        self.total_hours += hours
        self.payroll += payroll


@dataclass(slots=True)
class _EmployeeAccumulator:
    employee_name: str
    position: str
    category: str
    objects: set[str] = field(default_factory=set)
    entries_count: int = 0
    total_hours: int = 0
    payroll: Decimal = Decimal("0")

    def add(self, object_name: str, hours: int, payroll: Decimal) -> None:
        if object_name:
            self.objects.add(object_name)
        self.entries_count += 1
        self.total_hours += hours
        self.payroll += payroll


def build_analytics(
    entries: list[WorkLogEntry],
    employees: list[Employee],
    pay_rates: list[PayRate],
    calendar_days: list[WorkCalendarDay] | None = None,
    monthly_hours_norm: int = DEFAULT_MONTHLY_HOURS_NORM,
) -> AnalyticsResult:
    employees_by_id = {employee.id: employee for employee in employees if employee.id is not None}
    pay_rates_by_key = {
        (rate.position_name.casefold(), normalize_pay_category(rate.category)): rate
        for rate in pay_rates
    }
    monthly_norm = monthly_hours_norm if monthly_hours_norm > 0 else DEFAULT_MONTHLY_HOURS_NORM
    calendar_by_date = {item.work_date: item for item in calendar_days or []}

    object_groups: dict[str, _GroupAccumulator] = {}
    employee_groups: dict[int, _EmployeeAccumulator] = {}
    work_type_groups: dict[str, _GroupAccumulator] = {}
    date_groups: dict[str, _GroupAccumulator] = {}
    summary_employee_ids: set[int] = set()
    summary = AnalyticsSummary(entries_count=len(entries))

    for entry in entries:
        employee = employees_by_id.get(entry.employee_id)
        employee_name = entry.employee_name or (employee.full_name if employee else "Неизвестный сотрудник")
        position_name = employee.position if employee else ""
        category = normalize_pay_category(employee.category if employee else "")
        pay_rate = pay_rates_by_key.get((position_name.casefold(), normalize_pay_category(category)))
        hours = int(entry.hours or 0)
        payroll = _entry_payroll(hours, pay_rate, monthly_norm, entry, calendar_by_date)
        object_name = entry.object_name or "Без объекта"
        work_type_name = entry.work_type_name or "Без вида работ"
        day_key = entry.work_date.isoformat()

        summary_employee_ids.add(entry.employee_id)
        summary.total_hours += hours
        summary.person_hours += hours
        summary.payroll += payroll

        object_group = object_groups.setdefault(object_name, _GroupAccumulator(object_name))
        object_group.add(entry.employee_id, hours, payroll)

        employee_group = employee_groups.setdefault(
            entry.employee_id,
            _EmployeeAccumulator(employee_name=employee_name, position=position_name, category=category),
        )
        employee_group.add(object_name, hours, payroll)

        work_type_group = work_type_groups.setdefault(work_type_name, _GroupAccumulator(work_type_name))
        work_type_group.add(entry.employee_id, hours, payroll)

        date_group = date_groups.setdefault(day_key, _GroupAccumulator(day_key))
        date_group.add(entry.employee_id, hours, payroll)

    summary.employees_count = len(summary_employee_ids)
    summary.payroll = summary.payroll.quantize(MONEY_QUANT)

    return AnalyticsResult(
        summary=summary,
        by_object=[
            ObjectAnalyticsRow(
                object_name=group.name,
                employees_count=len(group.employees),
                entries_count=group.entries_count,
                total_hours=group.total_hours,
                person_hours=group.total_hours,
                payroll=group.payroll.quantize(MONEY_QUANT),
            )
            for group in sorted(object_groups.values(), key=lambda value: value.name.casefold())
        ],
        by_employee=[
            EmployeeAnalyticsRow(
                employee_name=group.employee_name,
                position=group.position,
                category=group.category,
                objects_count=len(group.objects),
                entries_count=group.entries_count,
                total_hours=group.total_hours,
                payroll=group.payroll.quantize(MONEY_QUANT),
            )
            for group in sorted(employee_groups.values(), key=lambda value: value.employee_name.casefold())
        ],
        by_work_type=[
            WorkTypeAnalyticsRow(
                work_type_name=group.name,
                employees_count=len(group.employees),
                entries_count=group.entries_count,
                total_hours=group.total_hours,
                payroll=group.payroll.quantize(MONEY_QUANT),
            )
            for group in sorted(work_type_groups.values(), key=lambda value: value.name.casefold())
        ],
        by_date=[
            DateAnalyticsRow(
                work_date=_display_date(group.name),
                day_type=_day_type_label(group.name, calendar_by_date),
                employees_count=len(group.employees),
                entries_count=group.entries_count,
                total_hours=group.total_hours,
                payroll=group.payroll.quantize(MONEY_QUANT),
            )
            for group in sorted(date_groups.values(), key=lambda value: value.name)
        ],
    )


def format_money(value: Decimal) -> str:
    amount = f"{value.quantize(MONEY_QUANT):,.2f}"
    return amount.replace(",", " ").replace(".", ",") + " руб."


def _entry_payroll(
    hours: int,
    pay_rate: PayRate | None,
    monthly_hours_norm: int,
    entry: WorkLogEntry,
    calendar_by_date: dict[date, WorkCalendarDay],
) -> Decimal:
    if hours <= 0 or pay_rate is None:
        return Decimal("0")
    salary = _payroll_base_salary(pay_rate, entry)
    if salary <= 0:
        return Decimal("0")
    multiplier = _payroll_multiplier(pay_rate, entry, calendar_by_date)
    if pay_rate.salary_type == "monthly":
        return salary / Decimal(monthly_hours_norm) * Decimal(hours) * multiplier
    return salary * Decimal(hours) * multiplier


def _payroll_base_salary(pay_rate: PayRate, entry: WorkLogEntry) -> Decimal:
    location = (entry.location_name or "").casefold()
    if "кд" in location or "kd" in location or "дальн" in location:
        far_trip_salary = _parse_money(pay_rate.far_trip_salary)
        if far_trip_salary > 0:
            return far_trip_salary
    return _parse_money(pay_rate.salary)


def _payroll_multiplier(
    pay_rate: PayRate,
    entry: WorkLogEntry,
    calendar_by_date: dict[date, WorkCalendarDay],
) -> Decimal:
    day_type = _day_type_for_entry(entry, calendar_by_date)
    if day_type in {WorkDayType.HOLIDAY.value, WorkDayType.WORKING_HOLIDAY.value, WorkDayType.DAY_OFF.value}:
        return _parse_coefficient(pay_rate.holiday_coeff)
    if day_type == WorkDayType.WORKING_SATURDAY.value:
        return _parse_coefficient(pay_rate.saturday_coeff)
    location = (entry.location_name or "").casefold()
    if "кб" in location or "kb" in location or "ближн" in location:
        return _parse_coefficient(pay_rate.near_trip_coeff)
    return Decimal("1")


def _day_type_for_entry(entry: WorkLogEntry, calendar_by_date: dict[date, WorkCalendarDay]) -> str:
    calendar_day = calendar_by_date.get(entry.work_date)
    if calendar_day:
        return calendar_day.day_type
    if entry.work_date.weekday() == 6:
        return WorkDayType.WORKING_HOLIDAY.value
    if entry.work_date.weekday() == 5:
        return WorkDayType.WORKING_SATURDAY.value
    return WorkDayType.WORKDAY.value


def _parse_money(value: str) -> Decimal:
    normalized = value.strip().replace(" ", "").replace(",", ".")
    if not normalized:
        return Decimal("0")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return Decimal("0")


def _parse_coefficient(value: str) -> Decimal:
    normalized = value.strip().replace(" ", "").replace(",", ".")
    if not normalized:
        return Decimal("1")
    try:
        result = Decimal(normalized)
    except InvalidOperation:
        return Decimal("1")
    return result if result > 0 else Decimal("1")


def _display_date(value: str) -> str:
    try:
        year, month, day = value.split("-")
    except ValueError:
        return value
    return f"{day}.{month}.{year}"


def _day_type_label(value: str, calendar_by_date: dict[date, WorkCalendarDay]) -> str:
    try:
        work_date = date.fromisoformat(value)
    except ValueError:
        return ""
    calendar_day = calendar_by_date.get(work_date)
    if calendar_day:
        return calendar_day.day_type
    if work_date.weekday() == 6:
        return WorkDayType.WORKING_HOLIDAY.value
    if work_date.weekday() == 5:
        return WorkDayType.WORKING_SATURDAY.value
    return WorkDayType.WORKDAY.value
