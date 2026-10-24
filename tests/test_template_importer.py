import pytest

from app.models import Severity
from app.services.template_importer import WorkbookKind, inspect_workbook
from tests.conftest import STANDARD_HEADERS, standard_rows

H = ["Task ID", "Task Name", "Task Type", "Predecessor"]


def messages(result, severity=None):
    return [i.message for i in result.issues if severity is None or i.severity == severity]


def has_error(result, text):
    return any(text in m for m in messages(result, Severity.ERROR))


def has_warning(result, text):
    return any(text in m for m in messages(result, Severity.WARNING))


# ---- happy path -------------------------------------------------------------

def test_valid_workbook(make_workbook):
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=standard_rows()))
    assert result.is_valid, messages(result)
    assert result.kind == WorkbookKind.ORACLE_EXPORT
    assert result.task_count == 3
    assert result.sheet == "Tasks"
    assert result.header_row == 1
    assert result.template_name == "Monthly Close Template"  # from file name, underscores -> spaces
    assert result.unknown_columns == []
    first = result.tasks[0]
    assert first.row == 2
    assert first.values["task_id"] == "T001"
    assert first.values["approver"] == ["controller"]
    assert first.values["start_day_offset"] == -2
    assert any("3 task(s) found in 'Tasks'" in m for m in messages(result, Severity.INFO))


def test_sample_workbook_in_repo_is_valid():
    from pathlib import Path
    sample = Path(__file__).resolve().parents[1] / "samples" / "Monthly_Close_Template.xlsx"
    result = inspect_workbook(sample)
    assert result.is_valid, messages(result)
    assert result.task_count == 5


def test_template_name_from_single_valued_column(make_workbook):
    rows = [r + ["Monthly Financial Close"] for r in standard_rows()]
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS + ["Template Name"], rows=rows))
    assert result.is_valid
    assert result.template_name == "Monthly Financial Close"


def test_template_name_column_with_different_values_warns(make_workbook):
    rows = [r + [f"Template {i}"] for i, r in enumerate(standard_rows())]
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS + ["Template Name"], rows=rows))
    assert result.template_name == "Monthly Close Template"  # falls back to file name
    assert has_warning(result, "'Template Name' column contains different values")


def test_header_not_in_first_row(make_workbook):
    path = make_workbook(
        title_rows=[["Monthly Close Template"], ["Exported 2027-01-01"], []],
        headers=STANDARD_HEADERS, rows=standard_rows())
    result = inspect_workbook(path)
    assert result.is_valid, messages(result)
    assert result.header_row == 4
    assert result.task_count == 3
    assert result.tasks[0].row == 5


def test_headers_matched_by_alias_and_punctuation(make_workbook):
    path = make_workbook(headers=["task-id", "NAME", "Task type"], rows=[["A", "a", "Basic"]])
    result = inspect_workbook(path)
    assert result.is_valid, messages(result)
    assert result.tasks[0].values == {"task_id": "A", "task_name": "a", "task_type": "Basic"}


# ---- structural errors ------------------------------------------------------

def test_missing_required_column(make_workbook):
    path = make_workbook(headers=["Task ID", "Task Name", "Owner"], rows=[["T1", "n", "o"]])
    result = inspect_workbook(path)
    assert not result.is_valid
    assert result.kind == WorkbookKind.CUSTOM
    assert has_error(result, "Required column 'TaskType' is missing.")


def test_empty_required_cell(make_workbook):
    rows = standard_rows()
    rows[1][1] = None      # Task Name of T002
    rows[2][2] = "   "     # Task Type of T003 (whitespace only)
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=rows))
    errors = [i for i in result.errors if "is empty" in i.message]
    assert {(i.row, i.column) for i in errors} == {(3, "TaskName"), (4, "TaskType")}


