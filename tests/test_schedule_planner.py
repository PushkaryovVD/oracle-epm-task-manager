import re
from datetime import date

import pytest

from app.models import MonthRef, ScheduleStatus
from app.services.deployment_ledger import DeploymentLedger, LedgerStatus
from app.services.member_validator import MemberCheckResult
from app.services.period_service import PeriodMapping, load_period_mappings
from app.services.schedule_planner import PlanRequest, plan
from tests.test_naming_service import spec_names

PATTERN = "Monthly Close - {MMMM} {YYYY}"


def verified():
    """Successful Oracle member check (Year/Period exist)."""
    return MemberCheckResult(application="FCCS", verified=True)


@pytest.fixture
def std():
    return load_period_mappings()["standard_monthly"]


def make_request(mapping, **overrides):
    params = dict(
        start=MonthRef(2027, 1), end=MonthRef(2027, 12), name_pattern=PATTERN,
        template_name="Monthly Financial Close", mapping=mapping, template_valid=True,
    )
    params.update(overrides)
    return PlanRequest(**params)


def test_acceptance_preview_unchecked(std):
    result = plan(make_request(std))
    assert result.errors == []
    assert [r.name for r in result.rows] == spec_names()
    assert [r.index for r in result.rows] == list(range(1, 13))
    assert all(r.status == ScheduleStatus.READY_UNCHECKED for r in result.rows)
    assert [r.period_member for r in result.rows][:3] == ["Jan", "Feb", "Mar"]
    assert {r.year_member for r in result.rows} == {"FY27"}
    assert result.rows[1].day_zero == date(2027, 2, 28)
    assert len(result.deployable) == 12
    assert result.count(ScheduleStatus.READY_UNCHECKED) == 12
    assert result.count(ScheduleStatus.READY) == 0


def test_all_ready_when_oracle_checked_and_empty(std):
    result = plan(make_request(std, existing_names=set(), oracle_check=verified()))
    assert all(r.status == ScheduleStatus.READY for r in result.rows)
    assert result.count(ScheduleStatus.READY) == 12


def test_existing_names_skipped_case_insensitive(std):
    existing = {"monthly close - march 2027", "MONTHLY CLOSE - DECEMBER 2027", "Unrelated"}
    result = plan(make_request(std, existing_names=existing, oracle_check=verified()))
    statuses = {r.name: r.status for r in result.rows}
    assert statuses["Monthly Close - March 2027"] == ScheduleStatus.SKIPPED_EXISTS
    assert statuses["Monthly Close - December 2027"] == ScheduleStatus.SKIPPED_EXISTS
    assert result.count(ScheduleStatus.SKIPPED_EXISTS) == 2
    assert result.count(ScheduleStatus.READY) == 10
    assert result.count(ScheduleStatus.READY, ScheduleStatus.SKIPPED_EXISTS) == 12
    assert len(result.deployable) == 10
    assert all(r.status == ScheduleStatus.READY for r in result.deployable)


def test_across_year_boundary_with_fiscal_mapping():
    fa = load_period_mappings()["fiscal_april"]
    result = plan(make_request(fa, start=MonthRef(2027, 11), end=MonthRef(2028, 2)))
    assert [r.name for r in result.rows] == [
        "Monthly Close - November 2027", "Monthly Close - December 2027",
        "Monthly Close - January 2028", "Monthly Close - February 2028"]
    assert [r.period_member for r in result.rows] == ["P08", "P09", "P10", "P11"]
    assert {r.year_member for r in result.rows} == {"FY28"}
    assert result.rows[-1].day_zero == date(2028, 2, 29)


def _assert_all_blocked(result, message_part=None, in_errors=True):
    assert result.rows, "rows must still be shown"
    assert all(r.status == ScheduleStatus.BLOCKED for r in result.rows)
    assert result.deployable == []
    assert result.count(ScheduleStatus.BLOCKED) == len(result.rows)
    if message_part:
        assert any(message_part in e for e in result.errors)


def test_blocked_when_template_not_validated(std):
    result = plan(make_request(std, template_valid=False, existing_names=set()))
    _assert_all_blocked(result, "not been validated")


