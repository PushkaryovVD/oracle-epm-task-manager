# Oracle EPM Task Manager — Monthly Schedule Deployer
## Stage 1 (Research) and Stage 2 (Architecture)

Status: draft for review · Date: 2026-10-23

---

## 1. Technical feasibility — summary

| Capability | Feasible? | How |
|---|---|---|
| Deploy one schedule from an existing Task Manager template | **Yes, officially supported** | REST `TM_DEPLOY_TEMPLATE` job, or EPM Automate `deployTaskManagerTemplate` |
| Deploy many monthly schedules in a loop | **Yes** | One deploy job per month, sequential, each polled to completion |
| Automatic schedule names | **Yes** | `scheduleName` is a free parameter of the deploy call |
| Custom Year / Period members (fiscal calendar) | **Yes** | `year` and `period` are dimension *member names* — mapping must be configurable |
| Holidays / working days | **Partly** | Only through `orgUnit`. Without it Oracle uses "standard date mapping" and ignores holiday rules |
| Verify the deploy outcome | **Yes (job level)** | Poll the job: `status` -1 in progress, 0 success, 1 fail |
| Import an Excel file into a Task Manager **template** via API | **No documented API** | Import of tasks into templates is a UI feature (CSV / tab-delimited file). See §3 |
| List existing schedules / templates via API | **No documented REST API** | Workaround needed for duplicate detection. See §4.3 |
| Same tool for FCCS, Tax Reporting, Planning, EPCM, … | **Yes** | Same deploy API; Oracle lists these products as supported |
| Same tool for **Account Reconciliation (ARCS)** | **Not for Task Manager** | ARCS is not in the supported-products list. Its monthly equivalent is `createReconciliations PERIOD [FILTER]` — a different operation. See §6.4 |

Conclusion: the core feature (bulk monthly deployment with automatic naming) is fully achievable with documented Oracle APIs. Two gaps — **template import from Excel** and **listing existing schedules** — have no documented API and are handled by the workarounds below. They are listed as open questions in §10.

---

## 2. Official Oracle documentation used

- Deploy Task Manager Templates (REST) — `POST /HyperionPlanning/rest/cmapi/{api_version}/jobs`, `jobType = TM_DEPLOY_TEMPLATE`:
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/fccs_deploy_task_manager_templates.html
- Task Manager REST APIs (index: API versions, deploy, update task status for event monitoring, OIC connections — nothing else):
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/task_manager_rest_apis_intro.html
- EPM Automate `deployTaskManagerTemplate`:
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/cepma/epm_auto_deploy_tm_template.html
- EPM Automate `runTaskManagerReport` (relational reports from Task Manager data; PDF/HTML/XLSX/CSV/CSV2):
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/cepma/epm_auto_run_task_mgr_report.html
- October 2024 readiness note (Task Manager commands added to EPM Automate):
  https://docs.oracle.com/en/cloud/saas/readiness/epm/2024/epm-oct24/24oct-epm-wn-f35346.htm
- Importing Tasks into Templates (FCCS):
  https://docs.oracle.com/en/cloud/saas/financial-consolidation-cloud/agfcc/cm_tasks_import_to_templates.html
- Importing Tasks into Schedules (FCCS):
  https://docs.oracle.com/en/cloud/saas/financial-consolidation-cloud/agfcc/cm_tasks_import_to_schedules.html
- Exporting Tasks to Microsoft Excel:
  https://docs.oracle.com/en/cloud/saas/tax-reporting-cloud/agtrc/cm_templates_export_tasks_excel.html
- REST authentication (Basic and OAuth 2):
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/authentication.html
  https://docs.oracle.com/en/cloud/saas/epm-cloud/prest/authentication_oath.html
- EPM Automate `encrypt` (password / OAuth refresh-token file):
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/cepma/epm_auto_encrypt.html
- EPM Automate `createReconciliations` (ARCS):
  https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/cepma/epm_auto_create_reconciliations.html

### 2.1 Deploy API — exact contract (from Oracle docs)

