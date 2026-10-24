import calendar
import locale
from pathlib import Path

import pytest

from app.models import MonthRef
from app.services import naming_service
from app.services.naming_service import render, unknown_placeholders, validate_name
from app.services.period_service import month_range

SPEC = Path(__file__).resolve().parents[1] / "Schedule Preview and Deployment Confirmation.md"


def spec_names() -> list[str]:
    """The 12 names exactly as listed in the spec's preview table."""
    names = []
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].isdigit():
            names.append(cells[1])
    return names


def test_spec_table_parsed():
    names = spec_names()
    assert len(names) == 12
    assert names[0] == "Monthly Close - January 2027"
    assert names[-1] == "Monthly Close - December 2027"


def test_acceptance_jan_dec_2027_matches_spec_exactly():
    months = month_range(MonthRef(2027, 1), MonthRef(2027, 12))
    generated = [render("Monthly Close - {MMMM} {YYYY}", m) for m in months]
    assert generated == spec_names()


def test_names_change_with_year():
    assert render("Monthly Close - {MMMM} {YYYY}", MonthRef(2028, 3)) == "Monthly Close - March 2028"


def test_fccs_compact_pattern():
    assert render("FCCS_Close_{YYYY}_{MM}", MonthRef(2027, 1)) == "FCCS_Close_2027_01"
    assert render("FCCS_Close_{YYYY}_{MM}", MonthRef(2027, 12)) == "FCCS_Close_2027_12"


@pytest.mark.parametrize("month, abbr", [(1, "Jan"), (5, "May"), (9, "Sep"), (12, "Dec")])
def test_short_month(month, abbr):
    assert render("{MMM}", MonthRef(2027, month)) == abbr


@pytest.mark.parametrize("year, yy", [(2027, "27"), (2000, "00"), (2009, "09")])
def test_two_digit_year(year, yy):
    assert render("{YY}", MonthRef(year, 1)) == yy


def test_template_name_placeholder_is_trimmed():
    assert render("{TemplateName} - {MMM}{YY}", MonthRef(2027, 2), "  Monthly Close  ") == "Monthly Close - Feb27"


def test_all_placeholders_together():
    assert render("{TemplateName}|{MMMM}|{MMM}|{MM}|{YYYY}|{YY}", MonthRef(2027, 7), "T") == "T|July|Jul|07|2027|27"


def test_pattern_without_placeholders_is_literal():
    assert render("Static Name", MonthRef(2027, 1)) == "Static Name"


@pytest.mark.parametrize("pattern, unknown", [
    ("Close {Month}", ["Month"]),
    ("Close {mmmm}", ["mmmm"]),          # placeholders are case-sensitive
    ("Close { MMMM }", [" MMMM "]),
    ("Close {}", [""]),
    ("{Foo} {MMMM} {Bar}", ["Foo", "Bar"]),
])
def test_unknown_placeholders_detected_and_rejected(pattern, unknown):
    assert unknown_placeholders(pattern) == unknown
    with pytest.raises(ValueError, match="Unknown placeholder"):
        render(pattern, MonthRef(2027, 1))


def test_known_placeholders_not_reported():
    assert unknown_placeholders("{MMMM}{MMM}{MM}{YYYY}{YY}{TemplateName}") == []


def test_month_names_independent_of_locale(monkeypatch):
    german = ["", "Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember"]
    monkeypatch.setattr(calendar, "month_name", german)
    monkeypatch.setattr(calendar, "month_abbr", [n[:3] for n in german])
    old = locale.setlocale(locale.LC_TIME)
    for candidate in ("de_DE.UTF-8", "de_DE", "German_Germany.1252", "deu", "ru_RU.UTF-8", "Russian_Russia.1251"):
        try:
            locale.setlocale(locale.LC_TIME, candidate)
            break
        except locale.Error:
            continue
    try:
        months = month_range(MonthRef(2027, 1), MonthRef(2027, 12))
        assert [render("Monthly Close - {MMMM} {YYYY}", m) for m in months] == spec_names()
        assert render("{MMM}", MonthRef(2027, 3)) == "Mar"
        assert render("{MMM}", MonthRef(2027, 12)) == "Dec"
    finally:
        locale.setlocale(locale.LC_TIME, old)


def test_month_constants_are_english():
    assert naming_service.MONTH_NAMES[0] == "January"
    assert naming_service.MONTH_ABBREVIATIONS[-1] == "Dec"


# ---- validate_name -----------------------------------------------------------

RULES = {"max_length": 80, "forbidden_chars": "\\/:*?\"<>|", "max_months_per_run": 60}
FORBIDDEN = list("\\/:*?\"<>|")


def test_valid_name_has_no_problems():
    assert validate_name("Monthly Close - January 2027") == []


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_empty_name(name):
    assert validate_name(name, RULES) == ["Schedule name is empty."]


def test_length_boundary():
    assert validate_name("x" * 80, RULES) == []
    problems = validate_name("x" * 81, RULES)
    assert len(problems) == 1 and "longer than 80" in problems[0]


@pytest.mark.parametrize("ch", FORBIDDEN)
def test_each_forbidden_char(ch):
    problems = validate_name(f"Close {ch} 2027", RULES)
    assert len(problems) == 1
    assert "forbidden characters" in problems[0] and ch in problems[0]


def test_control_characters_rejected():
    assert any("control characters" in p for p in validate_name("Close\x01 2027", RULES))


def test_multiple_problems_reported_together():
    problems = validate_name("a/b" * 40, RULES)
    assert len(problems) == 2


def test_default_rules_loaded_from_config():
    assert validate_name("x" * 81)  # config max_length = 80
    assert validate_name("a:b")