def test_duplicate_task_id_case_insensitive(make_workbook):
    path = make_workbook(headers=H, rows=[["T1", "a", "Basic", None], ["t1", "b", "Basic", None]])
    result = inspect_workbook(path)
    dup = [i for i in result.errors if "Duplicate Task ID" in i.message]
    assert len(dup) == 1
    assert dup[0].row == 3 and "first used in row 2" in dup[0].message


def test_predecessor_not_existing(make_workbook):
    path = make_workbook(headers=H, rows=[["T1", "a", "Basic", None], ["T2", "b", "Basic", "T9"]])
    result = inspect_workbook(path)
    assert has_error(result, "Predecessor 'T9' does not exist")


def test_self_predecessor(make_workbook):
    path = make_workbook(headers=H, rows=[["T1", "a", "Basic", "t1"]])
    result = inspect_workbook(path)
    assert has_error(result, "cannot be its own predecessor")
    assert not has_error(result, "Circular")


def test_circular_dependency_reported(make_workbook):
    rows = [["A", "a", "Basic", "C"], ["B", "b", "Basic", "A"], ["C", "c", "Basic", "B"], ["D", "d", "Basic", "A"]]
    result = inspect_workbook(make_workbook(headers=H, rows=rows))
    cycles = [i.message for i in result.errors if "Circular task dependency" in i.message]
    assert len(cycles) == 1
    for tid in ("A", "B", "C"):
        assert tid in cycles[0]
    assert "D" not in cycles[0].replace("dependency", "")


def test_no_cycle_for_diamond(make_workbook):
    rows = [["A", "a", "Basic", None], ["B", "b", "Basic", "A"], ["C", "c", "Basic", "A"], ["D", "d", "Basic", "B, C"]]
    result = inspect_workbook(make_workbook(headers=H, rows=rows))
    assert result.is_valid, messages(result)


@pytest.mark.parametrize("value", ["T1, T2", "T1;T2", "T1\nT2", " t1 ; T2 "])
def test_multiple_predecessors(make_workbook, value):
    rows = [["T1", "a", "Basic", None], ["T2", "b", "Basic", None], ["T3", "c", "Basic", value]]
    result = inspect_workbook(make_workbook(headers=H, rows=rows))
    assert result.is_valid, messages(result)


def test_multiple_predecessors_one_missing(make_workbook):
    rows = [["T1", "a", "Basic", None], ["T3", "c", "Basic", "T1;T2"]]
    result = inspect_workbook(make_workbook(headers=H, rows=rows))
    assert has_error(result, "Predecessor 'T2' does not exist")
    assert not has_error(result, "Predecessor 'T1'")


def test_approver_multi_columns_collected(make_workbook):
    headers = ["Task ID", "Task Name", "Task Type", "Approver1", "Approver 2", "Approver3"]
    rows = [["T1", "a", "Basic", "anna", "bob", None]]
    result = inspect_workbook(make_workbook(headers=headers, rows=rows))
    assert result.is_valid, messages(result)
    assert result.tasks[0].values["approver"] == ["anna", "bob"]
    assert [c.level for c in result.columns if c.key == "approver"] == [1, 2, 3]


def test_predecessor_multi_columns(make_workbook):
    headers = ["Task ID", "Task Name", "Task Type", "Predecessor 1", "Predecessor 2"]
    rows = [["T1", "a", "Basic", None, None], ["T2", "b", "Basic", None, None], ["T3", "c", "Basic", "T1", "T2"]]
    result = inspect_workbook(make_workbook(headers=headers, rows=rows))
    assert result.is_valid, messages(result)
    assert result.tasks[2].values["predecessor"] == ["T1", "T2"]


@pytest.mark.parametrize("value", ["abc", 2.5, "1.5", "two"])
def test_non_integer_start_day(make_workbook, value):
    headers = ["Task ID", "Task Name", "Task Type", "Start Day"]
    result = inspect_workbook(make_workbook(headers=headers, rows=[["T1", "a", "Basic", value]]))
    err = [i for i in result.errors if "whole number" in i.message]
    assert len(err) == 1 and err[0].column == "StartDate" and err[0].row == 2
    assert err[0].message == f"'StartDate' must be a whole number, got '{value}'."


