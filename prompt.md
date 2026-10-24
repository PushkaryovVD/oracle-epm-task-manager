# Project: Oracle EPM FCCS Task Manager — Monthly Schedule Deployment Desktop Application

## 1. Your Role

Act as a Senior Python Software Engineer, Desktop Application Architect, Oracle EPM FCCS Specialist, and Oracle EPM Integration Engineer.

Your task is to research, design, and develop a production-ready Windows desktop application for the Finance Department.

**Technology stack:**

- Python 3.12+
- CustomTkinter for the desktop interface
- openpyxl for Excel file processing
- Oracle EPM Automate and/or official Oracle EPM REST APIs
- PowerShell only when necessary for integration with EPM Automate
- PyInstaller for Windows executable packaging
- Windows Credential Manager or another secure credential storage mechanism

All application interfaces, labels, buttons, messages, logs intended for users, and documentation must be in English.

The application must be designed for non-technical finance employees.

## 2. Business Problem

Our company uses Oracle EPM Financial Consolidation and Close (FCCS).

Within FCCS, the Finance Department uses Task Manager to manage monthly financial closing activities.

Finance employees have an Excel Task Manager template containing the tasks they need to execute each month.

Currently, creating monthly schedules requires repetitive manual work.

We need an application that allows finance employees to upload their Excel template once, select a range of months, and automatically create Task Manager schedules for every selected month.

The application must preserve the original template structure and assign the correct schedule name and dates for each month.

## 3. Main User Workflow

The expected workflow is:

1. Launch the Windows desktop application.
2. Connect to the Oracle FCCS environment.
3. Select an Excel Task Manager template (.xlsx).
4. The application reads and validates the Excel file.
5. Enter or confirm the Task Manager template name.
6. Select Start Month.
7. Select End Month.
8. Select Year or a date range spanning multiple years.
9. Preview all schedules that will be created.
10. Click "Deploy Schedules".
11. The application processes each month individually.
12. Display deployment progress and individual results.
13. Provide a final deployment report.

Finance users must not need to open PowerShell, write Python code, execute CLI commands, or manually edit configuration files.

## 4. Excel Template Import

This is a critical requirement.

The user must be able to select an Excel file using a standard Windows file selection dialog.

Example:

`Monthly_Close_Template.xlsx`

The application must inspect the Excel workbook and identify its structure.

Determine whether the supplied Excel file is a native Oracle Task Manager import template or a custom spreadsheet.

Do not assume that every Excel file can be directly imported into Oracle.

Research the official Oracle FCCS Task Manager Excel import/export functionality and supported integration methods.

If Oracle requires a specific Excel format, explain the required columns, structure, metadata, and supported import operations.

If a direct Excel-to-Task-Manager API does not exist, design a supported conversion or import workflow.

Never invent Oracle API endpoints or unsupported EPM Automate commands.

The application should:

- Validate the Excel file before deployment.
- Identify missing required fields.
- Detect invalid task definitions.
- Preserve task names and descriptions.
- Preserve assignees and approvers.
- Preserve task dependencies.
- Preserve task instructions and supported attributes.
- Preserve relevant date rules.
- Report unsupported fields.
- Prevent deployment when critical validation errors exist.

Do not silently discard unsupported Excel data.

If importing the workbook requires creating or updating a Task Manager Template in Oracle, perform this using documented and supported operations.

Do not overwrite an existing Oracle template without explicit user confirmation.

## 5. Monthly Schedule Creation

The main feature is bulk deployment of monthly Task Manager schedules.

The user selects:

- Start Month
- End Month
- Year
- Excel Template
- Schedule Naming Format

Example:

Start Month: January

End Month: December

Year: 2027

Template: Monthly_Close_Template.xlsx

The application should prepare 12 monthly schedules.

Each month must be processed independently.

The deployment should reuse the validated template and avoid requiring users to upload the Excel file separately for every month.

If Oracle requires an import step followed by template deployment, implement that sequence.

Investigate the official EPM Automate command:

`deployTaskManagerTemplate`

Also investigate the official Oracle EPM REST API for deploying Task Manager templates.

Confirm supported parameters, limitations, required permissions, and version compatibility.

Prefer a simple, supported integration architecture over unnecessary complexity.

## 6. Automatic Schedule Naming

**This requirement is mandatory.**

Every generated Task Manager Schedule must receive the correct name based on the selected month and year.

