"""Production calendar providers.

The UI and analytics work with local SQLite data. Providers only synchronize
official calendar facts into that local storage.
"""

from __future__ import annotations

from calendar import isleap
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from models import WorkCalendarDay, WorkDayType


ISDAYOFF_API_URL = "https://isdayoff.ru/api/getdata"
DEFAULT_COUNTRY_CODE = "ru"
REQUEST_TIMEOUT_SECONDS = 10
API_NOTE_PREFIX = "Автозагрузка"


@dataclass(frozen=True, slots=True)
class CalendarImportResult:
    year: int
    days: list[WorkCalendarDay]
    source_url: str


class ProductionCalendarError(RuntimeError):
    """Raised when a production calendar provider cannot return valid data."""


class IsDayOffCalendarProvider:
    """Loads the Russian production calendar from the public isdayoff.ru API."""

    def load_year(self, year: int) -> CalendarImportResult:
        if year < 2000 or year > 2100:
            raise ProductionCalendarError("Год должен быть в диапазоне 2000-2100")
        url = self._build_url(year)
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
                payload = response.read().decode("utf-8").strip()
        except HTTPError as exc:
            raise ProductionCalendarError(f"Сервис календаря вернул HTTP {exc.code}") from exc
        except URLError as exc:
            raise ProductionCalendarError("Не удалось подключиться к сервису производственного календаря") from exc
        return CalendarImportResult(year=year, days=self._parse_year(year, payload), source_url=url)

    def _build_url(self, year: int) -> str:
        query = urlencode({"year": year, "cc": DEFAULT_COUNTRY_CODE, "pre": 1, "covid": 1})
        return f"{ISDAYOFF_API_URL}?{query}"

    def _parse_year(self, year: int, payload: str) -> list[WorkCalendarDay]:
        expected_days = 366 if isleap(year) else 365
        if len(payload) != expected_days or not payload.isdigit():
            raise ProductionCalendarError("Сервис календаря вернул данные в неожиданном формате")
        current = date(year, 1, 1)
        days: list[WorkCalendarDay] = []
        for raw_code in payload:
            days.append(_calendar_day_from_api_code(current, int(raw_code)))
            current += timedelta(days=1)
        return days


def _calendar_day_from_api_code(work_date: date, code: int) -> WorkCalendarDay:
    is_weekend = work_date.weekday() >= 5
    note = ""
    if code & 4:
        day_type = WorkDayType.WORKING_SATURDAY.value if work_date.weekday() == 5 else WorkDayType.WORKING_HOLIDAY.value
        note = f"{API_NOTE_PREFIX}: рабочий выходной"
    elif code & 8:
        day_type = WorkDayType.HOLIDAY.value
        note = f"{API_NOTE_PREFIX}: официальный праздник"
    elif code & 1:
        day_type = WorkDayType.DAY_OFF.value if is_weekend else WorkDayType.HOLIDAY.value
        note = f"{API_NOTE_PREFIX}: нерабочий день"
    elif code & 2:
        day_type = WorkDayType.SHORTENED_WORKDAY.value
        note = f"{API_NOTE_PREFIX}: сокращенный рабочий день"
    else:
        day_type = WorkDayType.WORKDAY.value
    return WorkCalendarDay(work_date=work_date, day_type=day_type, note=note)
