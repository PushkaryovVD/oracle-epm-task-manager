"""CSV (Oracle Task Manager 'Unformatted / For Future Import') parsing and the Oracle-format validations."""

from pathlib import Path

import pytest

from app.models import Severity
from app.services.template_importer import WorkbookKind, inspect_workbook

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "NA_Monthly_Close_sample.csv"

ORACLE_HEADER = (
    "TaskID,TaskName,TaskType,OrganizationalUnit,Description,Priority,StartDate,StartTime,Duration,"
    "MinimumDuration,Owner,Assignee,BackupAssignee,AssigneeEndDate,AssigneeEndTime,RequireCompletionBy,"
    "Active,Template,StartEarly,RunAs,Instruction,Predecessor1,PredType1,PredTemplate1,Approver1,"
    "ApproverBackup1,ApproverEndDate1,ApproverEndTime1,ApproverRequireCompletionBy1,QText1,QType1,QRole1,"
    "QReq1,QList1,QCurrSymb1,QDecPlaces1,QPercent1,QNegNumFmt1,QScale1,QThouSep1,QTextLines1,QInclAttach1,"
    "QAttachAccess1,Parameter1,ParamVal1,Parameter2,ParamVal2,Parent"
)


def messages(result, severity=None):
    return [i.message for i in result.issues if severity is None or i.severity == severity]


def issues_with(result, severity, text):
    return [i for i in result.issues if i.severity == severity and text in i.message]


@pytest.fixture
def make_csv(tmp_path):
    """Write text (or raw bytes) to tmp_path/name and return the path."""

    def _make(content, name="Close_Tasks.csv", encoding="utf-8") -> Path:
        path = tmp_path / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_bytes(content.encode(encoding))
        return path

    return _make


def csv_text(headers, rows, sep=","):
    lines = [sep.join(headers)] + [sep.join("" if v is None else str(v) for v in r) for r in rows]
    return "\n".join(lines) + "\n"


BASE = ["TaskID", "TaskName", "TaskType"]


def inspect(make_csv, headers, rows, **kw):
    return inspect_workbook(make_csv(csv_text(headers, rows), **kw))


# ---- the anonymised Oracle export in tests/fixtures ----------------------------------

@pytest.fixture(scope="module")
def sample():
    return inspect_workbook(FIXTURE)


def test_fixture_header_is_the_oracle_header():
    first_line = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == ORACLE_HEADER
    assert len(first_line.split(",")) == 48


def test_fixture_contains_no_real_mail_addresses():
    import re
    mails = set(re.findall(r"[\w.+-]+@[\w.-]+", FIXTURE.read_text(encoding="utf-8")))
    assert mails and all(m.endswith("@example.com") for m in mails)


def test_sample_parses_as_oracle_csv(sample):
    assert sample.file_format == "CSV"
    assert sample.kind == WorkbookKind.ORACLE_EXPORT
    assert sample.sheet == "NA_Monthly_Close_sample"
    assert sample.template_name == "NA Monthly Close sample"
    assert sample.header_row == 1
    assert sample.task_count == 6
    assert [t.values["task_id"] for t in sample.tasks] == [
        "ARC_002", "ARC_001", "NAWW_109", "NAWW_110", "NAWW_111", "ARC_003"]


def test_sample_all_48_headers_recognised(sample):
    assert sample.unknown_columns == []
    assert len(sample.columns) == 48
    assert [c.header for c in sample.columns] == ORACLE_HEADER.split(",")
    assert all(t.extra == {} for t in sample.tasks)
    # 'Template' is the template reference column, never the template name
    assert {c.key for c in sample.columns if c.header == "Template"} == {"template_ref"}


def test_sample_is_valid(sample):
    assert sample.is_valid, messages(sample, Severity.ERROR)
    assert sample.warnings and all("no assignee" in w.message for w in sample.warnings)


