"""Build the FSSG deliverables from fssg_tasks.py.

    python deliverables/fssg/build_fssg_catalog.py --assignee <Oracle user login>

Outputs (next to this script):
  FSSG_EPM_Operational_Task_Catalog.xlsx   human-readable catalog
  FSSG_Daily_Operations_TM_Import.csv      Task Manager import, daily template
  FSSG_Monthly_Close_TM_Import.csv         Task Manager import, monthly template
"""
import argparse
import csv
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fssg_tasks import DAILY, DRAFT_REVIEW, MONTHLY, NO_TAIL  # noqa: E402

# Exact header of FSSG_Oracle_EPM_Task_Manager_Draft.csv (48 columns, order preserved).
TM_HEADER = (
    "TaskID,TaskName,TaskType,OrganizationalUnit,Description,Priority,StartDate,StartTime,Duration,"
    "MinimumDuration,Owner,Assignee,BackupAssignee,AssigneeEndDate,AssigneeEndTime,RequireCompletionBy,"
    "Active,Template,StartEarly,RunAs,Instruction,Predecessor1,PredType1,PredTemplate1,Approver1,"
    "ApproverBackup1,ApproverEndDate1,ApproverEndTime1,ApproverRequireCompletionBy1,QText1,QType1,QRole1,"
    "QReq1,QList1,QCurrSymb1,QDecPlaces1,QPercent1,QNegNumFmt1,QScale1,QThouSep1,QTextLines1,QInclAttach1,"
    "QAttachAccess1,Parameter1,ParamVal1,Parameter2,ParamVal2,Parent"
).split(",")
assert len(TM_HEADER) == 48

# Values every BasicTaskType row carries in the customer's real Task Manager export
# (tests/fixtures/NA_Monthly_Close_sample.csv); Oracle rejected the import without them.
TM_DEFAULTS = {
    "Owner": "TM Admins", "StartDate": "0", "StartTime": "09:00", "MinimumDuration": "0",
    "AssigneeEndDate": "0", "AssigneeEndTime": "17:00", "StartEarly": "Yes",
}
ASSIGNEE = ""  # set from --assignee in main()


# ---------------------------------------------------------------- CSV
def tm_instruction(t):
    steps = " ".join(f"{i}) {s}" for i, s in enumerate(t["steps"], 1))
    return (f"Steps: {steps} Expected result: {t['expected']} Evidence: {t['evidence']} "
            f"If not successful: {t['failure']} {NO_TAIL}")


def tm_description(t):
    return f"{t['purpose']} FSSG: {t['fssg']} Finance: {t['finance']}"


def tm_row(t):
    row = dict.fromkeys(TM_HEADER, "")
    row.update(TM_DEFAULTS)
    row.update({
        "Assignee": ASSIGNEE, "QInclAttach1": "No",
        "TaskID": t["id"], "TaskName": t["name"], "TaskType": "BasicTaskType",
        "Description": tm_description(t), "Priority": t["priority"], "Active": "Yes",
        "Instruction": tm_instruction(t),
        "Predecessor1": t["pred"], "PredType1": "Finish to Start" if t["pred"] else "",
        "QText1": t["question"], "QType1": "Yes/No", "QRole1": "Assignee", "QReq1": "Yes",
    })
    return [row[c] for c in TM_HEADER]


def write_csv(path, tasks):
    # Same physical format as the draft: UTF-8 with BOM, CRLF line endings, minimal quoting.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        w.writerow(TM_HEADER)
        for t in tasks:
            w.writerow(tm_row(t))


# ---------------------------------------------------------------- Excel helpers
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color="1F3864")
WRAP = Alignment(wrap_text=True, vertical="top")


def table(ws, headers, rows, widths, start_row=1):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(start_row, c, h)
        cell.fill, cell.font, cell.alignment = HEAD_FILL, HEAD_FONT, WRAP
    for r, values in enumerate(rows, start_row + 1):
        for c, v in enumerate(values, 1):
            ws.cell(r, c, v).alignment = WRAP
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    last = start_row + len(rows)
    ws.freeze_panes = ws.cell(start_row + 1, 3 if len(headers) > 6 else 1)
    ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(headers))}{last}"
    return last


def priority_colours(ws, col_letter, first, last):
    rng = f"{col_letter}{first}:{col_letter}{last}"
    for value, colour in (("High", "F8CBAD"), ("Medium", "FFE699"), ("Low", "C6E0B4")):
        ws.conditional_formatting.add(
            rng, CellIsRule(operator="equal", formula=[f'"{value}"'], fill=PatternFill("solid", fgColor=colour)))


