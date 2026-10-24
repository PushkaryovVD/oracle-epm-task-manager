"""Builds the deployment preview. Pure logic: no Oracle calls happen here."""

from dataclasses import dataclass, field

from app.models import MonthRef, PlannedSchedule, ScheduleStatus
from app.services import naming_service
from app.services.deployment_ledger import DeploymentLedger, LedgerStatus
from app.services.member_validator import MemberCheckResult
from app.services.period_service import PeriodMapping, day_zero, month_range
from app.utils.config import naming_rules


@dataclass
class PlanRequest:
    start: MonthRef
    end: MonthRef
    name_pattern: str
    template_name: str
    mapping: PeriodMapping
    day_zero_strategy: str = "last_calendar_day"
    day_zero_fixed_day: int = 5
    org_unit: str = ""  # passed to Oracle as orgUnit at deploy time; empty = standard date mapping
    template_valid: bool = False
    # None = Oracle was not queried; otherwise the schedule names known to exist.
    existing_names: set[str] | None = None
    # Result of the pre-deployment Oracle check (Year/Period members); None = not run yet.
    oracle_check: MemberCheckResult | None = None
    # Local deployment ledger of the selected environment.
    ledger: DeploymentLedger | None = None


@dataclass
class PlanResult:
    rows: list[PlannedSchedule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def count(self, *statuses: ScheduleStatus) -> int:
        return sum(1 for r in self.rows if r.status in statuses)

    @property
    def deployable(self) -> list[PlannedSchedule]:
        return [r for r in self.rows if r.status in (ScheduleStatus.READY, ScheduleStatus.READY_UNCHECKED)]


def plan(request: PlanRequest) -> PlanResult:
    result = PlanResult()
    rules = naming_rules()

    try:
        months = month_range(request.start, request.end)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result
    if len(months) > rules["max_months_per_run"]:
        result.errors.append(f"At most {rules['max_months_per_run']} months can be deployed in one run.")
        return result

    if not request.template_name.strip():
        result.errors.append("Oracle template name is empty.")
    unknown = naming_service.unknown_placeholders(request.name_pattern)
    if unknown:
        result.errors.append("Unknown placeholder(s) in name pattern: " + ", ".join("{" + u + "}" for u in unknown))
    if not request.template_valid:
        result.errors.append("The Excel template has not been validated successfully.")

    existing = {n.casefold() for n in request.existing_names} if request.existing_names is not None else None
    names = ["" if unknown else naming_service.render(request.name_pattern, m, request.template_name) for m in months]
    rows_by_name: dict[str, list[int]] = {}
    for index, name in enumerate(names, start=1):
        rows_by_name.setdefault(name.casefold(), []).append(index)

    enforce_limits = rules.get("enforce_provisional_limits", False)
    for index, (month, name) in enumerate(zip(months, names), start=1):
        messages: list[str] = []
        warnings: list[str] = []
        if not unknown:
            messages.extend(naming_service.validate_name(name, rules, include_provisional=enforce_limits))
            if not enforce_limits:
                warnings.extend(f"{p} (limit not yet confirmed for Oracle)"
                                for p in naming_service.provisional_name_problems(name, rules))
            same = [r for r in rows_by_name[name.casefold()] if r != index]
            if same:
                messages.append("Same name as row(s) " + ", ".join(map(str, same)) + ". Add a month placeholder such as {MMMM}.")

        period = request.mapping.period_member(month)
        if not period:
            messages.append(f"No Oracle period mapped for month {month.month} in '{request.mapping.id}'.")

        try:
            zero = day_zero(month, request.day_zero_strategy, request.day_zero_fixed_day)
        except ValueError as exc:
            zero = None
            messages.append(str(exc))

        year = request.mapping.year_member(month)
        check = request.oracle_check
        if check is not None:
            if check.error:
                messages.append(check.error)
            if year in check.missing_years:
                messages.append(f"Year member '{year}' does not exist in Oracle (or your user has no access to it). "
                                f"Check the year format of the period mapping '{request.mapping.display_name}'.")
            if period and period in check.missing_periods:
                messages.append(f"Period member '{period}' does not exist in Oracle (or your user has no access to "
                                f"it). Check the period mapping '{request.mapping.display_name}'.")
            if check.skipped:
                warnings.append("Year/Period were not verified in Oracle (switched off in the environment settings).")

        previous = request.ledger.get(name) if request.ledger and name else None
        skip_reason = None
        if previous is not None:
            when = previous.updated_at.replace("T", " ")
            if previous.status == LedgerStatus.UNKNOWN:
                messages.append(f"The result of the deployment on {when} is unknown. Check the schedule in Oracle, "
                                "then click 'Resolve Unknown…' (Deploy section) and choose 'Exists in Oracle' "
                                "or 'Not in Oracle'.")
            elif previous.status in (LedgerStatus.COMPLETED, LedgerStatus.EXISTS):
                skip_reason = (f"Already deployed by this application on {when}." if previous.status ==
                               LedgerStatus.COMPLETED else f"Exists in Oracle (recorded {when}).")
            elif previous.status == LedgerStatus.FAILED:
                warnings.append(f"Previous attempt on {when} failed: {previous.details or 'no details'}.")

        if result.errors or messages:
            status = ScheduleStatus.BLOCKED
        elif skip_reason or (existing is not None and name.casefold() in existing):
            status = ScheduleStatus.SKIPPED_EXISTS
            messages.append(skip_reason or "A schedule with this name already exists in Oracle.")
        elif check is None or not check.verified:
            status = ScheduleStatus.READY_UNCHECKED
        else:
            status = ScheduleStatus.READY

        result.rows.append(PlannedSchedule(
            index=index,
            month=month,
            name=name,
            period_member=period or "",
            year_member=year,
            day_zero=zero,
            status=status,
            messages=messages + [f"Warning: {w}" for w in warnings],
        ))
    return result
