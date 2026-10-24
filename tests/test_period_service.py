import json
from datetime import date

import pytest

from app.models import MonthRef
from app.services.period_service import (
    DAY_ZERO_STRATEGIES, PeriodMapping, day_zero, load_period_mapping, load_period_mappings, month_range,
)


# ---- MonthRef / month_range -------------------------------------------------

@pytest.mark.parametrize("month", [0, 13, -1])
def test_monthref_rejects_invalid_month(month):
    with pytest.raises(ValueError):
        MonthRef(2027, month)


def test_month_range_within_year():
    months = month_range(MonthRef(2027, 1), MonthRef(2027, 12))
    assert months == [MonthRef(2027, m) for m in range(1, 13)]


def test_month_range_across_year_boundary():
    assert month_range(MonthRef(2027, 11), MonthRef(2028, 2)) == [
        MonthRef(2027, 11), MonthRef(2027, 12), MonthRef(2028, 1), MonthRef(2028, 2)]


def test_month_range_single_month():
    assert month_range(MonthRef(2027, 6), MonthRef(2027, 6)) == [MonthRef(2027, 6)]


@pytest.mark.parametrize("start, end", [
    (MonthRef(2027, 5), MonthRef(2027, 4)),
    (MonthRef(2028, 1), MonthRef(2027, 12)),
])
def test_month_range_end_before_start(start, end):
    with pytest.raises(ValueError, match="before start"):
        month_range(start, end)


# ---- day_zero ---------------------------------------------------------------

def test_all_strategies_known():
    assert set(DAY_ZERO_STRATEGIES) == {"last_calendar_day", "last_business_day", "first_day_next_month", "fixed_day"}


@pytest.mark.parametrize("ym, expected", [
    ((2027, 1), date(2027, 1, 31)),
    ((2027, 4), date(2027, 4, 30)),
    ((2027, 2), date(2027, 2, 28)),
    ((2028, 2), date(2028, 2, 29)),   # leap year
    ((2100, 2), date(2100, 2, 28)),   # century, not leap
    ((2000, 2), date(2000, 2, 29)),
])
def test_last_calendar_day(ym, expected):
    assert day_zero(MonthRef(*ym), "last_calendar_day") == expected


def test_last_business_day_month_ending_sunday():
    assert date(2027, 2, 28).weekday() == 6
    result = day_zero(MonthRef(2027, 2), "last_business_day")
    assert result == date(2027, 2, 26) and result.weekday() == 4


def test_last_business_day_month_ending_saturday():
    assert date(2027, 7, 31).weekday() == 5
    result = day_zero(MonthRef(2027, 7), "last_business_day")
    assert result == date(2027, 7, 30) and result.weekday() == 4


def test_last_business_day_month_ending_weekday():
    assert date(2028, 2, 29).weekday() == 1
    assert day_zero(MonthRef(2028, 2), "last_business_day") == date(2028, 2, 29)


@pytest.mark.parametrize("year", [2027, 2028])
def test_last_business_day_always_weekday_in_same_month(year):
    for m in range(1, 13):
        d = day_zero(MonthRef(year, m), "last_business_day")
        assert d.weekday() < 5 and d.month == m and d.year == year


@pytest.mark.parametrize("ym, expected", [
    ((2027, 1), date(2027, 2, 1)),
    ((2027, 12), date(2028, 1, 1)),
    ((2028, 2), date(2028, 3, 1)),
])
def test_first_day_next_month(ym, expected):
    assert day_zero(MonthRef(*ym), "first_day_next_month") == expected


@pytest.mark.parametrize("ym, fixed, expected", [
    ((2027, 3), 5, date(2027, 3, 5)),
    ((2027, 3), 1, date(2027, 3, 1)),
    ((2027, 2), 31, date(2027, 2, 28)),
    ((2028, 2), 31, date(2028, 2, 29)),
    ((2027, 4), 31, date(2027, 4, 30)),
    ((2027, 2), 29, date(2027, 2, 28)),
])
def test_fixed_day_clamped(ym, fixed, expected):
    assert day_zero(MonthRef(*ym), "fixed_day", fixed) == expected