CATALOG_HEADERS = [
    "Task ID", "Task Name", "Application", "Process", "Frequency", "Suggested Timing (not imported)",
    "Priority", "Business Purpose", "Execution Instructions", "Expected Successful Result",
    "Evidence Required", "Failure Handling and Escalation", "FSSG Responsibility (technical)",
    "Finance Responsibility (financial validation / approval)", "Completion Question (Yes/No)",
    "Dependencies", "Predecessor1 in Import CSV", "Finance Confirmation Required",
    "Environment-specific Configuration to Confirm", "Automation Recommendation", "Source Draft Task",
]
CATALOG_WIDTHS = [12, 42, 24, 20, 10, 26, 9, 45, 80, 40, 40, 55, 30, 34, 45, 22, 14, 26, 38, 40, 26]


def catalog_sheet(wb, title, tasks, freq):
    ws = wb.create_sheet(title)
    rows = []
    for t in tasks:
        rows.append([
            t["id"], t["name"], t["app"], t["process"], freq, t["timing"], t["priority"], t["purpose"],
            "\n".join(f"{i}. {s}" for i, s in enumerate(t["steps"], 1)) + "\n\n" + NO_TAIL,
            t["expected"], t["evidence"], t["failure"], t["fssg"], t["finance"], t["question"],
            ", ".join(t["deps"]) or "None", t["pred"] or "None", t["fin_confirm"], t["config"],
            t["automation"], t["draft"],
        ])
    last = table(ws, CATALOG_HEADERS, rows, CATALOG_WIDTHS)
    priority_colours(ws, "G", 2, last)
    ws.row_dimensions[1].height = 45


# ---------------------------------------------------------------- Supporting sheets
README_LINES = [
    ("FSSG Oracle EPM Operational Task Catalog", "title"),
    ("Status: draft for FSSG Lead and Finance review. Not yet imported or tested in an Oracle environment.", ""),
    ("Generated by deliverables/fssg/build_fssg_catalog.py from fssg_tasks.py (edit the data file, then rebuild).", ""),
    ("", ""),
    ("Scope", "h"),
    ("IT operational controls and technical verification of Finance processes for FCCS, ARCS, EDMCS, Planning "
     "and Narrative Reporting, the GB and Oracle EBS pipelines and FX rates. Financial validation and approval "
     "remain with Finance and are named in every task.", ""),
    ("", ""),
    ("Sheets", "h"),
    ("Daily Operations - 12 tasks run every FSSG business day.", ""),
    ("Monthly Close - 18 tasks across pre-close, close and post-close.", ""),
    ("Draft Review - disposition of each of the 22 draft tasks.", ""),
    ("Responsibility Boundary - FSSG technical scope vs Finance validation/approval per process.", ""),
    ("Escalation Matrix - severity definitions and escalation path (roles, not named users).", ""),
    ("Automation - checks that should be automated rather than performed manually.", ""),
    ("Config Register - environment values to confirm before import (left blank deliberately).", ""),
    ("Import Notes - import file format, populated fields and unverified items.", ""),
    ("", ""),
    ("What runs daily", "h"),
    ("Service availability and daily backup, overnight job outcomes, GB and EBS pipeline loads, daily FX "
     "rates (if used), application-specific job health for FCCS, ARCS, EDMCS, Planning and Narrative "
     "Reporting, SSO/access incidents, and the daily incident/SR status. These detect failures within one "
     "business day.", ""),
    ("", ""),
    ("What belongs to month-end", "h"),
    ("Pre-close: next-month schedules and time zones, Oracle update impact, credential and certificate "
     "expiry, EDMCS metadata synchronization and freeze, period mappings and substitution variables, ARCS "
     "period setup, month-end FX rates, readiness checkpoint. Close: month-end GB/EBS loads, FCCS "
     "consolidation jobs, ARCS balances, Planning actuals, Narrative Reporting refresh and distribution. "
     "Post-close: period lock and schedule roll-forward, snapshot archive, evidence pack, access review.", ""),
    ("", ""),
    ("What should be automated", "h"),
    ("Detection (job failures, pipeline failures, snapshot presence), rate comparison, count "
     "reconciliation, substitution variables, reconciliation creation, report extraction and snapshot "
     "download. The manual task then reviews the automated output and handles exceptions. See the "
     "Automation sheet.", ""),
    ("", ""),
    ("Completion questions", "h"),
    ("Each task has one required Yes/No question answered by the Assignee. Yes means the completion "
     "criterion in the question is met. A No answer is allowed: the assignee records the incident/SR "
     "number, the exception and the next step in the comments and attaches evidence. The catalog does "
     "not assume that Task Manager blocks completion on No.", ""),
]