def test_sample_levels_are_paired_by_number(sample):
    close_period = sample.tasks[0]
    assert close_period.level_value("parameter", 1) == "period"
    assert close_period.level_value("parameter_value", 1) is None
    assert close_period.level_value("parameter", 2) == "status"
    assert close_period.level_value("parameter_value", 2) == "Closed"
    assert close_period.values["parameter_value"] == ["Closed"]  # list keeps column order, no gaps
    assert close_period.level_value("pred_type", 1) == "Finish to Start"
    assert close_period.level_value("predecessor", 1) == "ARC_001"

    verify = sample.tasks[1]
    assert verify.level_value("q_text", 1) == "Have all reconciliations been prepared and reviewed?"
    assert verify.level_value("q_type", 1) == "Yes/No"
    assert verify.level_value("q_role", 1) == "Assignee"
    assert verify.level_value("q_required", 1) == "Yes"
    assert verify.level_value("q_include_attachments", 1) == "No"
    assert verify.level_value("q_text", 2) is None

    aging = sample.tasks[2]
    assert aging.level_value("approver", 1) == "user3@example.com"
    assert aging.level_value("approver_end_day_offset", 1) == "5"
    assert aging.level_value("approver_end_time", 1) == "10:00"


def test_sample_values_are_unquoted_and_typed_as_text(sample):
    t = sample.tasks[0]
    assert t.values["task_type"] == "ARC_RC_CHANGE_PERIOD_STATUS"
    assert t.values["owner"] == "TM Admins"
    assert t.values["start_day_offset"] == "10"
    assert t.values["start_time"] == "14:00"
    assert t.values["active"] == "Yes"
    # quoted value with a comma stays one field
    assert sample.tasks[3].values["description"] == "Reconcile AR subledger to GL, sign off"
    assert sample.tasks[3].values["parent_task_id"] == "NAWW_109"


def test_sample_predecessors_resolved(sample):
    assert not [i for i in sample.issues if "Predecessor" in i.message and i.severity == Severity.ERROR]
    assert not [i for i in sample.issues if "Parent task" in i.message]
    # NAWW_111 -> GL_CLOSE_010 lives in another template: INFO, not an error
    info = issues_with(sample, Severity.INFO, "refer to tasks in other templates")
    assert len(info) == 1 and info[0].message.startswith("1 predecessor(s)")


def test_sample_warns_about_integration_tasks_without_assignee(sample):
    warn = issues_with(sample, Severity.WARNING, "have no assignee")
    assert len(warn) == 1
    assert warn[0].message == "2 task(s) have no assignee (rows 2, 7)."
    assert warn[0].column == "Assignee"
    assert not issues_with(sample, Severity.WARNING, "have no owner")
    assert not issues_with(sample, Severity.WARNING, "approver")


def test_sample_file_is_not_modified():
    before = FIXTURE.read_bytes()
    inspect_workbook(FIXTURE)
    assert FIXTURE.read_bytes() == before


def test_sample_with_crlf_line_endings(make_csv):
    text = FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    result = inspect_workbook(make_csv(text, name="NA_Monthly_Close_sample.csv"))
    assert result.is_valid, messages(result, Severity.ERROR)
    assert result.task_count == 6 and result.unknown_columns == []


# ---- CSV reading -----------------------------------------------------------------------

def test_cp1252_file_with_non_ascii(make_csv):
    text = csv_text(BASE + ["Description"], [["T1", "Café réconciliation", "Basic", "€ 100 – net"]])
    path = make_csv(text, encoding="cp1252")
    with pytest.raises(UnicodeDecodeError):
        path.read_bytes().decode("utf-8")  # really not utf-8
    result = inspect_workbook(path)
    assert result.is_valid, messages(result)
    assert result.tasks[0].values["task_name"] == "Café réconciliation"
    assert result.tasks[0].values["description"] == "€ 100 – net"


def test_utf8_with_bom(make_csv):
    path = make_csv("﻿" + csv_text(BASE, [["T1", "År", "Basic"]]))
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    result = inspect_workbook(path)
    assert result.is_valid, messages(result)
    assert result.unknown_columns == []
    assert result.columns[0].header == "TaskID" and result.columns[0].key == "task_id"
    assert result.tasks[0].values["task_name"] == "År"