Request:
```json
POST https://<service>-<tenant>.<type>.<dc>.oraclecloud.com/HyperionPlanning/rest/cmapi/v1/jobs
{
  "jobType": "TM_DEPLOY_TEMPLATE",
  "parameters": {
    "templateName": "Monthly Financial Close",
    "scheduleName": "Monthly Close - January 2027",
    "year": "FY27",
    "period": "Jan",
    "dayZeroDate": "2027-01-31",
    "dateFormat": "yyyy-MM-dd",      // optional
    "orgUnit": "Corporate"          // optional; enables holiday rules
  }
}
```
Response: `jobId`, `status` (-1 / 0 / 1), `details`, `descriptiveStatus`, `links[]` — the link with `rel = "Job Status"` is polled with GET.

Required role: **Service Administrator**. Oracle does **not** document what happens when a schedule with the same name already exists — must be tested on a test pod (open question Q1).

EPM Automate equivalent:
```
epmAutomate deployTaskManagerTemplate TEMPLATE_NAME SCHEDULE_NAME YEAR PERIOD DAY_ZERO_DATE [dateFormat=...] [orgUnit=...]
```
Applies to: Planning, Planning Modules, FreeForm, Financial Consolidation and Close, Tax Reporting, Enterprise Profitability and Cost Management, Strategic Workforce Planning, Sales Planning.

---

## 3. Excel template import

### 3.1 What Oracle actually supports
- Task Manager can **export** template tasks to Excel (formatted, or unformatted "for future import").
- Tasks are **imported** into a template through the UI from a **CSV / tab-delimited** file with modes *Replace*, *Update*, *Delete*. Task IDs must be unique within the template.
- Confirmed with the customer's real exports (see §10a): the "for future import" export is a CSV with headers such as `TaskID, TaskName, TaskType, StartDate, StartTime, Owner, Assignee, AssigneeEndDate, Predecessor1, PredType1, Approver1, QText1, Parameter1, ParamVal1, Parent`.
- **There is no documented REST API or EPM Automate command to import tasks into a template.** We will not invent one.

### 3.2 Consequence for the design
The deploy call takes a **template name that already exists in Oracle**. The template does not have to be re-imported every month — it is maintained once by the Task Manager administrator. So the Excel file plays two roles:

1. **Source of truth for validation and preview** (template name, task count, owners/assignees/approvers, predecessors, day offsets, unsupported columns).
2. **Optional:** convert it into an Oracle-compatible import CSV that the administrator imports once through the UI (`Task Manager → Templates → Import Tasks`). The app produces the file and step-by-step instructions; it does not automate the UI.

### 3.3 Workbook inspection
The importer (`template_importer.py`, openpyxl, read-only) classifies the workbook:

| Detected type | Rule | Action |
|---|---|---|
| **Oracle Task Manager export** (unformatted) | Header row contains Oracle task columns (Task ID, Task Name, Task Type, Start/End Date offsets, Owner, Assignee, Approver, Predecessor…) | Full validation |
| **Oracle export (formatted)** | Visible-columns only | Validate, warn that hidden attributes are missing |
| **Custom spreadsheet** | Headers not recognised | User maps columns once in a mapping dialog; mapping saved per file signature |

The exact Oracle column header list is taken from a real export of the customer's environment (open question Q3) and kept in `config/tm_columns.json`, not hard-coded.

### 3.4 Validation rules
- **Critical (blocks deploy):** missing Task ID / Task Name / Task Type; duplicate Task ID; predecessor referencing a non-existent Task ID; circular dependencies; non-numeric day offsets; empty template name.
- **Warning:** empty assignee/approver; unknown columns (listed explicitly — *never silently dropped*); formulas instead of values; hidden sheets; merged cells.
- **Info:** task count, number of dependencies, distinct owners.

Result shown as a list with sheet/row/column references; exportable to the deployment report.

---

## 4. How schedules are created

