"""Pre-deployment check of Oracle Year/Period members with the Get Member REST API. Read-only."""

import logging
from dataclasses import dataclass, field

from app.models import Environment, PlannedSchedule
from app.services.oracle_client import OracleClient, OracleError, PermissionDeniedError

log = logging.getLogger(__name__)


@dataclass
class MemberCheckResult:
    application: str = ""
    verified: bool = False
    missing_years: set[str] = field(default_factory=set)
    missing_periods: set[str] = field(default_factory=set)
    error: str | None = None   # set when the check could not be completed
    skipped: bool = False      # verification switched off by the user (Environment.verify_members = False)

    @classmethod
    def switched_off(cls) -> "MemberCheckResult":
        return cls(verified=True, skipped=True)


def resolve_application(client: OracleClient, env: Environment) -> str:
    """Configured application name, or the only application the user can see. Raises OracleError/ValueError."""
    if env.application_name.strip():
        return env.application_name.strip()
    try:
        apps = client.list_applications()
    except PermissionDeniedError:
        raise ValueError("This Oracle user may not list applications. Enter the Oracle application name in the "
                         "environment settings (Manage… → Edit).") from None
    if len(apps) == 1:
        return apps[0]
    if not apps:
        raise ValueError("No Oracle application is visible to this user.")
    raise ValueError("Several Oracle applications are available (" + ", ".join(apps) +
                     "). Choose one in the environment settings.")


def check_members(client: OracleClient, env: Environment, rows: list[PlannedSchedule]) -> MemberCheckResult:
    if not env.verify_members:
        return MemberCheckResult.switched_off()
    result = MemberCheckResult()
    try:
        result.application = resolve_application(client, env)
        for dimension, members, missing in (
            (env.year_dimension, {r.year_member for r in rows if r.year_member}, result.missing_years),
            (env.period_dimension, {r.period_member for r in rows if r.period_member}, result.missing_periods),
        ):
            for member in sorted(members):
                if client.get_member(result.application, dimension, member) is None:
                    missing.add(member)
        result.verified = True
    except PermissionDeniedError as exc:
        result.error = ("Year/Period members could not be verified: " + str(exc) +
                        " You can switch off member verification in the environment settings at your own risk.")
    except (OracleError, ValueError) as exc:
        result.error = f"Year/Period members could not be verified: {exc}"
    if result.error:
        log.warning(result.error)
    return result