def test_utf8_without_bom_non_ascii(make_csv):
    result = inspect_workbook(make_csv(csv_text(BASE, [["T1", "Проверка", "Basic"]])))
    assert result.tasks[0].values["task_name"] == "Проверка"


@pytest.mark.parametrize("sep,name", [(";", "semi.csv"), ("\t", "tabs.txt"), ("|", "pipe.csv")])
def test_other_delimiters(make_csv, sep, name):
    headers = BASE + ["Predecessor1", "Owner"]
    rows = [["T1", "Close, AP", "Basic", "", "o"], ["T2", "Load", "Basic", "T1", "o"]]
    result = inspect_workbook(make_csv(csv_text(headers, rows, sep=sep), name=name))
    assert result.is_valid, messages(result)
    assert result.unknown_columns == []
    assert result.task_count == 2
    assert result.tasks[0].values["task_name"] == "Close, AP"  # comma is data, not a delimiter
    assert result.tasks[1].level_value("predecessor", 1) == "T1"


def test_semicolon_file_with_comma_separated_predecessors(make_csv):
    text = csv_text(BASE + ["Predecessor1"], [["T1", "a", "B", ""], ["T2", "b", "B", ""], ["T3", "c", "B", "T1, T2"]],
                    sep=";")
    result = inspect_workbook(make_csv(text))
    assert result.is_valid, messages(result)
    assert result.tasks[2].values["predecessor"] == ["T1, T2"]


def test_quoted_value_containing_comma(make_csv):
    text = 'TaskID,TaskName,TaskType,Description\n"T1","Accruals, prepaid","Basic","say ""hi"", then go"\n'
    result = inspect_workbook(make_csv(text))
    assert result.is_valid, messages(result)
    assert result.unknown_columns == []
    v = result.tasks[0].values
    assert v["task_name"] == "Accruals, prepaid"
    assert v["description"] == 'say "hi", then go'
    assert result.tasks[0].extra == {}


def test_quoted_predecessor_list_with_comma(make_csv):
    text = 'TaskID,TaskName,TaskType,Predecessor1\nT1,a,B,\nT2,b,B,\nT3,c,B,"T1, T2"\n'
    result = inspect_workbook(make_csv(text))
    assert result.is_valid, messages(result)


def test_quoted_multiline_values_keep_newlines(make_csv):
    text = ('TaskID,TaskName,TaskType,Instruction,Predecessor1\nT1,a,B,,\nT2,b,B,,\n'
            'T3,c,B,"Step 1\nStep 2","T1\nT2"\n')
    result = inspect_workbook(make_csv(text))
    assert result.task_count == 3
    assert result.tasks[2].values["instructions"] == "Step 1\nStep 2"
    assert result.is_valid, messages(result)  # 'T1\nT2' is two predecessors (newline is a separator)


def test_apostrophes_are_data_not_quotes(make_csv):
    text = "TaskID,TaskName,TaskType,Description\nT1,a,B,'N/A'\nT2,b,B,x\n"
    result = inspect_workbook(make_csv(text))
    assert result.tasks[0].values["description"] == "'N/A'"


def test_quoted_multiline_value_does_not_split_the_row(make_csv):
    text = 'TaskID,TaskName,TaskType,Instruction\nT1,a,B,"Step 1\nStep 2"\nT2,b,B,\n'
    result = inspect_workbook(make_csv(text))
    assert result.task_count == 2
    assert [t.values["task_id"] for t in result.tasks] == ["T1", "T2"]


def test_csv_with_title_rows_above_header(make_csv):
    text = "NA Monthly Close\nExported 2026-10-01,by admin\n\n" + csv_text(BASE + ["Owner"], [["T1", "a", "Basic", "o"]])
    result = inspect_workbook(make_csv(text))
    assert result.is_valid, messages(result)
    assert result.header_row == 4
    assert result.tasks[0].row == 5
    assert result.unknown_columns == []