@pytest.mark.parametrize("fixed", [0, -1])
def test_fixed_day_below_one(fixed):
    with pytest.raises(ValueError, match="1 or greater"):
        day_zero(MonthRef(2027, 1), "fixed_day", fixed)


@pytest.mark.parametrize("strategy", ["", "LAST_CALENDAR_DAY", "end_of_month"])
def test_unknown_strategy(strategy):
    with pytest.raises(ValueError, match="Unknown Day Zero strategy"):
        day_zero(MonthRef(2027, 1), strategy)


# ---- PeriodMapping ----------------------------------------------------------

@pytest.fixture
def mappings():
    return load_period_mappings()


def test_config_mappings_loaded(mappings):
    assert {"standard_monthly", "fiscal_april"} <= set(mappings)
    for mapping in mappings.values():
        assert set(mapping.periods) == set(range(1, 13))


def test_standard_mapping(mappings):
    std = mappings["standard_monthly"]
    assert std.period_member(MonthRef(2027, 1)) == "Jan"
    assert std.period_member(MonthRef(2027, 12)) == "Dec"
    assert std.year_member(MonthRef(2027, 1)) == "FY27"
    assert std.year_member(MonthRef(2027, 12)) == "FY27"
    assert std.year_member(MonthRef(2028, 1)) == "FY28"


@pytest.mark.parametrize("ym, period, year", [
    ((2027, 4), "P01", "FY28"),
    ((2027, 11), "P08", "FY28"),
    ((2027, 12), "P09", "FY28"),
    ((2028, 1), "P10", "FY28"),
    ((2028, 3), "P12", "FY28"),
    ((2027, 3), "P12", "FY27"),
    ((2028, 4), "P01", "FY29"),
])
def test_fiscal_april_named_by_end(mappings, ym, period, year):
    fa = mappings["fiscal_april"]
    assert fa.period_member(MonthRef(*ym)) == period
    assert fa.year_member(MonthRef(*ym)) == year


def _fiscal(**overrides):
    data = {
        "id": "fa", "fiscal_year_start_month": 4, "fiscal_year_named_by": "end", "year_member_format": "FY{YY}",
        "periods": {str(m): f"P{((m - 4) % 12) + 1:02d}" for m in range(1, 13)},
    }
    data.update(overrides)
    return PeriodMapping.from_dict(data)


def test_fiscal_named_by_start():
    fa = _fiscal(fiscal_year_named_by="start")
    assert fa.year_member(MonthRef(2027, 11)) == "FY27"
    assert fa.year_member(MonthRef(2028, 2)) == "FY27"
    assert fa.year_member(MonthRef(2027, 4)) == "FY27"
    assert fa.year_member(MonthRef(2027, 3)) == "FY26"


def test_fiscal_periods_generated_helper_matches_config(mappings):
    assert _fiscal().periods == mappings["fiscal_april"].periods


def test_year_member_format_four_digits():
    fa = _fiscal(year_member_format="{YYYY}")
    assert fa.year_member(MonthRef(2027, 11)) == "2028"
    std = PeriodMapping.from_dict({"id": "s", "periods": {"1": "Jan"}, "year_member_format": "{YYYY}"})
    assert std.year_member(MonthRef(2027, 1)) == "2027"


def test_from_dict_defaults():
    m = PeriodMapping.from_dict({"id": "x", "periods": {"1": "Jan"}})
    assert m.display_name == "x"
    assert m.year_member_format == "FY{YY}"
    assert m.fiscal_year_start_month == 1
    assert m.period_member(MonthRef(2027, 2)) is None


def test_load_single_mapping_from_file(tmp_path):
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"id": "custom", "display_name": "Custom", "periods": {"1": "Period 1"}}),
                    encoding="utf-8")
    m = load_period_mapping(path)
    assert m.id == "custom"
    assert m.display_name == "Custom"
    assert m.periods == {1: "Period 1"}
