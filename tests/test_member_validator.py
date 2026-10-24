"""member_validator: read-only Year/Period check with Get Member."""

from datetime import date

import pytest

from app.models import Environment, MonthRef, PlannedSchedule, ScheduleStatus
from app.services.member_validator import MemberCheckResult, check_members, resolve_application
from app.services.oracle_client import (
    AuthenticationError, NetworkError, OracleClient, PermissionDeniedError, ServiceUnavailableError,
)
from tests.oracle_fakes import BASE, PASSWORD, USER, ScriptedTransport, resp


def env(**overrides):
    params = dict(id="fccs-test", display_name="FCCS Test", service_type="fccs", base_url=BASE, username=USER)
    params.update(overrides)
    return Environment(**params)


def row(month, year="FY27", period=None):
    period = period if period is not None else ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month - 1]
    return PlannedSchedule(index=month, month=MonthRef(2027, month), name=f"Close {month}", period_member=period,
                           year_member=year, day_zero=date(2027, month, 1), status=ScheduleStatus.READY_UNCHECKED)


class FakeClient:
    def __init__(self, apps=("FCCS",), missing=(), raise_on=None):
        self.apps = list(apps)
        self.missing = set(missing)
        self.raise_on = raise_on or {}
        self.calls: list[tuple] = []

    def list_applications(self):
        self.calls.append(("list_applications",))
        if "list" in self.raise_on:
            raise self.raise_on["list"]
        return self.apps

    def get_member(self, application, dimension, member):
        self.calls.append(("get_member", application, dimension, member))
        if member in self.raise_on:
            raise self.raise_on[member]
        return None if member in self.missing else {"name": member}

    def deploy_template(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("check_members must never deploy")


def members_queried(client):
    return [c[2:] for c in client.calls if c[0] == "get_member"]


# --------------------------------------------------------------------------- application
def test_application_from_env_without_listing():
    client = FakeClient(apps=())
    assert resolve_application(client, env(application_name="  FCCS_Prod ")) == "FCCS_Prod"
    assert client.calls == []


def test_application_auto_when_exactly_one():
    client = FakeClient(apps=["OnlyApp"])
    assert resolve_application(client, env()) == "OnlyApp"


def test_application_auto_blank_name_treated_as_empty():
    assert resolve_application(FakeClient(apps=["OnlyApp"]), env(application_name="   ")) == "OnlyApp"


def test_no_application_is_error():
    result = check_members(FakeClient(apps=[]), env(), [row(1)])
    assert result.verified is False
    assert "No Oracle application is visible" in result.error


def test_several_applications_error_lists_names():
    result = check_members(FakeClient(apps=["FCCS", "PLAN", "TRCS"]), env(), [row(1)])
    assert result.verified is False
    assert "FCCS, PLAN, TRCS" in result.error
    assert "environment settings" in result.error


# --------------------------------------------------------------------------- members
def test_all_members_exist():
    client = FakeClient()
    result = check_members(client, env(), [row(m) for m in range(1, 13)])
    assert result.verified and result.error is None and not result.skipped
    assert result.application == "FCCS"
    assert result.missing_years == set() and result.missing_periods == set()


def test_unique_members_queried_once_each_with_configured_dimensions():
    client = FakeClient()
    rows = [row(m) for m in range(1, 13)] + [row(1), row(2, year="FY28")]
    check_members(client, env(year_dimension="Year", period_dimension="Periods"), rows)
    queried = members_queried(client)
    assert len(queried) == len(set(queried))
    assert sorted(m for d, m in queried if d == "Year") == ["FY27", "FY28"]
    assert len([m for d, m in queried if d == "Periods"]) == 12
    assert all(a == "FCCS" for a, _, _ in [c[1:] for c in client.calls if c[0] == "get_member"])


def test_empty_members_not_queried():
    client = FakeClient()
    check_members(client, env(), [row(1, period=""), row(2, year="")])
    assert members_queried(client) == [("Years", "FY27"), ("Period", "Feb")]


def test_missing_years_and_periods_collected():
    client = FakeClient(missing={"FY28", "Mar", "Dec"})
    rows = [row(m) for m in range(1, 13)] + [row(1, year="FY28")]
    result = check_members(client, env(), rows)
    assert result.verified is True and result.error is None
    assert result.missing_years == {"FY28"}
    assert result.missing_periods == {"Mar", "Dec"}


def test_permission_denied_mentions_switching_off():
    client = FakeClient(raise_on={"FY27": PermissionDeniedError("Your Oracle user is not allowed.", 403)})
    result = check_members(client, env(), [row(1)])
    assert result.verified is False
    assert "could not be verified" in result.error
    assert "not allowed" in result.error
    assert "switch off member verification" in result.error


@pytest.mark.parametrize("exc", [
    AuthenticationError("Oracle rejected the user name or password.", 401),
    ServiceUnavailableError("Oracle is temporarily unavailable (HTTP 503).", 503),
    NetworkError("Cannot reach the Oracle environment.", may_have_reached_server=False),
])
def test_oracle_error_becomes_error(exc):
    client = FakeClient(raise_on={"Jan": exc})
    result = check_members(client, env(), [row(1)])
    assert result.verified is False
    assert result.error == f"Year/Period members could not be verified: {exc}"
    assert "switch off" not in result.error


def test_error_while_listing_applications():
    client = FakeClient(raise_on={"list": AuthenticationError("bad creds", 401)})
    result = check_members(client, env(), [row(1)])
    assert result.verified is False and "bad creds" in result.error


def test_switched_off_makes_no_http_call():
    def transport(*args):  # pragma: no cover - must not be called
        raise AssertionError("no HTTP call expected")

    client = OracleClient(BASE, USER, PASSWORD, transport=transport, sleep=lambda s: None)
    result = check_members(client, env(verify_members=False), [row(1)])
    assert result == MemberCheckResult.switched_off()
    assert result.verified is True and result.skipped is True and result.error is None


def test_check_members_never_changes_oracle():
    """Acceptance: 'No Oracle changes occur before confirmation' — the pre-deploy check only reads."""
    transport = ScriptedTransport(
        resp(200, {"items": [{"name": "FCCS"}]}),
        *[resp(200, {"name": "x"})] * 13,
    )
    client = OracleClient(BASE, USER, PASSWORD, transport=transport, sleep=lambda s: None)
    result = check_members(client, env(), [row(m) for m in range(1, 13)])
    assert result.verified
    assert len(transport.calls) == 14
    assert {c.method for c in transport.calls} == {"GET"}
    assert all(c.body is None for c in transport.calls)
    assert not any("/jobs" in c.url for c in transport.calls)


def test_real_client_404_marks_member_missing():
    transport = ScriptedTransport(resp(404, {"detail": "not found"}), resp(200, {"name": "Jan"}))
    client = OracleClient(BASE, USER, PASSWORD, transport=transport, sleep=lambda s: None)
    result = check_members(client, env(application_name="FCCS"), [row(1, year="FY99")])
    assert result.missing_years == {"FY99"} and result.missing_periods == set()
    assert transport.calls[0].url.endswith("/applications/FCCS/dimensions/Years/members/FY99")