@pytest.mark.parametrize("value", [2.0, -3, "4", "-1", 0])
def test_integer_like_start_day_accepted(make_workbook, value):
    headers = ["Task ID", "Task Name", "Task Type", "Start Day"]
    result = inspect_workbook(make_workbook(headers=headers, rows=[["T1", "a", "Basic", value]]))
    assert result.is_valid, messages(result)


def test_parent_task_missing(make_workbook):
    headers = ["Task ID", "Task Name", "Task Type", "Parent Task"]
    rows = [["T1", "a", "Basic", None], ["T2", "b", "Basic", "T1"], ["T3", "c", "Basic", "P9"]]
    result = inspect_workbook(make_workbook(headers=headers, rows=rows))
    errs = [i for i in result.errors if "Parent task" in i.message]
    assert len(errs) == 1 and "'P9'" in errs[0].message and errs[0].row == 4


def test_several_columns_mapping_to_same_field(make_workbook):
    path = make_workbook(headers=["Task ID", "ID", "Task Name", "Task Type"], rows=[["T1", "T1", "a", "Basic"]])
    result = inspect_workbook(path)
    assert has_error(result, "Several columns map to the same field")


# ---- warnings, never-drop -----------------------------------------------------

def test_unknown_columns_warned_and_values_preserved(make_workbook):
    headers = STANDARD_HEADERS + ["Cost Center", "Region Code"]
    rows = [r + ["100", "EMEA"] for r in standard_rows()]
    rows[2][-2] = None  # empty unknown cell -> simply absent
    result = inspect_workbook(make_workbook(headers=headers, rows=rows))
    assert result.is_valid
    assert result.unknown_columns == ["Cost Center", "Region Code"]
    assert has_warning(result, "Cost Center") and has_warning(result, "Region Code")
    assert result.tasks[0].extra == {"Cost Center": "100", "Region Code": "EMEA"}
    assert result.tasks[2].extra == {"Region Code": "EMEA"}


def test_value_under_empty_header_kept_by_column_letter(make_workbook):
    headers = ["Task ID", "Task Name", "Task Type", None, "Note"]
    result = inspect_workbook(make_workbook(headers=headers, rows=[["T1", "a", "Basic", "orphan", "n"]]))
    assert result.tasks[0].extra == {"D": "orphan", "Note": "n"}


def test_formulas_warning(make_workbook):
    headers = STANDARD_HEADERS + ["Description"]
    rows = [r + [None] for r in standard_rows()]
    rows[0][-1] = '="Close "&"AP"'
    rows[1][-1] = "=1+1"
    result = inspect_workbook(make_workbook(headers=headers, rows=rows))
    warn = [i.message for i in result.warnings if "formulas" in i.message]
    assert len(warn) == 1
    assert "2 cell(s)" in warn[0] and "J2" in warn[0] and "J3" in warn[0]


def test_merged_cells_warning(make_workbook):
    def merge(wb, ws):
        ws.merge_cells("A1:D1")
    path = make_workbook(title_rows=[["Monthly Close Template"]], headers=STANDARD_HEADERS,
                         rows=standard_rows(), customize=merge)
    result = inspect_workbook(path)
    assert result.header_row == 2
    assert has_warning(result, "1 merged cell range")
    assert result.is_valid


def test_hidden_sheet_ignored_with_info(make_workbook):
    def add_hidden(wb, ws):
        hidden = wb.create_sheet("Old Tasks")
        hidden.append(STANDARD_HEADERS + ["Description", "Priority", "Instructions"])
        hidden.append(["X1", "x", "Basic"])
        hidden.sheet_state = "hidden"
    path = make_workbook(headers=STANDARD_HEADERS, rows=standard_rows(), customize=add_hidden)
    result = inspect_workbook(path)
    # The hidden sheet has a better-scoring header but must not be read.
    assert result.sheet == "Tasks"
    assert result.task_count == 3
    info = [i.message for i in result.issues if i.severity == Severity.INFO]
    assert any("Hidden sheets were not read: Old Tasks" in m for m in info)