def test_empty_csv(make_csv):
    result = inspect_workbook(make_csv(""))
    assert not result.is_valid
    assert result.file_format == "CSV"
    assert result.kind == WorkbookKind.UNRECOGNISED
    assert any("No task table found" in m for m in messages(result, Severity.ERROR))
    assert result.tasks == []


def test_whitespace_only_csv(make_csv):
    result = inspect_workbook(make_csv("\n  \n\n"))
    assert any("No task table found" in m for m in messages(result, Severity.ERROR))


@pytest.mark.parametrize("header", [ORACLE_HEADER, "TaskID;TaskName;TaskType", "TaskID,TaskName,TaskType"])
def test_header_only_csv(make_csv, header):
    result = inspect_workbook(make_csv(header + "\n"))
    assert not result.is_valid
    assert result.header_row == 1
    assert result.unknown_columns == []
    assert any("contains no tasks" in m for m in messages(result, Severity.ERROR))


def test_csv_blank_lines_between_rows_skipped(make_csv):
    text = "TaskID,TaskName,TaskType\nT1,a,B\n,,\n\nT2,b,B\n"
    result = inspect_workbook(make_csv(text))
    assert [t.row for t in result.tasks] == [2, 5]


def test_csv_without_task_headers(make_csv):
    result = inspect_workbook(make_csv("a,b,c\n1,2,3\n"))
    assert any("No task table found" in m for m in messages(result, Severity.ERROR))


def test_undecodable_bytes_reported_not_raised(make_csv):
    # 0x81 / 0x8d are undefined in cp1252 and invalid utf-8
    result = inspect_workbook(make_csv(b"TaskID,TaskName,TaskType\nT1,\x81\x8d,B\n"))
    assert not result.is_valid
    assert any("cannot be opened" in m for m in messages(result, Severity.ERROR))


def test_old_aliases_still_match_in_csv(make_csv):
    headers = ["Task ID", "Task Name", "Task Type", "Start Day", "End Day", "Approver 1"]
    result = inspect(make_csv, headers, [["T1", "a", "B", "1", "2", "u"]])
    assert result.is_valid, messages(result)
    assert result.unknown_columns == []
    assert [c.key for c in result.columns] == [
        "task_id", "task_name", "task_type", "start_day_offset", "end_day_offset", "approver"]


# ---- whole numbers ---------------------------------------------------------------------

INTEGER_FIELDS = [("StartDate", "start_day_offset"), ("Duration", "duration"),
                  ("MinimumDuration", "minimum_duration"), ("AssigneeEndDate", "end_day_offset")]


@pytest.mark.parametrize("header,key", INTEGER_FIELDS)
@pytest.mark.parametrize("value", ["abc", "1.5", "1,5", "2d"])
def test_integer_fields_reject_non_integers(make_csv, header, key, value):
    text = csv_text(BASE + [header], [["T1", "a", "B", f'"{value}"']])
    result = inspect_workbook(make_csv(text))
    err = [i for i in result.errors if "whole number" in i.message]
    assert len(err) == 1
    assert err[0].message == f"'{header}' must be a whole number, got '{value}'."
    assert err[0].column == header and err[0].row == 2


@pytest.mark.parametrize("header,key", INTEGER_FIELDS)
@pytest.mark.parametrize("value", ["0", "-3", "+4", "15", " 7 "])
def test_integer_fields_accept_integers(make_csv, header, key, value):
    result = inspect(make_csv, BASE + [header], [["T1", "a", "B", value]])
    assert not [i for i in result.errors if "whole number" in i.message], messages(result)


def test_approver_end_date_n_must_be_integer(make_csv):
    headers = BASE + ["Approver1", "ApproverEndDate1", "Approver2", "ApproverEndDate2"]
    result = inspect(make_csv, headers, [["T1", "a", "B", "u1", "3", "u2", "x"]])
    err = [i for i in result.errors if "whole number" in i.message]
    assert len(err) == 1
    assert err[0].message == "'ApproverEndDate' must be a whole number, got 'x'."
    assert err[0].column == "ApproverEndDate2"


