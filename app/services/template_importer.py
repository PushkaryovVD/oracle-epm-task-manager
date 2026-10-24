"""Inspection and validation of a Task Manager template file.

Supported inputs:
- .csv / .txt — Oracle Task Manager export "Unformatted / For Future Import" (the native import format);
- .xlsx / .xlsm — the same columns in a workbook, or a custom spreadsheet with recognisable headers.

The file is only read. Columns that are not recognised are reported, never silently dropped:
their values are kept in TaskRow.extra.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from app.models import Severity, ValidationIssue
from app.utils.config import column_catalogue

CSV_SUFFIXES = (".csv", ".txt")
EXCEL_SUFFIXES = (".xlsx", ".xlsm")
SUPPORTED_SUFFIXES = CSV_SUFFIXES + EXCEL_SUFFIXES
_CSV_ENCODINGS = ("utf-8-sig", "cp1252")
_TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")


class WorkbookKind:
    ORACLE_EXPORT = "Oracle Task Manager export"
    CUSTOM = "Custom spreadsheet"
    UNRECOGNISED = "Unrecognised workbook"


@dataclass
class ColumnMatch:
    key: str            # canonical field key, e.g. "approver"
    header: str         # header text as in the file
    column: int         # 1-based column index
    level: int = 0      # for multi fields: Approver1 -> 1


@dataclass
class TaskRow:
    row: int
    values: dict[str, object]                 # canonical key -> value (multi fields -> list, in column order)
    extra: dict[str, object] = field(default_factory=dict)  # unrecognised header -> value
    levels: dict[str, dict[int, object]] = field(default_factory=dict)  # multi fields by suffix number

    def level_value(self, key: str, level: int) -> object | None:
        return self.levels.get(key, {}).get(level)


@dataclass
class TemplateInspection:
    path: Path
    kind: str = WorkbookKind.UNRECOGNISED
    file_format: str = ""
    sheet: str | None = None
    header_row: int | None = None
    columns: list[ColumnMatch] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    tasks: list[TaskRow] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    template_name: str = ""

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def task_count(self) -> int:
        return len(self.tasks)


@dataclass
class _Sheet:
    """A table of cell values, independent of the file format."""

    title: str
    rows: list[list[object]]
    formula_cells: set[tuple[int, int]] = field(default_factory=set)  # (row, column), 1-based
    ws: object = None  # openpyxl worksheet, for Excel-only structural checks


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


class _Matcher:
    def __init__(self, catalogue: dict):
        self.fields = {f["key"]: f for f in catalogue["fields"]}
        self.alias_to_key: dict[str, str] = {}
        for f in catalogue["fields"]:
            for alias in f["aliases"]:
                self.alias_to_key.setdefault(_norm(alias), f["key"])

    def match(self, header: object) -> tuple[str, int] | None:
        norm = _norm(header)
        if not norm:
            return None
        if norm in self.alias_to_key:
            return self.alias_to_key[norm], 0
        # Multi-value fields: "Approver1", "Approver 2", "PredType1"...
        m = re.fullmatch(r"(.*?)(\d+)", norm)
        if m and m.group(1) in self.alias_to_key:
            key = self.alias_to_key[m.group(1)]
            if self.fields[key].get("multi"):
                return key, int(m.group(2))
        return None


# --------------------------------------------------------------------------- entry point
def inspect_workbook(path: str | Path) -> TemplateInspection:
    path = Path(path)
    result = TemplateInspection(path=path, template_name=path.stem.replace("_", " ").strip())
    catalogue = column_catalogue()
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_SUFFIXES:
        result.issues.append(ValidationIssue(
            Severity.ERROR, "Select a Task Manager export (.csv) or an Excel workbook (.xlsx)."))
        return result

    try:
        if suffix in CSV_SUFFIXES:
            result.file_format = "CSV"
            sheets = [_read_csv(path)]
        else:
            result.file_format = "Excel"
            sheets = _read_excel(path, result)
    except Exception as exc:  # corrupt, password-protected, locked by Excel, unreadable encoding...
        result.issues.append(ValidationIssue(Severity.ERROR, f"The file cannot be opened: {exc}"))
        return result

    matcher = _Matcher(catalogue)
    sheet = _locate_table(sheets, matcher, catalogue, result)
    if sheet is None:
        return result
    _read_rows(sheet, result, catalogue)
    _validate_tasks(result, catalogue)
    return result


# --------------------------------------------------------------------------- readers
def _read_csv(path: Path) -> _Sheet:
    raw = path.read_bytes()
    for encoding in _CSV_ENCODINGS:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # bytes undefined in cp1252 (0x81, 0x8D...)
        raise ValueError("unsupported text encoding; save the file as UTF-8")
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    # Oracle quotes with '"' only; apostrophes are data. newline="" keeps line breaks inside quoted values.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"', doublequote=True)
    return _Sheet(title=path.stem, rows=[list(r) for r in reader])


def _read_excel(path: Path, result: TemplateInspection) -> list[_Sheet]:
    wb = openpyxl.load_workbook(path, data_only=False)
    try:
        sheets = []
        hidden = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":
                hidden.append(ws.title)
                continue
            sheet = _Sheet(title=ws.title, rows=[], ws=ws)
            for row in ws.iter_rows():
                values = []
                for cell in row:
                    values.append(cell.value)
                    if cell.data_type == "f":
                        sheet.formula_cells.add((cell.row, cell.column))
                sheet.rows.append(values)
            sheets.append(sheet)
        if hidden:
            result.issues.append(ValidationIssue(Severity.INFO, "Hidden sheets were not read: " + ", ".join(hidden)))
        return sheets
    finally:
        wb.close()


def _excel_structure_checks(result: TemplateInspection, sheet: _Sheet) -> None:
    ws = sheet.ws
    if ws is None:
        return
    if ws.merged_cells.ranges:
        result.issues.append(ValidationIssue(
            Severity.WARNING, f"The sheet contains {len(ws.merged_cells.ranges)} merged cell range(s).", ws.title))
    hidden_cols = [m.header for m in result.columns if ws.column_dimensions[get_column_letter(m.column)].hidden]
    if hidden_cols:
        result.issues.append(ValidationIssue(
            Severity.WARNING, "Hidden columns are included in validation: " + ", ".join(hidden_cols), ws.title))


# --------------------------------------------------------------------------- table detection
def _locate_table(sheets: list[_Sheet], matcher: _Matcher, catalogue: dict, result: TemplateInspection) -> _Sheet | None:
    best = None  # (score, sheet, row, matches, unknown)
    for sheet in sheets:
        for r_idx, row in enumerate(sheet.rows[:catalogue["header_scan_rows"]], start=1):
            matches, unknown = [], []
            for c_idx, value in enumerate(row, start=1):
                if value is None or str(value).strip() == "":
                    continue
                hit = matcher.match(value)
                if hit:
                    matches.append(ColumnMatch(hit[0], str(value).strip(), c_idx, hit[1]))
                else:
                    unknown.append(str(value).strip())
            score = len({m.key for m in matches})
            if score >= 2 and (best is None or score > best[0]):
                best = (score, sheet, r_idx, matches, unknown)

    if best is None:
        result.issues.append(ValidationIssue(
            Severity.ERROR,
            "No task table found. The first rows must contain Task Manager column headers such as "
            "'TaskID', 'TaskName' and 'TaskType'. Export the template from Task Manager as "
            "'Unformatted data for future import'.",
        ))
        return None

    _, sheet, result.header_row, result.columns, result.unknown_columns = best
    result.sheet = sheet.title
    _excel_structure_checks(result, sheet)

    seen: dict[tuple[str, int], str] = {}
    for m in result.columns:
        if (m.key, m.level) in seen:
            result.issues.append(ValidationIssue(
                Severity.ERROR, f"Several columns map to the same field: {seen[(m.key, m.level)]}, {m.header}.",
                result.sheet, result.header_row))
        else:
            seen[(m.key, m.level)] = m.header

    keys = {m.key for m in result.columns}
    missing = [f["label"] for f in catalogue["fields"] if f.get("required") and f["key"] not in keys]
    for label in missing:
        result.issues.append(ValidationIssue(
            Severity.ERROR, f"Required column '{label}' is missing.", result.sheet, result.header_row))
    result.kind = WorkbookKind.ORACLE_EXPORT if not missing else WorkbookKind.CUSTOM

    if result.unknown_columns:
        result.issues.append(ValidationIssue(
            Severity.WARNING,
            "Columns not recognised as Task Manager attributes (kept and listed in the report, not sent to Oracle): "
            + ", ".join(result.unknown_columns),
            result.sheet, result.header_row,
        ))
    return sheet


def _read_rows(sheet: _Sheet, result: TemplateInspection, catalogue: dict) -> None:
    by_col = {m.column: m for m in result.columns}
    header = sheet.rows[result.header_row - 1]
    multi = {f["key"] for f in catalogue["fields"] if f.get("multi")}
    formula_cells = []
    headerless: dict[str, list[int]] = {}  # column letter -> rows with a value under an empty header

    for r_idx, row in enumerate(sheet.rows[result.header_row:], start=result.header_row + 1):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        task = TaskRow(row=r_idx, values={})
        for c_idx, value in enumerate(row, start=1):
            if isinstance(value, str):
                value = value.strip()
            if value == "" or value is None:
                continue
            if (r_idx, c_idx) in sheet.formula_cells:
                formula_cells.append(f"{get_column_letter(c_idx)}{r_idx}")
            match = by_col.get(c_idx)
            if match is None:
                head = header[c_idx - 1] if c_idx <= len(header) else None
                if head is None or str(head).strip() == "":
                    name = get_column_letter(c_idx)
                    headerless.setdefault(name, []).append(r_idx)
                else:
                    name = str(head).strip()
                task.extra[name] = value
            elif match.key in multi:
                task.values.setdefault(match.key, []).append(value)
                task.levels.setdefault(match.key, {})[match.level] = value
            else:
                task.values[match.key] = value
        result.tasks.append(task)

    if formula_cells:
        listed = ", ".join(formula_cells[:catalogue["max_issue_rows_listed"]])
        result.issues.append(ValidationIssue(
            Severity.WARNING,
            f"{len(formula_cells)} cell(s) contain formulas instead of values ({listed}). "
            "Oracle imports values only; paste values before importing.",
            result.sheet,
        ))
    for letter, rows in headerless.items():
        shown = ", ".join(map(str, rows[:catalogue["max_issue_rows_listed"]])) + (" ..." if len(rows) > 10 else "")
        result.issues.append(ValidationIssue(
            Severity.WARNING,
            f"Column {letter} has values but no header (rows {shown}); they are kept in the report, not sent to Oracle. "
            "Check for extra delimiters in these rows.",
            result.sheet,
        ))
    if not result.tasks:
        result.issues.append(ValidationIssue(Severity.ERROR, "The task table contains no tasks.", result.sheet))


# --------------------------------------------------------------------------- validation
def _split_refs(value: object, separators: str) -> list[str]:
    parts = re.split("[" + re.escape(separators) + "]", str(value))
    return [p.strip() for p in parts if p.strip()]


def _validate_tasks(result: TemplateInspection, catalogue: dict) -> None:
    sheet = result.sheet
    fields_by_key = {f["key"]: f for f in catalogue["fields"]}
    present = {m.key for m in result.columns}
    limit = catalogue["max_issue_rows_listed"]

    def add(severity, message, row=None, key=None, level=0):
        label = None
        if key:
            label = fields_by_key[key]["label"] + (str(level) if level else "")
        result.issues.append(ValidationIssue(severity, message, sheet, row, label))

    def cells(t: TaskRow, key: str):
        """(level, value) pairs of a field in a task; level 0 for single-value fields."""
        if fields_by_key[key].get("multi"):
            return sorted(t.levels.get(key, {}).items())
        return [(0, t.values[key])] if key in t.values else []

    # Required values per row
    for f in catalogue["fields"]:
        if f.get("required") and f["key"] in present:
            for t in result.tasks:
                if f["key"] not in t.values:
                    add(Severity.ERROR, f"'{f['label']}' is empty.", t.row, f["key"])

    # Unique task IDs
    first_row: dict[str, int] = {}
    for t in result.tasks:
        tid = t.values.get("task_id")
        if tid is None:
            continue
        key = str(tid).strip().casefold()
        if key in first_row:
            add(Severity.ERROR, f"Duplicate Task ID '{tid}' (first used in row {first_row[key]}).", t.row, "task_id")
        else:
            first_row[key] = t.row

    # Value formats: whole numbers (day offsets, minutes), times, allowed values
    for f in catalogue["fields"]:
        if f["key"] not in present:
            continue
        allowed = {a.casefold(): a for a in f.get("allowed", [])}
        for t in result.tasks:
            for level, value in cells(t, f["key"]):
                if f.get("integer") and not _is_int(value):
                    add(Severity.ERROR, f"'{f['label']}' must be a whole number, got '{value}'.", t.row, f["key"], level)
                elif f.get("time") and not _is_time(value):
                    add(Severity.ERROR, f"'{f['label']}' must be a time like 9:00 or 14:30, got '{value}'.",
                        t.row, f["key"], level)
                elif allowed and str(value).strip().casefold() not in allowed:
                    add(Severity.WARNING, f"'{value}' is not a known value for '{f['label']}' "
                        f"(expected {', '.join(f['allowed'])}).", t.row, f["key"], level)

    # Day offsets: the assignee/approver must not end before the task starts
    for t in result.tasks:
        start = _as_int(t.values.get("start_day_offset"))
        end = _as_int(t.values.get("end_day_offset"))
        if start is not None and end is not None and end < start:
            add(Severity.WARNING, f"Assignee end day ({end}) is before the start day ({start}).", t.row, "end_day_offset")
        for level, value in cells(t, "approver_end_day_offset") if "approver_end_day_offset" in present else []:
            approver_end = _as_int(value)
            if approver_end is not None and end is not None and approver_end < end:
                add(Severity.WARNING, f"Approver end day ({approver_end}) is before the assignee end day ({end}).",
                    t.row, "approver_end_day_offset", level)

    # Predecessors (within this template unless PredTemplate names another one) and parents must exist; no cycles
    ids = {str(t.values["task_id"]).strip().casefold(): t for t in result.tasks if "task_id" in t.values}
    graph: dict[str, list[str]] = {}
    external = 0
    for t in result.tasks:
        if "task_id" not in t.values:
            continue
        own = str(t.values["task_id"]).strip().casefold()
        refs = []
        for level, value in cells(t, "predecessor") if "predecessor" in present else []:
            if t.level_value("pred_template", level):
                external += 1  # predecessor lives in another template/schedule; Oracle resolves it
                continue
            refs.extend(_split_refs(value, catalogue["predecessor_separators"]))
        for ref in refs:
            if ref.casefold() not in ids:
                add(Severity.ERROR, f"Predecessor '{ref}' does not exist in this template.", t.row, "predecessor")
            elif ref.casefold() == own:
                add(Severity.ERROR, "A task cannot be its own predecessor.", t.row, "predecessor")
        graph[own] = [r.casefold() for r in refs if r.casefold() in ids and r.casefold() != own]
        parent = t.values.get("parent_task_id")
        if parent is not None and str(parent).strip().casefold() not in ids:
            add(Severity.ERROR, f"Parent task '{parent}' does not exist in this template.", t.row, "parent_task_id")
    if external:
        add(Severity.INFO, f"{external} predecessor(s) refer to tasks in other templates (PredTemplate); "
            "they are checked by Oracle, not by this application.")

    for cycle in _find_cycles(graph):
        names = " -> ".join(str(ids[c].values["task_id"]) for c in cycle)
        add(Severity.ERROR, f"Circular task dependency: {names}.", ids[cycle[0]].row, "predecessor")

    # Empty people fields (one aggregated warning per field)
    for key in ("owner", "assignee"):
        if key not in present:
            continue
        rows = [t.row for t in result.tasks if not t.values.get(key)]
        if rows:
            shown = ", ".join(map(str, rows[:limit])) + (" ..." if len(rows) > limit else "")
            add(Severity.WARNING, f"{len(rows)} task(s) have no {fields_by_key[key]['label'].lower()} (rows {shown}).",
                key=key)

    # Template name from a 'Template Name' column when it holds one value
    names = {str(t.values["template_name"]).strip() for t in result.tasks if t.values.get("template_name")}
    if len(names) == 1:
        result.template_name = names.pop()
    elif len(names) > 1:
        add(Severity.WARNING, "The 'Template Name' column contains different values: " + ", ".join(sorted(names)),
            key="template_name")

    result.issues.append(ValidationIssue(Severity.INFO, f"{result.task_count} task(s) found in '{sheet}'."))


def _is_int(value: object) -> bool:
    return _as_int(value) is not None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    return int(text) if re.fullmatch(r"[+-]?\d+", text) else None


def _is_time(value: object) -> bool:
    if hasattr(value, "hour") and hasattr(value, "minute"):  # datetime.time from Excel
        return True
    return bool(_TIME_RE.fullmatch(str(value).strip()))


def _find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Return one representative path per dependency cycle (iterative DFS)."""
    white, grey, black = 0, 1, 2
    color = {n: white for n in graph}
    cycles: list[list[str]] = []
    for root in graph:
        if color[root] != white:
            continue
        stack = [(root, iter(graph[root]))]
        path = [root]
        color[root] = grey
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                color[node] = black
                stack.pop()
                path.pop()
            elif color.get(child, black) == grey:
                cycles.append(path[path.index(child):] + [child])
            elif color.get(child) == white:
                color[child] = grey
                stack.append((child, iter(graph[child])))
                path.append(child)
    return cycles