def test_hidden_columns_warning(make_workbook):
    def hide(wb, ws):
        ws.column_dimensions["D"].hidden = True
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=standard_rows(), customize=hide))
    assert has_warning(result, "Hidden columns are included in validation: Owner")


def test_empty_people_fields_aggregated_warning(make_workbook):
    rows = standard_rows()
    rows[0][4] = None  # assignee of T001
    rows[2][4] = None
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=rows))
    assert result.is_valid
    assert has_warning(result, "2 task(s) have no assignee (rows 2, 4)")


def test_blank_rows_between_tasks_skipped(make_workbook):
    rows = standard_rows()
    rows.insert(1, [None] * len(STANDARD_HEADERS))
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=rows))
    assert result.task_count == 3
    assert [t.row for t in result.tasks] == [2, 4, 5]


# ---- unusable files ---------------------------------------------------------

def test_empty_workbook(make_workbook):
    result = inspect_workbook(make_workbook(name="empty.xlsx"))
    assert not result.is_valid
    assert has_error(result, "No task table found")
    assert result.kind == WorkbookKind.UNRECOGNISED
    assert result.tasks == []


def test_workbook_without_table(make_workbook):
    path = make_workbook(name="notes.xlsx", title_rows=[["Some notes"], ["more", "text"], [1, 2, 3]])
    result = inspect_workbook(path)
    assert has_error(result, "No task table found")


def test_headers_without_tasks(make_workbook):
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=[]))
    assert has_error(result, "contains no tasks")


def test_table_only_on_hidden_sheet_is_not_found(make_workbook):
    def hide_main(wb, ws):
        wb.create_sheet("Cover").append(["Cover page"])
        ws.sheet_state = "hidden"
    result = inspect_workbook(make_workbook(headers=STANDARD_HEADERS, rows=standard_rows(), customize=hide_main))
    assert has_error(result, "No task table found")


@pytest.mark.parametrize("name", ["tasks.xls", "tasks"])
def test_unsupported_file_type(tmp_path, name):
    path = tmp_path / name
    path.write_text("Task ID,Task Name,Task Type\nT1,a,Basic\n", encoding="utf-8")
    result = inspect_workbook(path)
    assert not result.is_valid
    assert messages(result, Severity.ERROR) == [
        "Select a Task Manager export (.csv) or an Excel workbook (.xlsx)."]
    assert result.tasks == [] and result.file_format == ""


@pytest.mark.parametrize("name", ["tasks.csv", "tasks.txt", "TASKS.CSV"])
def test_csv_and_txt_are_supported(tmp_path, name):
    path = tmp_path / name
    path.write_text("Task ID,Task Name,Task Type\nT1,a,Basic\n", encoding="utf-8")
    result = inspect_workbook(path)
    assert result.is_valid, messages(result)
    assert result.file_format == "CSV"
    assert result.sheet == path.stem
    assert result.tasks[0].values == {"task_id": "T1", "task_name": "a", "task_type": "Basic"}


def test_corrupt_xlsx(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is not a zip archive")
    result = inspect_workbook(path)
    assert not result.is_valid
    assert has_error(result, "cannot be opened")


def test_missing_file(tmp_path):
    result = inspect_workbook(tmp_path / "does_not_exist.xlsx")
    assert has_error(result, "cannot be opened")


def test_workbook_is_not_modified(make_workbook):
    path = make_workbook(headers=STANDARD_HEADERS, rows=standard_rows())
    before = path.read_bytes()
    inspect_workbook(path)
    assert path.read_bytes() == before