def test_empty_integer_cell_is_not_an_error(make_csv):
    result = inspect(make_csv, BASE + ["StartDate", "Duration"], [["T1", "a", "B", "", ""]])
    assert result.is_valid, messages(result)


# ---- times -----------------------------------------------------------------------------

TIME_FIELDS = ["StartTime", "AssigneeEndTime"]


@pytest.mark.parametrize("header", TIME_FIELDS)
@pytest.mark.parametrize("value", ["24:00", "9:60", "9.00", "900", "9:00 AM", "abc", "25:00", "9:5"])
def test_time_fields_reject_bad_times(make_csv, header, value):
    result = inspect(make_csv, BASE + [header], [["T1", "a", "B", value]])
    err = [i for i in result.errors if "must be a time" in i.message]
    assert len(err) == 1
    assert err[0].message == f"'{header}' must be a time like 9:00 or 14:30, got '{value}'."
    assert err[0].column == header and err[0].row == 2


@pytest.mark.parametrize("header", TIME_FIELDS)
@pytest.mark.parametrize("value", ["0:00", "9:00", "09:00", "13:30", "23:59"])
def test_time_fields_accept_valid_times(make_csv, header, value):
    result = inspect(make_csv, BASE + [header], [["T1", "a", "B", value]])
    assert result.is_valid, messages(result)


def test_approver_end_time_n_validated_with_number_in_column(make_csv):
    headers = BASE + ["Approver1", "ApproverEndTime1", "Approver2", "ApproverEndTime2"]
    result = inspect(make_csv, headers, [["T1", "a", "B", "u1", "10:00", "u2", "10.30"]])
    err = [i for i in result.errors if "must be a time" in i.message]
    assert len(err) == 1 and err[0].column == "ApproverEndTime2"
    assert "'10.30'" in err[0].message


def test_excel_time_cell_accepted(make_workbook):
    import datetime
    path = make_workbook(headers=BASE + ["StartTime"], rows=[["T1", "a", "B", datetime.time(9, 30)]])
    result = inspect_workbook(path)
    assert result.is_valid, messages(result)


# ---- allowed values (warnings, case-insensitive) ---------------------------------------

ALLOWED = [
    ("Priority", ["Low", "Medium", "High"], "Urgent"),
    ("Active", ["Yes", "No"], "Y"),
    ("StartEarly", ["Yes", "No"], "True"),
    ("QReq1", ["Yes", "No"], "maybe"),
    ("PredType1", ["Finish to Start", "Finish to Finish", "Finish Error to Start", "Finish Error to Finish"], "FS"),
]


@pytest.mark.parametrize("header,allowed,bad", ALLOWED)
def test_allowed_values_unknown_value_is_a_warning(make_csv, header, allowed, bad):
    result = inspect(make_csv, BASE + [header], [["T1", "a", "B", bad]])
    assert result.is_valid, messages(result)  # never an error
    warn = [i for i in result.warnings if "is not a known value" in i.message]
    assert len(warn) == 1
    label = header.rstrip("1")
    assert warn[0].message == f"'{bad}' is not a known value for '{label}' (expected {', '.join(allowed)})."
    assert warn[0].column == header and warn[0].row == 2


@pytest.mark.parametrize("header,allowed,bad", ALLOWED)
def test_allowed_values_pass_in_any_case(make_csv, header, allowed, bad):
    rows = []
    for i, v in enumerate(allowed):
        rows += [[f"A{i}", "a", "B", v], [f"L{i}", "a", "B", v.lower()], [f"U{i}", "a", "B", f" {v.upper()} "]]
    result = inspect(make_csv, BASE + [header], rows)
    assert result.is_valid, messages(result)
    assert not [i for i in result.warnings if "is not a known value" in i.message]