### 4.1 Per-month pipeline
```
for each month in selected range (chronological):
    name     = NamingService.render(pattern, month)
    year     = PeriodService.year_member(month)        # e.g. "FY27"
    period   = PeriodService.period_member(month)      # e.g. "Jan"
    dayZero  = PeriodService.day_zero(month, strategy) # e.g. 2027-01-31
    status   = DuplicateCheck(name)                    # Ready / Skipped - Already Exists
    -> preview row
on "Confirm and Deploy":
    for each Ready row (sequential, one at a time):
        POST /cmapi/v1/jobs  (TM_DEPLOY_TEMPLATE)
        poll Job Status link until status != -1 (backoff 2s → 15s, timeout configurable)
        status 0  -> Completed, write to ledger
        status 1  -> Failed (Oracle "details" shown)
        401       -> stop run, ask to re-enter credentials, remaining rows stay Pending
        429/5xx   -> retry with exponential backoff (max 3), then Failed
```
An accepted job (HTTP 200/201 with `status = -1`) is **never** reported as Completed.

### 4.2 Day Zero Date
Oracle calculates every task date from Day Zero + template offsets (Oracle's native date calculation is used — the app only supplies Day Zero). Strategies (per environment, overridable per run):

| Strategy | Example for Jan 2027 |
|---|---|
| Last calendar day of month (default) | 2027-01-31 |
| Last business day (Mon–Fri) | 2027-01-29 |
| First day of next month | 2027-02-01 |
| Fixed day N of month | N=5 → 2027-01-05 |
| Explicit per-month table | from config |

Holidays: passed through `orgUnit`; without it Oracle ignores holiday rules (documented behaviour).

### 4.3 Duplicate detection (no "list schedules" API)
Ranked approach:
1. **Local deployment ledger** per environment (`%APPDATA%\EPMTaskDeployer\ledger\<env>.json`): every Completed schedule is recorded → retries never re-deploy it.
2. **Oracle check via `runTaskManagerReport`** (EPM Automate, optional): an administrator creates a Task Manager report "Schedule List" once; the app runs it as CSV and reads the schedule names. Requires EPM Automate on the PC (or on a shared path) — decision Q2.
3. **Oracle's own response** on deploy — if Oracle rejects a duplicate name (to be confirmed on a test pod, Q1), that error is mapped to *Skipped - Already Exists*.

If neither (2) nor confirmed (3) is available, preview rows are shown as **"Ready (not checked in Oracle)"** and the confirmation dialog says so explicitly.

### 4.4 Template existence
There is no documented API to list templates. The template name is checked at first deploy; a failure with "template not found" stops the run before further months are attempted (the remaining months stay *Pending*, not *Failed*).

---

## 5. Recommended architecture

**Option A — Python + Oracle EPM REST API (recommended)**, with EPM Automate as an **optional** add-on only for `runTaskManagerReport`.

| | A: REST | B: EPM Automate | C: PowerShell + EPM Automate |
|---|---|---|---|
| Install on each PC | Only our .exe | EPM Automate (needs Java-based installer, admin rights often) | Same as B + execution-policy issues |
| Async job handling | Native: poll job link | CLI blocks; parse stdout | Same as B |
| Error detail | JSON `details` | Text parsing | Text parsing |
| Multiple environments in parallel | Easy (separate sessions) | One login state per working dir | Same as B |
| Corporate endpoint security | One signed exe | Extra executable, often blocked | PowerShell often restricted |

PowerShell gives no technical benefit here → not used.

### 5.1 Authentication
- **Basic auth** (Classic and OCI Gen 2): works only for an Oracle **native** (non-SSO) account. A corporate SSO password **cannot** be used.
  Username format: OCI Gen 2 → `john.doe@company.com`; Classic → `identitydomain.john.doe@company.com`.
- **OAuth 2 (OCI Gen 2 only, recommended by Oracle):** the Identity Domain admin registers a client once; each user obtains a refresh token once (device-code flow); the app stores the refresh token and exchanges it for access tokens. This is the route for SSO-only users.
- Recommendation for go-live: a **dedicated native service account with Service Administrator role per environment** (deploy requires Service Administrator anyway), plus OAuth 2 in a later release.

---

## 6. Environments and one-time credentials (new requirement)

### 6.1 Requirement
- The user enters **URL, username and password once per Oracle system**; the app remembers them securely and connects automatically on the next launch.
- Must work not only for FCCS but for other EPM services (ARCS, Tax Reporting, Planning, …), and the **URL decides which pod** the schedules go to.

### 6.2 Environment profile
Each Oracle system is an *Environment profile*. Non-secret data in `%APPDATA%\EPMTaskDeployer\environments.json`:

```json
{
  "id": "fccs-test",
  "display_name": "FCCS – Test",
  "service_type": "FCCS",
  "base_url": "https://acme-test-fccs.epm.us-phoenix-1.ocs.oraclecloud.com",
  "auth_method": "basic",
  "username": "svc_tm_deploy@acme.com",
  "is_production": false,
  "default_org_unit": "Corporate",
  "period_mapping": "standard_monthly",
  "year_member_format": "FY{YY}",
  "day_zero_strategy": "last_calendar_day"
}
```

Secrets (password or OAuth refresh token) are stored **only** in **Windows Credential Manager** via the `keyring` library (Windows Vault backend, encrypted with the user's Windows profile via DPAPI):

- target name: `EPMTaskDeployer:<environment id>`
- username: the Oracle username
- secret: password / refresh token

Never written to JSON, logs, reports or crash dumps; the log formatter masks `Authorization` headers and any field named `password|token|secret`.

### 6.3 First-run and next-run flow
```
First launch ─► "Add Oracle Environment" wizard
                 1. Service type: FCCS | Tax Reporting | Planning | EPCM | FreeForm | ARCS | …
                 2. Environment URL  (validated, see below)
                 3. Username + Password  (or "Sign in with OAuth")
                 4. [Test Connection]  ─► GET /HyperionPlanning/rest/cmapi/  (Task Manager API versions)
                 5. Save  ─► profile to JSON, secret to Credential Manager
Next launches ─► environment dropdown, auto-connect with stored secret
401 Unauthorized (password changed/expired) ─► "Update credentials" dialog, overwrites the secret
Buttons per environment: Edit · Update Password · Forget Credentials · Remove Environment
```

**URL validation / normalisation** (so tasks land on the right pod):
- must be `https://`; host must end with `.oraclecloud.com` (custom vanity hosts allowed via an admin setting);
- strip paths like `/epmcloud`, `/HyperionPlanning/...`, trailing `/` → store only scheme + host;
- detect test vs. production from the Oracle host naming (`-test`) and **let the user confirm** the flag; production environments get a red badge and a second confirmation in the deploy dialog;
- Test Connection must succeed before the profile can be saved.

The confirmation dialog always shows **environment name, full host and Production/Test badge**, so the user sees exactly which pod is being changed.

### 6.4 Service types and capabilities
Capability registry (`config/service_types.json`) — the UI only shows operations the service supports:

| Service type | Task Manager schedule deploy | Notes |
|---|---|---|
| Financial Consolidation and Close (FCCS) | ✅ | Primary target |
| Tax Reporting (TRCS) | ✅ | |
| Planning / Planning Modules / FreeForm | ✅ | |
| Enterprise Profitability and Cost Management | ✅ | |
| Strategic Workforce Planning, Sales Planning | ✅ | |
| **Account Reconciliation (ARCS)** | ❌ not supported by Oracle | Monthly equivalent: `createReconciliations PERIOD [SAVED_FILTER]` (copies profiles into a period). Can be added as a separate "ARCS – Create Period Reconciliations" module — decision Q5 |

The app stores ARCS credentials in the same way, so adding the ARCS module later needs no change to the credential layer.

---

## 7. Proposed CustomTkinter UI

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Oracle EPM Task Manager – Schedule Deployer        [☾ Theme]  [⚙ Settings]   │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ ENVIRONMENT   │ 1  Template                                                  │
│ [FCCS – Test ▾]│   [Browse…] Monthly_Close_Template.xlsx   ✔ Valid, 148 tasks │
│ ● Connected   │   Oracle template name: [Monthly Financial Close        ]    │
│ acme-test-fccs│   2 warnings · 0 errors  [Details]                           │
│ TEST          │──────────────────────────────────────────────────────────────│
│ [+ Add]       │ 2  Schedules                                                 │
│ [Manage…]     │   From [January ▾][2027 ▾]  To [December ▾][2027 ▾]          │
│               │   Name pattern [Monthly Close - {MMMM} {YYYY}]  {…} help     │
│ STEPS         │   Day Zero [Last calendar day ▾]  Org unit [Corporate ▾]     │
│ 1 Template ✔  │   Period mapping [Standard monthly ▾] [Edit…]                │
│ 2 Schedules ✔ │──────────────────────────────────────────────────────────────│
│ 3 Preview     │ 3  Preview                         [Check in Oracle]         │
│ 4 Deploy      │  # │ Schedule name                │Period│Year│Day 0  │Status │
│               │  1 │ Monthly Close - January 2027 │ Jan  │FY27│01-31  │Ready  │
│               │  2 │ Monthly Close - February 2027│ Feb  │FY27│02-28  │Ready  │
│               │  … │                              │      │    │       │       │
│               │──────────────────────────────────────────────────────────────│
│               │ 4  Deploying schedule 4 of 12: April 2027 ███████░░░░ 33%    │
│               │   ✔ 3  ⏭ 0  ✖ 0      [Deploy Schedules] [Cancel Remaining]  │
│               │                       [Retry Failed] [Export Report]         │
└───────────────┴──────────────────────────────────────────────────────────────┘
```

- Preview updates on every change (debounced 200 ms); no Oracle call happens until **Deploy Schedules → Confirm and Deploy**.
- Deployment runs in a `threading.Thread` worker; UI updates go through a `queue.Queue` drained by `after(100, …)` — Tk widgets are touched only from the main thread.
- Cancel = finish the current month's job (cannot be cancelled in Oracle), then stop; the rest stay *Pending*.
- Confirmation dialog and completion summary exactly as in "Schedule Preview and Deployment Confirmation.md".

---

## 8. Automatic naming

- Placeholders: `{MMMM}` January, `{MMM}` Jan, `{MM}` 01, `{YYYY}` 2027, `{YY}` 27, `{TemplateName}`.
- Month names come from a fixed English table in code (`calendar` module is locale-dependent — not used).
- Unknown placeholders → validation error, not silent pass-through.
- Name validation: non-empty, trimmed, no leading/trailing spaces, max length and forbidden characters from `config/naming_rules.json` (Oracle's exact limits to be confirmed on a test pod — Q4; initial safe default: ≤ 80 chars, no `\ / : * ? " < > |`).
- Generated names must be unique inside the run; duplicates inside the run block deploy.
- Acceptance: Jan–Dec 2027 + `Monthly Close - {MMMM} {YYYY}` → exactly the 12 names in the specification (covered by a unit test).

Period mapping (`config/period_mappings/*.json`): calendar month → Oracle `period` member and `year` member, e.g. standard (`Jan…Dec`, `FY{YY}`) or fiscal (`Apr → P01`, year shift +1). Nothing hard-coded in deploy logic.

---

## 9. Project structure

```text
OracleEPMTaskManager/
├── app/
│   ├── main.py
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── environment_view.py      # sidebar + Add/Manage Environment wizard
│   │   ├── template_view.py
│   │   ├── schedule_view.py
│   │   ├── preview_table.py
│   │   ├── deployment_view.py
│   │   └── dialogs.py               # confirm, summary, credentials
│   ├── services/
│   │   ├── environment_store.py     # environments.json (non-secret)
│   │   ├── credential_store.py      # keyring / Windows Credential Manager
│   │   ├── oracle_client.py         # REST session, auth, retries, job polling
│   │   ├── epm_automate.py          # optional: runTaskManagerReport
│   │   ├── template_importer.py     # openpyxl inspection + validation + CSV export
│   │   ├── naming_service.py
│   │   ├── period_service.py
│   │   ├── schedule_planner.py      # builds preview rows
│   │   ├── schedule_deployer.py     # worker, ledger, retry
│   │   └── report_service.py        # xlsx/csv report
│   ├── models/                      # dataclasses: Environment, PlannedSchedule, DeployResult…
│   └── utils/                       # logging (secret masking), paths
├── config/                          # service_types, naming_rules, tm_columns, period_mappings
├── tests/
├── assets/
├── requirements.txt                 # customtkinter, openpyxl, requests, keyring, pytest, responses
├── build.ps1                        # PyInstaller one-folder build + optional code signing
└── README.md
```

Packaging: PyInstaller **one-folder** build (fewer AV false positives than one-file), signed with the company code-signing certificate if available; runs under a standard user account (all data in `%APPDATA%`).

---

## 10. Limitations and open questions

| # | Question | Why it matters |
|---|---|---|
| Q1 | What does Oracle return when `scheduleName` already exists? (test on a **test pod**) | Decides whether Oracle itself guarantees no duplicates |
| Q2 | May EPM Automate be installed on finance PCs (or a shared path)? | Needed only for Oracle-side duplicate check via `runTaskManagerReport` |
| Q3 | Please provide a sample of your Excel template (a real export from Task Manager, data can be anonymised) | Defines the column catalogue and validation rules |
| Q4 | Exact schedule-name length / character limits in your pod | Name validation |
| Q5 | ARCS: do you want "Create period reconciliations" (`createReconciliations`) as a second module? Which other services (Tax Reporting, Planning) are in use? | Scope of service types |
| Q6 | Authentication: native service account (basic auth) or OAuth 2 with your Identity Domain? Are users SSO-only? | SSO passwords cannot be used for API calls |
| Q7 | Do finance users have the **Service Administrator** role? Oracle requires it for deploy | Otherwise a shared service account is required |
| Q8 | Year/Period member names in your FCCS application (`FY27`/`2027`, `Jan`/`January`/`P01`), and your Day Zero rule | Period mapping defaults |
| Q9 | Is `orgUnit` (holiday calendar) used in your Task Manager? | Holiday-aware dates |
| Q10 | Should the app ever change schedule status (e.g. set to Open)? Default: **no** | Safety rule 13 |

Known hard limitations:
- No API to import tasks into a template — one-time manual UI import by the administrator.
- No API to list schedules/templates — duplicate check relies on ledger + report/Oracle response.
- A running Oracle deploy job cannot be cancelled.
- Holiday rules only via `orgUnit`.

---

## 10a. Decisions after Q1–Q10 (2026-10-23)

| # | Answer | Decision in the design |
|---|---|---|
| Q1 (confirmed 2026-10-23) | Tested on the customer's test pod | **Oracle rejects a duplicate name itself**: the deploy POST answers HTTP 400 "You must specify a unique schedule name." before any job starts. Mapped to *Skipped - Already Exists* (`schedule_exists_patterns`). Duplicate protection is therefore two-layer: local ledger + Oracle. Also observed: deploy POST answers HTTP 201; the "Job Status" link carries an explicit `:443`. |
| Q1 (original) | Test on a test pod | Stage 4 ships a **test checklist** (deploy the same name twice, record Oracle's `details`). Until then: local ledger + mapping of the Oracle error text to *Skipped - Already Exists* is configurable (`config/oracle_messages.json`), not hard-coded. |
| Q2 | No EPM Automate on finance PCs | **REST only.** `runTaskManagerReport` is dropped. Consequence: there is **no documented REST API that lists schedules or templates** (re-checked against the full REST API list), so the Oracle-side duplicate check is only Oracle's own deploy response + the local ledger. Preview rows stay *Ready (not checked in Oracle)* and the confirmation dialog says so. |
| Q3 | Real export provided | Native format is **CSV** ("Unformatted / For Future Import"), not Excel. Importer now reads `.csv` (UTF-8/BOM or cp1252, delimiter sniffed) and `.xlsx`. Column catalogue rebuilt from the real headers (48 columns incl. numbered groups `Predecessor/PredType/PredTemplate N`, `Approver… N`, `Q… N` questions, `Parameter/ParamVal N`). Both sample exports parse with **0 unrecognised columns**. Template name = file name (the export has no template-name column). Day offsets (`StartDate`, `AssigneeEndDate`, `ApproverEndDate N`) are relative to Day Zero, confirming Oracle-side date calculation. |
| Q4 | Don't guess limits | `naming_rules.json` keeps only what is objectively safe (non-empty, no control characters). The 80-char/forbidden-character defaults are marked provisional and are **warnings**, not blocks, until the test-pod check; Oracle's deploy error is shown verbatim if it rejects a name. |
| Q5 | FCCS Task Manager only | Service type list stays (credential layer is generic), but the UI defaults to FCCS; ARCS remains "planned". Note: the NA Monthly Close template already contains **ARCS integration tasks** (`ARC_RC_CHANGE_PERIOD_STATUS`) executed by FCCS Task Manager — these deploy like any other task. |
| Q6 | Username + password | **Basic auth** over HTTPS (OCI: plain user name; Classic: `identitydomain.user`). Stored once per environment in Windows Credential Manager (already implemented). OAuth 2 stays optional for later. |
| Q7 | Probably not only Service Administrators | Oracle's REST docs state **Service Administrator** for both *Deploy Task Manager Templates* and *Get Member*. We do not assume either way: Stage 4 maps HTTP 401/403 to a clear message ("Your Oracle user is not allowed to deploy Task Manager templates. Oracle requires the Service Administrator role for this API.") and the test-pod checklist includes a run with a non-admin user. |
| Q8 | Read real values from FCCS; Day Zero configurable | Stage 4: `GET /HyperionPlanning/rest/v3/applications` (application name) and `GET …/applications/{app}/dimensions/{dim}/members/{member}` (*Get Member*) to **verify each Year/Period member before deploy** (dimension names configurable, defaults `Years`, `Period`). A missing member → row *Blocked*. There is no documented "list children" API, so members are verified one by one, not listed. Day Zero stays a per-environment/per-run strategy (already implemented). |
| Q8 (confirmed) | Real FCCS test pod + an existing schedule "NA and WW June 2026 Close" | Year members `FY26`, Period members `Jan…Dec`, FY = calendar year, Day Zero = last calendar day of the month (Jun 30, 2026) → defaults *Standard monthly* + *Last calendar day* are correct. Naming convention: `NA and WW {MMMM} {YYYY} Close`. Get Member answers **HTTP 400** (not 404) for a missing member; matched via `member_not_found_patterns` in `config/oracle_messages.json`. |
| Q9 | Use org units if configured | `orgUnit` is passed when set (environment default, overridable per run). No REST API lists organizational units, so the value is typed by the user; the template's own `OrganizationalUnit` column is shown in validation for reference. |
| Q10 | Never open schedules | Confirmed: the app only deploys. No schedule status changes. |

---

## 11. Development plan

| Stage | Deliverable | Exit criteria |
|---|---|---|
| 1 Research | This document | Q1–Q10 answered |
| 2 Architecture | Config schemas, UI mock, environment/credential model | Approved by you |
| 3 Prototype | CustomTkinter UI: environments (credential store), Excel validation, month range, naming, live preview — **no Oracle calls** | Acceptance of the 12-name preview |
| 4 Oracle integration | REST client, Test Connection, deploy + job polling, ledger, retry, confirmation dialog | Successful run on a **test pod** |
| 5 Production readiness | Logging with masking, report export, PyInstaller build, README, full test suite (mocked Oracle via `responses`) | Signed build, tests green |