@pytest.mark.parametrize("name", ["", "   "])
def test_blocked_when_template_name_empty(std, name):
    result = plan(make_request(std, template_name=name))
    _assert_all_blocked(result, "template name is empty")


def test_blocked_on_unknown_placeholder(std):
    result = plan(make_request(std, name_pattern="Close {Month} {YYYY}"))
    _assert_all_blocked(result, "{Month}")


def test_pattern_without_month_placeholder_blocks_every_row_including_first(std):
    result = plan(make_request(std, name_pattern="Monthly Close {YYYY}", start=MonthRef(2027, 1), end=MonthRef(2027, 3)))
    _assert_all_blocked(result)
    assert result.errors == []
    first = result.rows[0]
    assert any("Same name as row(s) 2, 3" in m for m in first.messages)
    assert any("Same name as row(s) 1, 3" in m for m in result.rows[1].messages)


def test_partial_duplicates_only_block_colliding_rows(std):
    # {MM} alone repeats after 12 months: Jan 2027 and Jan 2028 collide, the rest is fine.
    result = plan(make_request(std, name_pattern="Close {MM}", start=MonthRef(2027, 1), end=MonthRef(2028, 1)))
    blocked = [r.index for r in result.rows if r.status == ScheduleStatus.BLOCKED]
    assert blocked == [1, 13]
    assert result.count(ScheduleStatus.READY_UNCHECKED) == 11


def test_single_month_with_static_pattern_is_not_a_duplicate(std):
    result = plan(make_request(std, name_pattern="X", start=MonthRef(2027, 1), end=MonthRef(2027, 1)))
    assert result.rows[0].status == ScheduleStatus.READY_UNCHECKED  # a single row never collides


def test_missing_period_mapping_blocks_only_that_month():
    partial = PeriodMapping.from_dict({"id": "partial", "periods": {"1": "Jan", "3": "Mar"}})
    result = plan(make_request(partial, start=MonthRef(2027, 1), end=MonthRef(2027, 3), existing_names=set(),
                                oracle_check=verified()))
    statuses = [r.status for r in result.rows]
    assert statuses == [ScheduleStatus.READY, ScheduleStatus.BLOCKED, ScheduleStatus.READY]
    feb = result.rows[1]
    assert feb.period_member == ""
    assert any("No Oracle period mapped for month 2" in m for m in feb.messages)


def test_unknown_day_zero_strategy_blocks(std):
    result = plan(make_request(std, day_zero_strategy="whatever"))
    assert all(r.status == ScheduleStatus.BLOCKED and r.day_zero is None for r in result.rows)


def test_fixed_day_zero_below_one_blocks(std):
    result = plan(make_request(std, day_zero_strategy="fixed_day", day_zero_fixed_day=0))
    assert all(r.status == ScheduleStatus.BLOCKED for r in result.rows)


def test_name_too_long_warns_while_limits_provisional(std):
    result = plan(make_request(std, name_pattern="{TemplateName} {MMMM} {YYYY}", template_name="T" * 80))
    assert all(r.status == ScheduleStatus.READY_UNCHECKED for r in result.rows)
    assert all(any(m.startswith("Warning:") and "longer than" in m for m in r.messages) for r in result.rows)


def test_forbidden_char_warns_while_limits_provisional(std):
    result = plan(make_request(std, name_pattern="{TemplateName} {MMMM} {YYYY}", template_name="Close/Q1"))
    assert all(r.status == ScheduleStatus.READY_UNCHECKED for r in result.rows)
    assert all(any("forbidden characters" in m for m in r.messages) for r in result.rows)


@pytest.fixture
def enforced_limits(monkeypatch):
    from app.services import schedule_planner
    from app.utils.config import naming_rules
    monkeypatch.setattr(schedule_planner, "naming_rules",
                        lambda: {**naming_rules(), "enforce_provisional_limits": True})


def test_name_too_long_blocks_when_enforced(std, enforced_limits):
    result = plan(make_request(std, name_pattern="{TemplateName} {MMMM} {YYYY}", template_name="T" * 80))
    _assert_all_blocked(result)
    assert all(any("longer than" in m for m in r.messages) for r in result.rows)


def test_forbidden_char_blocks_when_enforced(std, enforced_limits):
    result = plan(make_request(std, name_pattern="{TemplateName} {MMMM} {YYYY}", template_name="Close/Q1"))
    _assert_all_blocked(result)