def test_allowed_value_checked_per_level(make_csv):
    headers = BASE + ["Predecessor1", "PredType1", "Predecessor2", "PredType2"]
    rows = [["T1", "a", "B", "", "", "", ""], ["T2", "b", "B", "", "", "", ""],
            ["T3", "c", "B", "T1", "finish to start", "T2", "Start to Start"]]
    result = inspect(make_csv, headers, rows)
    warn = [i for i in result.warnings if "is not a known value" in i.message]
    assert len(warn) == 1 and warn[0].column == "PredType2" and warn[0].row == 4


# ---- end-before-start warnings ---------------------------------------------------------

def test_assignee_end_before_start_warns(make_csv):
    result = inspect(make_csv, BASE + ["StartDate", "AssigneeEndDate"],
                     [["T1", "a", "B", "5", "3"], ["T2", "b", "B", "3", "3"], ["T3", "c", "B", "-2", "-1"]])
    assert result.is_valid
    warn = [i for i in result.warnings if "before the start day" in i.message]
    assert len(warn) == 1
    assert warn[0].message == "Assignee end day (3) is before the start day (5)."
    assert warn[0].row == 2 and warn[0].column == "AssigneeEndDate"


def test_negative_offsets_compared_numerically(make_csv):
    result = inspect(make_csv, BASE + ["StartDate", "AssigneeEndDate"], [["T1", "a", "B", "-1", "-10"]])
    assert [i.message for i in result.warnings if "before the start day" in i.message] == [
        "Assignee end day (-10) is before the start day (-1)."]


def test_approver_end_before_assignee_end_warns(make_csv):
    headers = BASE + ["AssigneeEndDate", "Approver1", "ApproverEndDate1", "Approver2", "ApproverEndDate2"]
    result = inspect(make_csv, headers, [["T1", "a", "B", "5", "u1", "5", "u2", "4"]])
    assert result.is_valid
    warn = [i for i in result.warnings if "before the assignee end day" in i.message]
    assert len(warn) == 1
    assert warn[0].message == "Approver end day (4) is before the assignee end day (5)."
    assert warn[0].column == "ApproverEndDate2"


def test_no_date_warning_when_values_missing_or_invalid(make_csv):
    headers = BASE + ["StartDate", "AssigneeEndDate", "ApproverEndDate1"]
    rows = [["T1", "a", "B", "", "1", "0"], ["T2", "b", "B", "x", "1", ""]]
    result = inspect(make_csv, headers, rows)
    assert not [i for i in result.warnings if "before the start day" in i.message]
    # ApproverEndDate1=0 < AssigneeEndDate=1 is still compared
    assert len([i for i in result.warnings if "before the assignee end day" in i.message]) == 1


# ---- predecessors in other templates ---------------------------------------------------

def test_pred_template_predecessor_not_checked_and_counted_in_info(make_csv):
    headers = BASE + ["Predecessor1", "PredTemplate1", "Predecessor2", "PredTemplate2"]
    rows = [["T1", "a", "B", "X_1", "Other Template", "", ""],
            ["T2", "b", "B", "T1", "", "X_2", "Other Template"]]
    result = inspect(make_csv, headers, rows)
    assert result.is_valid, messages(result)
    info = [i.message for i in result.issues if i.severity == Severity.INFO and "other templates" in i.message]
    assert len(info) == 1 and info[0].startswith("2 predecessor(s)")


def test_pred_template_on_other_level_does_not_exempt_predecessor(make_csv):
    headers = BASE + ["Predecessor1", "PredTemplate1", "Predecessor2", "PredTemplate2"]
    rows = [["T1", "a", "B", "MISSING", "", "X_2", "Other Template"]]
    result = inspect(make_csv, headers, rows)
    errs = [i.message for i in result.errors if "does not exist" in i.message]
    assert errs == ["Predecessor 'MISSING' does not exist in this template."]


def test_no_info_without_external_predecessors(make_csv):
    result = inspect(make_csv, BASE + ["Predecessor1", "PredTemplate1"], [["T1", "a", "B", "", ""]])
    assert not [m for m in messages(result) if "other templates" in m]