BOUNDARY = [
    ("Service health / backup", "Availability checks, snapshots, Oracle SRs", "Informed of outages"),
    ("Job scheduling", "Maintain and verify schedules, time zones, Task Manager schedules", "Provide and confirm the close calendar"),
    ("GB / EBS data loads", "Run and monitor loads, technical completeness (counts, control totals, rejections)",
     "Approve mapping changes, validate balances, close source periods"),
    ("FX rates", "Load rates, technically compare with the approved source", "Source, own and approve rate values"),
    ("FCCS consolidation", "Job health, technical re-runs on agreement", "Run/approve consolidation, validate results, journals, data status, lock"),
    ("ARCS", "Period setup on instruction, imports, matching job health", "Period status decisions, profiles, preparation, review, sign-off"),
    ("EDMCS", "Synchronization, exports, target imports", "Raise and approve requests, confirm metadata freeze"),
    ("Planning", "Loads, rules, data maps job health", "FP&A validates actuals, plans and forecasts"),
    ("Narrative Reporting", "Refresh and distribution technical success", "Approve content, release distribution"),
    ("Access and SSO", "Triage, execute approved access changes, produce review reports", "Application owners approve requests and certify access"),
    ("Month-end readiness", "Prepare readiness status and evidence pack", "Finance close lead acknowledges and decides"),
]

ESCALATION = [
    ("P1 - Critical", "Production service unavailable, widespread SSO failure, or close-critical process blocked during the close window.",
     "FSSG Analyst -> FSSG Lead immediately -> Finance close lead -> Oracle SR (production outage) -> IT management",
     "To be agreed in the FSSG SLA"),
    ("P2 - High", "Close-critical job, load, FX or consolidation failure; missing backup; credential/certificate expired.",
     "FSSG Analyst -> FSSG Lead same day -> job/interface owner (GB, EBS, identity provider) -> Oracle SR if product issue; Finance close lead informed during close",
     "To be agreed in the FSSG SLA"),
    ("P3 - Medium", "Non-critical job failure with workaround, single-user access issue, performance degradation.",
     "FSSG Analyst -> job/application owner; FSSG Lead if overdue", "To be agreed in the FSSG SLA"),
    ("P4 - Low", "Cosmetic issue, information request, improvement.", "FSSG backlog", "To be agreed in the FSSG SLA"),
]

AUTOMATION = [
    ("Service availability and snapshot presence", "FSSG_D001", "Scheduled EPM Automate script: login and listFiles for the Artifact Snapshot timestamp per service; alert on failure.",
     "Review alert output; handle exceptions."),
    ("Job and pipeline failure detection", "FSSG_D002, D003, D004, D006-D010", "Native job/pipeline email notifications where available, or REST API job status polling.",
     "Reconcile against the Job Schedule Register; triage."),
    ("Record count and control total reconciliation", "FSSG_D003, D004, M009, M010, M013", "Script reading Data Integration process details/exports and comparing with source totals.",
     "Review exceptions; Finance validates balances."),
    ("FX rate comparison", "FSSG_D005, M007", "Script comparing the approved rate file with exported application rates; produces an exception file.",
     "Review; Finance confirms."),
    ("EDMCS synchronization", "FSSG_D008, M004", "Scheduled EDMCS export and target metadata import with failure alerting (verify commands for your EPM Automate version).",
     "Viewpoint comparison and metadata freeze confirmation."),
    ("Substitution variables / period parameters", "FSSG_M005, M015", "EPM Automate setSubstVars driven by the close calendar; jobs read variables instead of hard-coded periods.",
     "Finance confirms values."),
    ("ARCS reconciliation creation and imports", "FSSG_M006, M012", "EPM Automate createReconciliations; ARCS integration task types in FCCS Task Manager (such as ARC_RC_IMPORT_DATA seen in the existing NA Monthly Close export) once configured.",
     "Count verification; Finance owns profiles."),
    ("Access review extracts", "FSSG_M018", "Scheduled EPM Automate roleAssignmentReport / user and group reports per service.",
     "Owner review and certification."),
    ("Post-close snapshot archive", "FSSG_M016", "EPM Automate downloadFile of the Artifact Snapshot to the approved repository.",
     "Confirm stored files."),
    ("Task Manager schedule creation", "FSSG_M001", "Deploy daily and monthly schedules from the templates (Schedule Deployer in this repository).",
     "Confirm schedules exist."),
    ("Keep manual", "FSSG_D011, D012, M002, M003, M008, M017", "Judgement, approvals and communication - not suitable for automation.", "-"),
]

