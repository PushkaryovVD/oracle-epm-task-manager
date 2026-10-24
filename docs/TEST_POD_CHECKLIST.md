# Test-pod checklist — Stage 4 Oracle integration

Run **only on a TEST environment** (host contains `-test-`). The application never deletes Oracle objects:
remove the test schedules manually in Task Manager → Schedules after the session.

Record every result in the "Result" column. Copy Oracle's message text **exactly** (screenshot or Export
Report) — several settings below are filled from these texts. Log file: `%APPDATA%\EPMTaskDeployer\logs\app.log`
(passwords are masked). Deployment history: `%APPDATA%\EPMTaskDeployer\ledger\<environment>.json`.

Suggested template: **NA Monthly Close** (14 tasks, includes ARCS integration tasks and approvers).
Use a schedule name pattern that cannot clash with real schedules, e.g. `ZZ TEST {MMMM} {YYYY}`.

## A. Connection and authentication

| # | Steps | Expected | Result | Feeds |
|---|---|---|---|---|
| T1 | Add Environment with the test URL, your user name and password → **Test Connection** | "Connected", REST version listed, application name shown | | — |
| T2 | Edit environment, enter a wrong password → Test Connection | Clear "Oracle rejected the user name or password" message; nothing else happens | | — |
| T3 | (If available) user with SSO-only or MFA login → Test Connection | Message about SSO/MFA or 401. Record the HTTP status shown in the log | | DESIGN Q6 |
| T4 | Restart the app | Environment is listed, connects without asking for the password again | | one-time credentials |
| T5 | Windows → Credential Manager → Windows Credentials | Exactly **one** entry `EPMTaskDeployer:<id>` per environment | | — |

## B. Year / Period verification (Get Member)

| # | Steps | Expected | Result | Feeds |
|---|---|---|---|---|
| T6 | Range Jan–Mar of the current year → **Check in Oracle** | All rows **Ready**, "0 missing members" | | — |
| T7 | Period mapping with a wrong year format (e.g. `{YYYY}` instead of `FY{YY}`) → Check in Oracle | Rows **Blocked**: "Year member '2027' does not exist". Record HTTP status for a missing member in the log | **2026-10-23: Oracle answers HTTP 400** "The member P05 does not exist for the specified cube or you do not have access to it." (not 404). Handled since then: only the affected rows are blocked | Get Member behaviour |
| T8 | Same check signed in as a **non-administrator** user | Either works, or "could not be verified … Service Administrator". Record which | | DESIGN Q7 |

## C. Deployment

| # | Steps | Expected | Result | Feeds |
|---|---|---|---|---|
| T9 | Deploy **one** month (`ZZ TEST January …`) | Status goes Deploying → Completed; summary shows 1 created. Record job duration | | job timeout |
| T10 | Open the schedule in Oracle | Name exact; Year/Period correct; task dates = Day Zero + offsets (e.g. StartDate 9 → 9 days after Day Zero); schedule status **Pending** (not Open); approvers, questions, ARCS parameters present | | Q8, Q10 |
| T11 | Select the **same month again** in the app | Preview shows **Skipped - Already Exists** ("Already deployed by this application"); Deploy sends nothing for it | | ledger |
| T12 | To see Oracle's own duplicate response: close the app, rename `ledger\<environment>.json` (test pod only!), start the app and deploy the same name again. Restore the file afterwards | Record Oracle's exact error text. Put a specific fragment of it into `config/oracle_messages.json` → `schedule_exists_patterns` | **2026-10-23: HTTP 400 "You must specify a unique schedule name."** (no job started). Added to `schedule_exists_patterns` | **Q1** |
| T13 | Deploy with a template name that does not exist (`NA Monthly Close X`) | Failed with Oracle's message; next months still attempted. Record text | | messages |
| T14 | Deploy a non-admin user (if T8 allowed) | Completed, or 403 "not allowed … Service Administrator" and the run stops. Record | | **Q7** |
| T15 | Org Unit: deploy one month with a valid organizational unit, one with a non-existing name | Valid: holiday rules applied to dates. Invalid: record Oracle's message | | **Q9** |
| T16 | Day Zero strategies: last calendar day / last business day / fixed day 5 | Task dates in Oracle shift accordingly | | Q8 |

## D. Names (Q4)

Deploy one month each with these patterns and record accepted / rejected + message:

| # | Pattern | Result |
|---|---|---|
| T17 | 80 characters | |
| T18 | 120 characters | |
| T19 | 255 characters | |
| T20 | `ZZ TEST / {MM}` and each of `\ : * ? " < > \|` | |
| T21 | `ZZ TEST & ' % # {MM}` | |
| T22 | Non-ASCII: `ZZ TEST Январь {MM}` | |

Then set `max_length` / `forbidden_chars` in `config/naming_rules.json` to the observed limits and
`enforce_provisional_limits` to `true`.

## E. Robustness

| # | Steps | Expected | Result |
|---|---|---|---|
| T23 | Deploy Jan–Dec (12 months) | 12 Completed (or Skipped for earlier ones); UI stays responsive; summary + Export Report | |
| T24 | During a 6-month run press **Cancel Remaining** | Current month finishes; the rest stay Pending; Retry deploys only them | |
| T25 | During a run disconnect VPN/network for ~1 minute | Affected month becomes **Unknown - Check Oracle** (if the request had been sent) or Failed; nothing is re-sent automatically | |
| T26 | Resolve Unknown: check the schedule in Oracle, choose "Exists" or "Not in Oracle", then Retry | "Exists" → skipped; "Not in Oracle" → deployed once | |
| T27 | Change your Oracle password while the app is open, then Deploy | Run stops with "rejected the credentials"; Update Password; Retry continues with the remaining months only | |
| T28 | Corporate proxy / TLS inspection PC | Connection works (Windows certificate store and proxy are used). If not, record the error | |

## After the session

1. Delete the `ZZ TEST …` schedules in Oracle manually.
2. Send back: this file with results, `app.log`, and the exported reports.
3. Developer updates `config/oracle_messages.json`, `config/naming_rules.json`, the default job timeout and
   DESIGN.md §10a (Q1, Q4, Q7, Q9) from the results.