def test_missing_predecessor_without_pred_template_is_error(make_csv):
    result = inspect(make_csv, BASE + ["Predecessor1", "PredTemplate1"], [["T1", "a", "B", "T9", ""]])
    assert "Predecessor 'T9' does not exist in this template." in messages(result, Severity.ERROR)


# ---- duplicate columns per (field, number) ---------------------------------------------

def test_duplicate_approver1_columns_error(make_csv):
    headers = BASE + ["Approver1", "Approver1"]
    result = inspect(make_csv, headers, [["T1", "a", "B", "u1", "u2"]])
    assert not result.is_valid
    assert "Several columns map to the same field: Approver1, Approver1." in messages(result, Severity.ERROR)


def test_approver1_and_approver2_are_not_duplicates(make_csv):
    result = inspect(make_csv, BASE + ["Approver1", "Approver 2"], [["T1", "a", "B", "u1", "u2"]])
    assert result.is_valid, messages(result)
    assert result.tasks[0].level_value("approver", 2) == "u2"


def test_alias_spellings_of_same_numbered_field_are_duplicates(make_csv):
    result = inspect(make_csv, BASE + ["PredType1", "Pred Type 1"], [["T1", "a", "B", "", ""]])
    assert any("Several columns map to the same field: PredType1, Pred Type 1." == m
               for m in messages(result, Severity.ERROR))


# ---- Template vs Template Name ---------------------------------------------------------

def test_template_column_is_not_the_template_name(make_csv):
    result = inspect(make_csv, BASE + ["Template"], [["T1", "a", "B", "Global Close"], ["T2", "b", "B", "Global Close"]],
                     name="NA_Close.csv")
    assert result.is_valid, messages(result)
    assert result.template_name == "NA Close"
    assert result.tasks[0].values["template_ref"] == "Global Close"
    assert "template_name" not in result.tasks[0].values


def test_template_column_with_different_values_does_not_warn(make_csv):
    result = inspect(make_csv, BASE + ["Template"], [["T1", "a", "B", "X"], ["T2", "b", "B", "Y"]])
    assert not [m for m in messages(result) if "Template Name" in m]


def test_template_name_column_sets_name_and_template_is_ignored(make_csv):
    result = inspect(make_csv, BASE + ["Template", "Template Name"],
                     [["T1", "a", "B", "Ref A", "Monthly Close"], ["T2", "b", "B", "Ref B", "Monthly Close"]])
    assert result.template_name == "Monthly Close"
    assert result.unknown_columns == []


# ---- empty owner / assignee ------------------------------------------------------------

def test_empty_owner_and_assignee_aggregated_but_not_approver(make_csv):
    headers = BASE + ["Owner", "Assignee", "Approver1"]
    rows = [["T1", "a", "B", "", "", ""], ["T2", "b", "B", "o", "", ""], ["T3", "c", "B", "o", "x", ""]]
    result = inspect(make_csv, headers, rows)
    assert result.is_valid
    assert "1 task(s) have no owner (rows 2)." in messages(result, Severity.WARNING)
    assert "2 task(s) have no assignee (rows 2, 3)." in messages(result, Severity.WARNING)
    assert not [m for m in messages(result) if "approver" in m.lower()]


def test_people_warning_lists_at_most_ten_rows(make_csv):
    rows = [[f"T{i}", "a", "B", ""] for i in range(12)]
    result = inspect(make_csv, BASE + ["Assignee"], rows)
    warn = [m for m in messages(result, Severity.WARNING) if "no assignee" in m]
    assert warn == ["12 task(s) have no assignee (rows 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 ...)."]


def test_no_people_warning_when_column_absent(make_csv):
    result = inspect(make_csv, BASE, [["T1", "a", "B"]])
    assert not result.warnings


def test_value_under_empty_header_is_reported(make_csv):
    result = inspect_workbook(make_csv("TaskID,TaskName,TaskType\nT1,a,B,surplus\nT2,b,B\n"))
    assert result.tasks[0].extra == {"D": "surplus"}
    assert any(i.severity == Severity.WARNING and "Column D has values but no header (rows 2)" in i.message
               for i in result.issues)