CONFIG_ITEMS = [
    ("Production and Test URLs for FCCS, ARCS, EDMCS, Planning, Narrative Reporting", "D001, M002"),
    ("SSO / identity provider contact and certificate owner", "D001, D011, M003"),
    ("Approved daily maintenance window per service (time zone)", "D001, M001"),
    ("Job Schedule Register location and owner", "D002, M001, M015"),
    ("GB pipeline / integration names, delivery mechanism, control total source", "D003, M009"),
    ("Oracle EBS integration names and connection method (agent, file, direct)", "D004, M010"),
    ("Approved FX rate source, Currency Pair Register, rate types, applications using rates", "D005, M007"),
    ("Whether daily FX rates are loaded (else deactivate D005)", "D005"),
    ("Whether daily Narrative Reporting jobs exist (else deactivate D010)", "D010"),
    ("Finance close calendar and close lead", "M001, M005, M008"),
    ("Substitution variable and period mapping names", "M005, M015"),
    ("ARCS saved filter for reconciliation creation; expected profile count; balance source", "M006, M012"),
    ("Planning actuals source (FCCS, GB or EBS)", "M013"),
    ("Who runs FCCS consolidation (FSSG scheduled job or Finance user)", "M011"),
    ("Narrative Reporting packages, data source connections, distribution lists", "M014"),
    ("Credential and certificate register", "M003"),
    ("Snapshot archive repository and retention", "M016"),
    ("Evidence repository location", "M017"),
    ("Approved privileged user list and application owners", "M018"),
    ("Task Manager: Owner (default TM Admins), Assignee (--assignee), BackupAssignee per task", "All (Import CSV)"),
    ("Task Manager: Approver1 for tasks marked Finance confirmation", "M001, M004, M005, M007, M008, M009, M010, M011, M013, M014, M015, M017, M018"),
    ("Task Manager: OrganizationalUnit (holiday calendar)", "All (Import CSV)"),
    ("Task Manager: StartDate / StartTime / AssigneeEndDate offsets per task (default 0, 09:00-17:00)", "All (Import CSV)"),
    ("Task Manager template names for the daily and monthly imports", "All (Import CSV)"),
    ("Incident tool and SLA response targets", "D002, D012, Escalation Matrix"),
]

IMPORT_NOTES = [
    ("File format", "Both CSVs keep the exact 48-column header and order of the draft, UTF-8 with BOM, CRLF line "
     "endings, minimal quoting - the same physical format as the draft file."),
    ("Two files, two templates", "FSSG_Daily_Operations_TM_Import.csv (12 tasks) and FSSG_Monthly_Close_TM_Import.csv "
     "(18 tasks). The import format has no recurrence column, so daily tasks cannot repeat inside one monthly "
     "schedule. Recommended: import the daily file into its own template and deploy one schedule per business "
     "day; the alternative (one copy of each daily task per working day in the monthly template) is harder to "
     "maintain. The template is selected in the Task Manager import dialog; the file has no template-name column."),
    ("Fields populated in the draft", "TaskID, TaskName, TaskType, Description, Priority, Active, Instruction, "
     "QText1, QType1, QRole1, QReq1. The draft gives no evidence that any other field is mandatory, and none is "
     "assumed mandatory here."),
    ("Fields populated in this import", "Same as the draft plus Predecessor1/PredType1 ('Finish to Start') for "
     "tasks with an in-template dependency, and the fields every BasicTaskType row carries in the customer's "
     "real NA Monthly Close export: Owner = TM Admins, Assignee (build parameter --assignee), StartDate = 0, "
     "StartTime = 09:00, MinimumDuration = 0, AssigneeEndDate = 0, AssigneeEndTime = 17:00, StartEarly = Yes, "
     "QInclAttach1 = No."),
    ("Why these fields are filled", "The first import, with these fields blank as in the draft, was rejected by "
     "Oracle. The values follow the real export; adjust day offsets and times per task to the close calendar."),
    ("Left blank deliberately", "OrganizationalUnit, Duration, BackupAssignee, RequireCompletionBy, Template, "
     "RunAs, PredTemplate1, Approver1 group, Q formatting columns, Parameter/ParamVal, Parent. These are "
     "environment-specific and optional in the real export."),
    ("Assignee", "Must be an existing Oracle user login. Build with "
     "'python deliverables/fssg/build_fssg_catalog.py --assignee <login>'; without it the column stays blank "
     "and the import is expected to fail."),
    ("Unverified: No answer behaviour", "The catalog does not assume a No answer blocks submission. Confirm in "
     "Test how the required Yes/No question behaves with a No answer and whether a comment can be enforced."),
    ("One predecessor, one question, one approver", "The 48-column structure has only Predecessor1, QText1 and "
     "Approver1. Tasks with several dependencies carry the most important one in Predecessor1; the full list is "
     "in the Dependencies column of the catalog. Numbered columns such as Predecessor2 exist in the Oracle "
     "pattern but were not used because they are not in the provided structure."),
    ("Predecessors to adjust", "FSSG_M012 and FSSG_M013 point to FSSG_M008 until the source of ARCS balances and "
     "Planning actuals is confirmed; then point them to the actual source-load task."),
    ("Approvers for Finance confirmation", "Approver1 is blank because no user accounts are known. Without an "
     "approver the task closes on assignee submission; for tasks marked 'Finance Confirmation Required = Yes' "
     "add the Finance approver before deployment, or record Finance confirmation as evidence."),
    ("Task type", "All tasks use BasicTaskType, the only type in the draft. Integration task types (for example "
     "ARC_RC_IMPORT_DATA) depend on integrations configured in the environment and are listed only as "
     "automation options."),
    ("Text limits", "Instruction texts are about 700-1,700 characters on one line and task names are at most 82 "
     "characters. Oracle field length limits were not verified against documentation; check the import log "
     "for truncation."),
    ("Conditional tasks", "FSSG_D005 (daily FX) and FSSG_D010 (daily Narrative Reporting) are Active = Yes; set "
     "Active = No if the environment has no such daily jobs."),
    ("Suggested timing", "All tasks import with day offset 0, 09:00-17:00. The 'Suggested Timing' column in the "
     "catalog is guidance for setting real offsets per task; no calendar dates are in the files."),
]


