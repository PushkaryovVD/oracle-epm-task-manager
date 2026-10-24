"""Plain data objects shared by services and UI."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "Error"
    WARNING = "Warning"
    INFO = "Info"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    message: str
    sheet: str | None = None
    row: int | None = None
    column: str | None = None

    @property
    def location(self) -> str:
        parts = []
        if self.sheet:
            parts.append(f"Sheet '{self.sheet}'")
        if self.row:
            parts.append(f"row {self.row}")
        if self.column:
            parts.append(f"column '{self.column}'")
        return ", ".join(parts)


class ScheduleStatus(StrEnum):
    READY = "Ready"
    READY_UNCHECKED = "Ready (not checked in Oracle)"
    SKIPPED_EXISTS = "Skipped - Already Exists"
    BLOCKED = "Blocked - Validation Error"
    PENDING = "Pending"
    VALIDATING = "Validating"
    DEPLOYING = "Deploying"
    COMPLETED = "Completed"
    SKIPPED = "Skipped"
    FAILED = "Failed"
    UNKNOWN = "Unknown - Check Oracle"


@dataclass(frozen=True, order=True)
class MonthRef:
    """A calendar month (not an Oracle period)."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"Month must be 1-12, got {self.month}")

    def next(self) -> "MonthRef":
        return MonthRef(self.year + 1, 1) if self.month == 12 else MonthRef(self.year, self.month + 1)


@dataclass
class PlannedSchedule:
    index: int
    month: MonthRef
    name: str
    period_member: str
    year_member: str
    day_zero: date | None
    status: ScheduleStatus
    messages: list[str] = field(default_factory=list)


@dataclass
class Environment:
    """A configured Oracle EPM system. Holds no secrets: the password lives in Windows Credential Manager."""

    id: str
    display_name: str
    service_type: str
    base_url: str
    username: str
    auth_method: str = "basic"
    is_production: bool = False
    default_org_unit: str = ""
    period_mapping: str = "standard_monthly"
    day_zero_strategy: str = "last_calendar_day"
    day_zero_fixed_day: int = 5
    application_name: str = ""          # empty = the only application visible to the user
    year_dimension: str = "Years"
    period_dimension: str = "Period"
    verify_members: bool = True         # check Year/Period with Get Member before deploying
    job_timeout_minutes: int = 30       # after this the outcome is recorded as unknown

    @property
    def host(self) -> str:
        return self.base_url.split("://", 1)[-1]