The user must not manually rename each Schedule.

Example configuration:

Template Name: Monthly Financial Close

Schedule Name Pattern:

`Monthly Close - {MMMM} {YYYY}`

Expected names:

- Monthly Close - January 2027
- Monthly Close - February 2027
- Monthly Close - March 2027
- Monthly Close - April 2027
- Monthly Close - May 2027
- Monthly Close - June 2027
- Monthly Close - July 2027
- Monthly Close - August 2027
- Monthly Close - September 2027
- Monthly Close - October 2027
- Monthly Close - November 2027
- Monthly Close - December 2027

Support configurable placeholders:

- `{MMMM}` — full English month name
- `{MMM}` — abbreviated English month name
- `{MM}` — two-digit month number
- `{YYYY}` — four-digit year
- `{TemplateName}` — source template name

For example:

`FCCS_Close_{YYYY}_{MM}`

Produces:

`FCCS_Close_2027_01`

The application must show the exact resulting Schedule names before deployment.

The naming engine must use English month names regardless of the Windows system language.

Validate generated names against Oracle's actual naming restrictions.

Check for existing schedules with the same name.

Never create duplicate schedules silently.

The default behavior must be to skip existing schedules and report them to the user.

## 7. Date Calculation and Oracle Period Mapping

The application must correctly calculate deployment parameters for every selected month.

Investigate how Oracle Task Manager uses:

- Year
- Period
- Day Zero Date
- Template date offsets
- Calendar dates
- Working days
- Holidays
- Task dependencies

Do not assume that the calendar month always corresponds directly to the Oracle fiscal period.

If the FCCS application uses a custom fiscal calendar, the application must support explicit period mapping.

For example:

January 2027 may require an Oracle period identifier different from the displayed calendar month.

Do not hardcode Oracle period names without validating them.

Provide a configuration mechanism for mapping calendar months to Oracle period names.

Investigate whether Oracle can calculate task dates from the existing template.

Prefer Oracle's native date calculation when supported.

For each month, display:

- Schedule Name
- Oracle Period
- Year
- Day Zero Date
- Task Count, when available
- Deployment Status

## 8. Desktop Application UI

Build the application using CustomTkinter.

The interface must be modern, clean, responsive, and suitable for finance professionals.

Use a professional enterprise design with clear spacing, readable typography, and intuitive navigation.

Support light and dark themes.

Suggested application layout:

### Connection Panel

Fields:

- Oracle FCCS Environment URL
- Username
- Password or secure authentication method
- Connect button
- Connection status

Do not store passwords in plaintext.

### Template Panel

Controls:

- Browse Excel File
- Selected File Name
- Validate Template
- Template Validation Status
- Task Count
- Template Name

### Schedule Configuration Panel

Controls:

- Start Month
- End Month
- Start Year
- End Year
- Schedule Name Pattern
- Oracle Period Mapping
- Day Zero Date Strategy

### Schedule Preview Panel

Display a table containing:

| Schedule Name                 | Period | Year | Status |
| ----------------------------- | ------ | ---- | ------ |
| Monthly Close - January 2027  | Jan    | 2027 | Ready  |
| Monthly Close - February 2027 | Feb    | 2027 | Ready  |
| Monthly Close - March 2027    | Mar    | 2027 | Ready  |

The table must dynamically update whenever the user changes the date range or naming configuration.

### Deployment Panel

Controls:

- Preview Deployment
- Deploy Schedules
- Cancel Remaining Deployments
- Open Schedule After Creation (optional)
- Export Deployment Report

Display a progress bar and detailed per-month status.

Example:

`Deploying schedule 4 of 12: April 2027`

Possible statuses:

- Pending
- Validating
- Deploying
- Completed
- Skipped
- Failed

The UI must remain responsive while deployments run.

Use a background worker and thread-safe UI updates.

## 9. Deployment Safety

The application will be used in a real corporate Oracle FCCS environment.

Implement appropriate safeguards:

1. Always validate the Excel file before deployment.
2. Always show a deployment preview.
3. Require confirmation before bulk deployment.
4. Check whether each Schedule already exists.
5. Do not overwrite existing schedules automatically.
6. Do not delete Oracle objects.
7. Never log passwords, tokens, or secrets.
8. Stop safely when authentication expires.
9. Handle API rate limits and temporary service failures.
10. Verify the result of each deployment.
11. Do not treat an accepted asynchronous Oracle job as a completed deployment.
12. Support partial success and retrying failed months.
13. Do not automatically open or activate newly created schedules unless explicitly configured.