def text_sheet(wb, title, lines):
    ws = wb.create_sheet(title)
    ws.column_dimensions["A"].width = 130
    for r, (text, kind) in enumerate(lines, 1):
        cell = ws.cell(r, 1, text)
        cell.alignment = WRAP
        if kind == "title":
            cell.font = TITLE_FONT
        elif kind == "h":
            cell.font = Font(bold=True, color="1F3864", size=12)
    return ws


def build_workbook(path):
    wb = Workbook()
    wb.remove(wb.active)
    text_sheet(wb, "README", README_LINES)
    catalog_sheet(wb, "Daily Operations", DAILY, "Daily")
    catalog_sheet(wb, "Monthly Close", MONTHLY, "Monthly")

    ws = wb.create_sheet("Draft Review")
    table(ws, ["Draft Task ID", "Disposition", "New Task ID(s)", "Rationale"], DRAFT_REVIEW, [14, 20, 24, 110])

    ws = wb.create_sheet("Responsibility Boundary")
    table(ws, ["Process", "FSSG - technical verification", "Finance - financial validation and approval"],
          BOUNDARY, [26, 60, 60])

    ws = wb.create_sheet("Escalation Matrix")
    table(ws, ["Severity", "Definition", "Escalation path (roles)", "Response target"], ESCALATION, [16, 60, 80, 28])

    ws = wb.create_sheet("Automation")
    table(ws, ["Check", "Tasks", "Recommended automation", "Remaining manual step"], AUTOMATION, [34, 26, 80, 40])

    ws = wb.create_sheet("Config Register")
    table(ws, ["Item to confirm", "Used by", "Value (to be completed)", "Confirmed by"],
          [(i, u, "", "") for i, u in CONFIG_ITEMS], [70, 40, 40, 20])

    ws = wb.create_sheet("Import Notes")
    table(ws, ["Topic", "Note"], IMPORT_NOTES, [32, 120])

    wb.save(path)


def main():
    global ASSIGNEE
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assignee", default="", help="existing Oracle user login assigned to every task")
    ASSIGNEE = parser.parse_args().assignee.strip()
    if not ASSIGNEE:
        print("WARNING: --assignee not given; Assignee stays blank and Oracle is expected to reject the import.")
    ids = [t["id"] for t in DAILY + MONTHLY]
    assert len(ids) == len(set(ids)), "duplicate TaskID"
    for group in (DAILY, MONTHLY):
        local = {t["id"] for t in group}
        for t in group:
            assert not t["pred"] or t["pred"] in local, f"{t['id']}: predecessor outside its template"
    write_csv(HERE / "FSSG_Daily_Operations_TM_Import.csv", DAILY)
    write_csv(HERE / "FSSG_Monthly_Close_TM_Import.csv", MONTHLY)
    build_workbook(HERE / "FSSG_EPM_Operational_Task_Catalog.xlsx")
    print(f"Built {len(DAILY)} daily + {len(MONTHLY)} monthly tasks in {HERE}")


if __name__ == "__main__":
    main()