def test_control_character_always_blocks(std):
    result = plan(make_request(std, name_pattern="{TemplateName} {MMMM}", template_name="Close\x01"))
    _assert_all_blocked(result)


def test_end_before_start_reports_error_and_no_rows(std):
    result = plan(make_request(std, start=MonthRef(2027, 5), end=MonthRef(2027, 4)))
    assert result.rows == [] and result.deployable == []
    assert any("before start" in e for e in result.errors)


def test_max_months_per_run_boundary(std):
    ok = plan(make_request(std, start=MonthRef(2027, 1), end=MonthRef(2031, 12)))   # 60 months
    assert len(ok.rows) == 60 and ok.errors == []
    too_many = plan(make_request(std, start=MonthRef(2027, 1), end=MonthRef(2032, 1)))  # 61 months
    assert too_many.deployable == []
    assert any("At most 60 months" in e for e in too_many.errors)


def test_count_with_no_statuses_is_zero(std):
    assert plan(make_request(std)).count() == 0


def test_preview_is_pure_does_not_mutate_existing_names(std):
    existing = {"Monthly Close - March 2027"}
    plan(make_request(std, existing_names=existing))
    assert existing == {"Monthly Close - March 2027"}


# --------------------------------------------------------------------------- READY needs a verified Oracle check
def test_existing_names_without_oracle_check_stays_unchecked(std):
    result = plan(make_request(std, existing_names={"Monthly Close - March 2027"}))
    statuses = [r.status for r in result.rows]
    assert statuses[2] == ScheduleStatus.SKIPPED_EXISTS  # existing_names still forces the skip
    assert statuses.count(ScheduleStatus.READY_UNCHECKED) == 11
    assert result.count(ScheduleStatus.READY) == 0


def test_unverified_check_without_error_is_unchecked(std):
    result = plan(make_request(std, oracle_check=MemberCheckResult(verified=False)))
    assert all(r.status == ScheduleStatus.READY_UNCHECKED for r in result.rows)


def test_verified_check_gives_ready_without_messages(std):
    result = plan(make_request(std, oracle_check=verified()))
    assert all(r.status == ScheduleStatus.READY and r.messages == [] for r in result.rows)


# --------------------------------------------------------------------------- oracle_check integration
def test_missing_year_blocks_every_row_with_message(std):
    check = MemberCheckResult(application="FCCS", verified=True, missing_years={"FY27"})
    result = plan(make_request(std, oracle_check=check))
    _assert_all_blocked(result)
    assert all(any(m.startswith("Year member 'FY27' does not exist in Oracle") and "Standard monthly" in m
                   for m in r.messages) for r in result.rows)


def test_missing_period_blocks_only_that_month(std):
    check = MemberCheckResult(application="FCCS", verified=True, missing_periods={"Mar", "Nov"})
    result = plan(make_request(std, oracle_check=check))
    blocked = [r.index for r in result.rows if r.status == ScheduleStatus.BLOCKED]
    assert blocked == [3, 11]
    assert len(result.rows[2].messages) == 1
    assert result.rows[2].messages[0].startswith("Period member 'Mar' does not exist in Oracle")
    assert "period mapping 'Standard monthly" in result.rows[2].messages[0]
    assert result.count(ScheduleStatus.READY) == 10


def test_missing_year_in_fiscal_mapping_blocks_only_its_months():
    fa = load_period_mappings()["fiscal_april"]
    check = MemberCheckResult(application="FCCS", verified=True, missing_years={"FY28"})
    result = plan(make_request(fa, start=MonthRef(2027, 2), end=MonthRef(2027, 5), oracle_check=check))
    assert [r.status for r in result.rows] == [ScheduleStatus.READY, ScheduleStatus.READY,
                                               ScheduleStatus.BLOCKED, ScheduleStatus.BLOCKED]


def test_check_error_blocks_all_rows(std):
    error = "Year/Period members could not be verified: Oracle is temporarily unavailable (HTTP 503)."
    result = plan(make_request(std, oracle_check=MemberCheckResult(error=error)))
    _assert_all_blocked(result)
    assert all(error in r.messages for r in result.rows)