The application must not modify existing production schedules without explicit authorization.

## 10. Oracle Integration Architecture

Research and compare:

**Option A: Python + Oracle EPM REST API**

Use Python HTTP clients to interact with official Oracle EPM endpoints.

**Option B: Python + EPM Automate**

Use Python subprocess to execute EPM Automate commands.

**Option C: Python + PowerShell + EPM Automate**

Use PowerShell as an integration layer only if there is a clear technical benefit.

Choose the most maintainable and reliable architecture.

Explain whether EPM Automate must be installed on every finance employee's computer or whether the application can use REST APIs directly.

Investigate Oracle authentication options, including SSO limitations and non-interactive service account authentication.

Do not assume that an interactive corporate SSO login can be reused for API authentication.

If a required Oracle capability is unavailable, identify the limitation clearly.

## 11. Project Structure

Propose a maintainable project structure similar to:

```text
OracleEPMTaskManager/
├── app/
│   ├── main.py
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── connection_view.py
│   │   ├── template_view.py
│   │   ├── schedule_view.py
│   │   └── deployment_view.py
│   ├── services/
│   │   ├── oracle_client.py
│   │   ├── template_importer.py
│   │   ├── schedule_deployer.py
│   │   ├── naming_service.py
│   │   └── period_service.py
│   ├── models/
│   └── utils/
├── config/
├── tests/
├── assets/
├── requirements.txt
├── build.ps1
└── README.md
```

Adjust the structure if needed.

Keep business logic separate from UI components.

Do not create unnecessary abstractions or excessive boilerplate.

## 12. Windows Distribution

The final application must be distributable to finance employees.

Package it as a Windows executable using PyInstaller or an equivalent suitable tool.

Finance employees should not need to install Python or use a command-line interface.

The application should support:

- Windows 10 and Windows 11
- Standard corporate user accounts
- Configurable Oracle FCCS environment
- Secure credential handling
- Local application logs
- Clear error messages
- A simple installation or distribution process

Consider corporate endpoint security and restrictions on installing external executables.

## 13. Testing Requirements

Create meaningful tests covering:

- Excel template validation
- Schedule naming
- Month range selection
- Year transitions
- Leap years
- Custom fiscal period mapping
- Day Zero Date calculations
- Duplicate detection
- Invalid credentials
- Missing Oracle permissions
- API failures
- Asynchronous job failures
- Partial deployment
- Repeated deployment
- UI responsiveness

Mock Oracle API responses in automated tests.

Do not run destructive tests against a production environment.

## 14. Development Workflow

Work in stages.

### Stage 1 — Research

Verify official Oracle functionality.

Determine how an Excel Task Manager template can be imported and how monthly schedules can be deployed.

Provide official Oracle documentation references.

### Stage 2 — Architecture

Propose the integration design, UI structure, configuration format, and deployment workflow.

### Stage 3 — Prototype

Implement a working CustomTkinter interface with Excel selection, month range controls, automatic naming, and schedule preview.

### Stage 4 — Oracle Integration

Implement real Oracle authentication, template import where supported, schedule deployment, and result verification.

### Stage 5 — Production Readiness

Add secure configuration, error handling, logging, packaging, and tests.

## 15. Important Development Rules

- All UI text must be in English.
- Never invent Oracle API endpoints.
- Do not assume Excel import is identical to Schedule deployment.
- Do not silently discard Excel columns or task attributes.
- Do not hardcode month names in the deployment logic.
- Do not assume a calendar year equals an Oracle fiscal year.
- Never overwrite existing schedules without explicit confirmation.
- Use official Oracle APIs and supported EPM Automate commands.
- Prefer maintainability and reliability over unnecessary complexity.
- Keep the application simple enough for non-technical finance employees.

## 16. First Response Expected

Before writing the application, provide:

1. Technical feasibility assessment.
2. Official Oracle documentation references.
3. Explanation of how Excel templates will be imported.
4. Explanation of how Task Manager Schedules will be created.
5. Recommended architecture.
6. Proposed CustomTkinter UI.
7. Automatic naming strategy.
8. Development plan.
9. Technical limitations and questions that must be resolved.

Do not immediately generate the entire codebase.

Start by validating the Oracle integration approach and designing the application architecture.
