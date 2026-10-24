"""Calendar month -> Oracle Year/Period members, and Day Zero Date calculation."""

import calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.models import MonthRef
from app.utils.config import period_mapping_files

DAY_ZERO_STRATEGIES = {
    "last_calendar_day": "Last calendar day of month",
    "last_business_day": "Last business day of month (Mon-Fri)",
    "first_day_next_month": "First day of next month",
    "fixed_day": "Fixed day of month",
}


@dataclass(frozen=True)
class PeriodMapping:
    id: str
    display_name: str
    periods: dict[int, str]
    year_member_format: str = "FY{YY}"
    fiscal_year_start_month: int = 1
    fiscal_year_named_by: str = "end"

    @classmethod
    def from_dict(cls, data: dict) -> "PeriodMapping":
        return cls(
            id=data["id"],
            display_name=data.get("display_name", data["id"]),
            periods={int(k): str(v) for k, v in data["periods"].items()},
            year_member_format=data.get("year_member_format", "FY{YY}"),
            fiscal_year_start_month=int(data.get("fiscal_year_start_month", 1)),
            fiscal_year_named_by=data.get("fiscal_year_named_by", "end"),
        )

    def fiscal_year(self, month: MonthRef) -> int:
        start = self.fiscal_year_start_month
        if start == 1:
            return month.year
        start_year = month.year if month.month >= start else month.year - 1
        return start_year + 1 if self.fiscal_year_named_by == "end" else start_year

    def year_member(self, month: MonthRef) -> str:
        fy = self.fiscal_year(month)
        return self.year_member_format.replace("{YYYY}", f"{fy:04d}").replace("{YY}", f"{fy % 100:02d}")

    def period_member(self, month: MonthRef) -> str | None:
        return self.periods.get(month.month)


def load_period_mappings() -> dict[str, PeriodMapping]:
    mappings = {}
    for path in period_mapping_files():
        mapping = load_period_mapping(path)
        mappings[mapping.id] = mapping
    return mappings


def load_period_mapping(path: Path) -> PeriodMapping:
    with open(path, encoding="utf-8") as fh:
        return PeriodMapping.from_dict(json.load(fh))


def month_range(start: MonthRef, end: MonthRef) -> list[MonthRef]:
    if end < start:
        raise ValueError("End month is before start month.")
    months, current = [], start
    while current <= end:
        months.append(current)
        current = current.next()
    return months


def day_zero(month: MonthRef, strategy: str, fixed_day: int = 1) -> date:
    last_day = calendar.monthrange(month.year, month.month)[1]
    if strategy == "last_calendar_day":
        return date(month.year, month.month, last_day)
    if strategy == "last_business_day":
        result = date(month.year, month.month, last_day)
        while result.weekday() >= 5:
            result -= timedelta(days=1)
        return result
    if strategy == "first_day_next_month":
        nxt = month.next()
        return date(nxt.year, nxt.month, 1)
    if strategy == "fixed_day":
        if fixed_day < 1:
            raise ValueError("Fixed day must be 1 or greater.")
        return date(month.year, month.month, min(fixed_day, last_day))
    raise ValueError(f"Unknown Day Zero strategy: {strategy}")
