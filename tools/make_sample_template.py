"""Create samples/Monthly_Close_Template.xlsx — a demo workbook in the provisional Task Manager column layout."""

from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]

HEADERS = ["Task ID", "Task Name", "Task Type", "Description", "Owner", "Assignee", "Approver1",
           "Predecessor", "Start Day", "End Day", "Instructions", "Cost Center"]
TASKS = [
    ("T001", "Close subledgers", "Basic", "Close AP and AR", "controller", "ap.lead", "controller", "", -2, -1, "Run close in ERP", "100"),
    ("T002", "Load trial balance", "Data Load", "Load TB into FCCS", "controller", "fin.analyst", "controller", "T001", 0, 0, "", "100"),
    ("T003", "Run consolidation", "Consolidation", "", "controller", "fin.analyst", "cfo", "T002", 1, 1, "", "200"),
    ("T004", "Intercompany reconciliation", "Basic", "", "controller", "ic.analyst", "controller", "T002", 1, 2, "", "200"),
    ("T005", "Review results", "Review", "CFO review", "cfo", "controller", "cfo", "T003, T004", 2, 3, "", ""),
]


def main() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tasks"
    ws.append(HEADERS)
    for task in TASKS:
        ws.append(list(task))
    out = ROOT / "samples" / "Monthly_Close_Template.xlsx"
    out.parent.mkdir(exist_ok=True)
    wb.save(out)
    print(out)


if __name__ == "__main__":
    main()