def test_check_switched_off_is_ready_with_warning(std):
    result = plan(make_request(std, oracle_check=MemberCheckResult.switched_off()))
    assert all(r.status == ScheduleStatus.READY for r in result.rows)
    assert len(result.deployable) == 12
    assert all(any(m.startswith("Warning:") and "not verified in Oracle" in m for m in r.messages)
               for r in result.rows)


@pytest.fixture
def ledger():
    return DeploymentLedger("fccs-test")


@pytest.mark.parametrize("ledger_status,message_part", [
    (LedgerStatus.COMPLETED, "Already deployed by this application on"),
    (LedgerStatus.EXISTS, "Exists in Oracle (recorded"),
])
def test_ledger_completed_or_exists_skips(std, ledger, ledger_status, message_part):
    ledger.record("MONTHLY CLOSE - MARCH 2027", ledger_status)
    result = plan(make_request(std, oracle_check=verified(), ledger=ledger))
    march = result.rows[2]
    assert march.status == ScheduleStatus.SKIPPED_EXISTS
    assert any(message_part in m for m in march.messages)
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", march.messages[0])  # "T" replaced by a space
    assert result.count(ScheduleStatus.READY) == 11
    assert march not in result.deployable


def test_ledger_skip_applies_without_oracle_check(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.COMPLETED)
    result = plan(make_request(std, ledger=ledger))
    assert result.rows[2].status == ScheduleStatus.SKIPPED_EXISTS
    assert result.count(ScheduleStatus.READY_UNCHECKED) == 11


def test_ledger_unknown_blocks(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.UNKNOWN)
    result = plan(make_request(std, oracle_check=verified(), ledger=ledger))
    march = result.rows[2]
    assert march.status == ScheduleStatus.BLOCKED
    assert any("is unknown" in m and "Resolve Unknown" in m for m in march.messages)
    assert march not in result.deployable


def test_ledger_unknown_resolved_as_missing_becomes_deployable(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.UNKNOWN)
    ledger.resolve_unknown("Monthly Close - March 2027", exists_in_oracle=False)
    march = plan(make_request(std, oracle_check=verified(), ledger=ledger)).rows[2]
    assert march.status == ScheduleStatus.READY
    assert any("Previous attempt" in m for m in march.messages)


def test_ledger_unknown_resolved_as_existing_is_skipped(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.UNKNOWN)
    ledger.resolve_unknown("Monthly Close - March 2027", exists_in_oracle=True)
    march = plan(make_request(std, oracle_check=verified(), ledger=ledger)).rows[2]
    assert march.status == ScheduleStatus.SKIPPED_EXISTS


def test_ledger_failed_is_deployable_with_warning(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.FAILED, details="Invalid day zero")
    result = plan(make_request(std, oracle_check=verified(), ledger=ledger))
    march = result.rows[2]
    assert march.status == ScheduleStatus.READY
    assert march in result.deployable
    assert any(m.startswith("Warning: Previous attempt on") and "failed: Invalid day zero" in m
               for m in march.messages)


def test_ledger_failed_without_details(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.FAILED)
    march = plan(make_request(std, oracle_check=verified(), ledger=ledger)).rows[2]
    assert any("failed: no details" in m for m in march.messages)


def test_blocking_message_wins_over_ledger_skip(std, ledger):
    ledger.record("Monthly Close - March 2027", LedgerStatus.COMPLETED)
    check = MemberCheckResult(application="FCCS", verified=True, missing_periods={"Mar"})
    march = plan(make_request(std, oracle_check=check, ledger=ledger)).rows[2]
    assert march.status == ScheduleStatus.BLOCKED


def test_planner_never_contacts_oracle(std, ledger, monkeypatch):
    """Acceptance: 'No Oracle changes occur before confirmation' — building the preview makes no HTTP call."""
    from app.services import oracle_client

    def forbidden(*args, **kwargs):
        raise AssertionError("planner must not call Oracle")

    monkeypatch.setattr(oracle_client, "urllib_transport", forbidden)
    monkeypatch.setattr(oracle_client.OracleClient, "_request", forbidden)
    result = plan(make_request(std, oracle_check=verified(), ledger=ledger))
    assert len(result.deployable) == 12
    assert not ledger.path.exists()  # the preview does not write the ledger either