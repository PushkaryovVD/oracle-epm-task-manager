"""FSSG Oracle EPM operational task catalog - single source of truth.

Every task here feeds both deliverables:
  * the human-readable Excel catalog (all fields), and
  * the Oracle Task Manager import CSVs (48-column structure of the FSSG draft).

Text is kept ASCII-only so the CSV survives any import code page.
"""

APPS_ALL = "All (FCCS, ARCS, EDMCS, Planning, Narrative Reporting)"

NO_TAIL = ("A No answer does not by itself block completion: record the incident/SR number, "
           "the exception and the agreed next step in the task comments and attach the evidence "
           "before submitting.")

DAILY = [
    dict(
        id="FSSG_D001",
        name="[DAILY][ALL] Confirm EPM service availability and daily maintenance",
        app=APPS_ALL, process="Service health and backup",
        timing="Start of the FSSG business day, before Finance users start work",
        priority="High",
        purpose=("Confirms every production EPM service is reachable through SSO and that the Oracle "
                 "daily maintenance completed and produced a current Artifact Snapshot, so users can "
                 "work and a recovery point exists."),
        steps=[
            "Sign in to each production service (FCCS, ARCS, EDMCS, Planning, Narrative Reporting) "
            "through the corporate SSO URL and confirm the home page loads.",
            "Check the Oracle Cloud status page and service announcements for unplanned outages or "
            "maintenance notices affecting the services.",
            "In each service, confirm the daily maintenance completed: check the Artifact Snapshot "
            "timestamp (Migration > Snapshots) or the Activity Report and confirm it is within the "
            "last 24 hours.",
            "Confirm the daily maintenance start time still matches the approved window in the "
            "Environment Configuration Register.",
            "Record the result per service in the task comments.",
        ],
        expected=("All five services available through SSO; an Artifact Snapshot dated within the "
                  "last 24 hours exists for each; maintenance windows unchanged."),
        evidence=("Per service: sign-in time, Artifact Snapshot date/time (screenshot or Activity "
                  "Report reference); incident/SR number for any exception."),
        failure=("Service unavailable: raise a P1 incident, check the Oracle status page, open an "
                 "Oracle SR for a production outage and notify the FSSG Lead and the Finance close "
                 "lead immediately. Snapshot missing or failed: raise a P2 incident and Oracle SR, and "
                 "postpone administrative changes (metadata imports, restructures, deployments) until a "
                 "valid backup is confirmed."),
        fssg="Performs the check, manages incidents and Oracle SRs.",
        finance="None (informed of outages).",
        question=("Were all five production EPM services available through SSO and did each produce "
                  "an Artifact Snapshot within the last 24 hours?"),
        deps=[], pred="",
        config="Production and SSO URLs; approved daily maintenance window per service.",
        fin_confirm="No",
        automation=("High: scheduled EPM Automate script (login + listFiles) checking the snapshot "
                    "timestamp with alerting; the manual task then reviews the alert output."),
        draft="New (gap in draft)",
    ),
    dict(
        id="FSSG_D002",
        name="[DAILY][ALL] Review overnight scheduled jobs and next-24-hour schedule",
        app=APPS_ALL, process="Job scheduling and monitoring",
        timing="Start of the FSSG business day, after FSSG_D001",
        priority="High",
        purpose=("Detects failed, cancelled, long-running or missed scheduled jobs and confirms that "
                 "jobs due in the next 24 hours are scheduled correctly."),
        steps=[
            "Open the Jobs console in FCCS, Planning and ARCS and the job/history views in EDMCS and "
            "Narrative Reporting; filter to the period since the last check.",
            "Compare executed jobs with the Job Schedule Register. Identify jobs with status Error, "
            "Warning or Cancelled, jobs still running beyond their expected duration, and expected "
            "jobs that did not run.",
            "For each failed integration job open Data Integration Process Details and capture the "
            "process ID and log.",
            "Review the scheduled jobs list in each service and confirm that jobs due in the next 24 "
            "hours exist, are active, and have the correct time and time zone.",
            "Log an incident for every exception not already tracked and link existing incidents.",
        ],
        expected=("All expected jobs completed with Success; none missing; jobs due in the next 24 "
                  "hours scheduled correctly; every exception linked to an incident."),
        evidence=("For each exception: job name, job/process ID, status, start and end time, incident "
                  "number; note confirming reconciliation against the Job Schedule Register."),
        failure=("Re-run a failed job only if the runbook allows a re-run and the cause is understood "
                 "(for example a transient connection error); otherwise raise an incident and notify the "
                 "job owner. Close-critical job failures during the close window are P2 and the Finance "
                 "close lead is notified. A missing or disabled schedule is restored from the Job "
                 "Schedule Register under the approved change process."),
        fssg="Performs the review, re-runs per runbook, raises incidents.",
        finance="None (informed of close-critical failures).",
        question=("Did all expected overnight scheduled jobs complete successfully, with no missing or "
                  "disabled jobs due in the next 24 hours?"),
        deps=["FSSG_D001"], pred="FSSG_D001",
        config="Job Schedule Register (job, service, expected time, time zone, owner, run-as account).",
        fin_confirm="No",
        automation=("Medium: REST job-status polling or native job/pipeline email notifications for "
                    "failures; reconciliation against the Register stays manual."),
        draft="FSSG_001 (daily part) + FSSG_002 merged",
    ),
    dict(
        id="FSSG_D003",
        name="[DAILY][GB] Verify GB to EPM pipeline execution and load completeness",
        app="GB source to target EPM applications (as configured)", process="Data integration",
        timing="After the scheduled GB pipeline window",
        priority="High",
        purpose=("Confirms GB data reached the EPM applications completely and without unresolved "
                 "rejections, so Finance works with current data."),
        steps=[
            "Confirm the GB source extract for the business date was delivered (file or interface "
            "delivery confirmation defined for the GB interface).",
            "In Data Integration open Process Details for the GB pipeline/integrations and confirm "
            "each scheduled run ended with Success.",
            "For each integration compare records extracted with records imported and loaded to the "
            "target; record counts and, where available, control totals.",
            "Review the process log and Workbench for rejected rows, unmapped members and validation "
            "errors.",
            "Record the results and raise incidents for exceptions.",
        ],
        expected=("All scheduled GB runs succeeded; source and loaded record counts reconcile (or the "
                  "difference is explained by approved filters); no unresolved rejected or unmapped "
                  "records."),
        evidence=("Pipeline/integration name, process ID, status, timestamps, source vs loaded record "
                  "counts, rejected record count, log extract for exceptions, incident numbers."),
        failure=("Source file missing or late: contact the GB interface owner and raise an incident. "
                 "Load failure: review the log, correct the technical cause and re-run per runbook. "
                 "Unmapped members: notify the mapping/data owner; FSSG does not create or change "
                 "mappings without an approved request. Partial load during the close window: P2 and "
                 "notify the Finance close lead."),
        fssg="Verifies technical completeness, fixes technical causes, re-runs loads.",
        finance="Approves mapping changes; validates loaded balances.",
        question=("Did all scheduled GB pipeline runs complete successfully with reconciled record "
                  "counts and no unresolved rejected or unmapped records?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config="GB pipeline and integration names, delivery mechanism, expected run times, count method.",
        fin_confirm="No (Finance involved only for mapping exceptions)",
        automation=("High: pipeline failure email notification and scripted count reconciliation from "
                    "process details; manual task reviews exceptions."),
        draft="FSSG_003 kept and expanded",
    ),
    dict(
        id="FSSG_D004",
        name="[DAILY][EBS] Verify Oracle EBS to EPM pipeline execution and load completeness",
        app="Oracle EBS source to target EPM applications (as configured)", process="Data integration",
        timing="After the scheduled EBS pipeline window",
        priority="High",
        purpose=("Confirms Oracle EBS data reached the EPM applications completely and without "
                 "unresolved rejections."),
        steps=[
            "Confirm the EBS extract step completed (EBS concurrent request, EPM Integration Agent or "
            "file delivery, whichever the configured integration uses).",
            "In Data Integration open Process Details for the EBS pipeline/integrations and confirm "
            "each scheduled run ended with Success and loaded the expected period.",
            "Compare records extracted with records imported and loaded; record counts and, where "
            "available, control totals.",
            "Review the process log and Workbench for rejected rows, unmapped members and validation "
            "errors.",
            "Record the results and raise incidents for exceptions.",
        ],
        expected=("All scheduled EBS runs succeeded for the expected period; counts reconcile; no "
                  "unresolved rejected or unmapped records."),
        evidence=("Integration name, process ID, status, period, timestamps, source vs loaded record "
                  "counts, rejected record count, incident numbers."),
        failure=("Extract or connection failure: contact the EBS support team, check the integration "
                 "credentials/agent, raise an incident. Load failure: correct the technical cause and "
                 "re-run per runbook. Unmapped members: notify the mapping/data owner. Close-window "
                 "impact: P2 and notify the Finance close lead."),
        fssg="Verifies technical completeness, fixes technical causes, re-runs loads.",
        finance="Approves mapping changes; validates loaded balances.",
        question=("Did all scheduled Oracle EBS pipeline runs complete successfully with reconciled "
                  "record counts and no unresolved rejected or unmapped records?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config="EBS integration names and connection method, expected run times, count method.",
        fin_confirm="No (Finance involved only for mapping exceptions)",
        automation="High: as FSSG_D003.",
        draft="FSSG_004 kept and expanded",
    ),
    dict(
        id="FSSG_D005",
        name="[DAILY][FX] Verify daily FX rate load against the approved source",
        app="FCCS, Planning, ARCS (where daily rates are used)", process="FX rates",
        timing="After the daily FX rate load",
        priority="High",
        purpose=("Confirms daily FX rates are complete and identical to the approved source, preventing "
                 "translation errors. Applies only if daily rates are loaded; otherwise set Active = No."),
        steps=[
            "Obtain the approved daily FX rate source for the business date as defined in the "
            "Environment Configuration Register.",
            "Confirm the FX rate load job/integration ended with Success; record the process ID.",
            "Confirm every currency pair in the Currency Pair Register is loaded for the correct date "
            "or period, rate type and scenario, with no blank or zero rates.",
            "Compare loaded rates with the approved source for all pairs (preferred: exported rates "
            "vs source file). Rates must match to the precision of the source.",
            "Record expected, loaded and matched pair counts and any differences.",
        ],
        expected="Load succeeded; 100% of expected pairs present; all loaded rates match the approved source.",
        evidence=("Source file name and timestamp, load process ID and status, comparison output "
                  "(pairs expected/loaded/matched), list of differences, incident numbers."),
        failure=("Source missing: contact the Treasury/rate source owner; do not key rates manually "
                 "unless Finance provides approved rates in writing. Mismatch or missing pairs: correct "
                 "the cause, reload from the approved source and notify the Finance rate owner. FSSG "
                 "never approves rate values."),
        fssg="Loads and technically compares rates with the approved source.",
        finance="Owns the rate source and values; approves any exception.",
        question=("Were all expected daily FX rates loaded successfully and do they match the approved "
                  "source for every currency pair?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config=("Approved rate source, Currency Pair Register, rate types, load integration name, "
                "whether daily rates are used at all."),
        fin_confirm="Only for exceptions",
        automation=("High: scripted comparison of the source file with exported rates producing an "
                    "exception file; manual task reviews it."),
        draft="FSSG_005 kept and expanded",
    ),
    dict(
        id="FSSG_D006",
        name="[DAILY][FCCS] Verify FCCS loads and scheduled consolidation jobs",
        app="FCCS", process="Data integration and consolidation jobs",
        timing="After FSSG_D003 and FSSG_D004",
        priority="Medium",
        purpose=("Confirms FCCS-specific loads and any scheduled consolidation, translation and business "
                 "rule jobs ran without technical errors and within expected duration. Source pipeline "
                 "health is covered by FSSG_D003/FSSG_D004 and is not repeated here."),
        steps=[
            "Review FCCS-only scheduled loads listed in the Job Schedule Register and confirm Success.",
            "In the Jobs console review scheduled Consolidation, Translation, business rule and Refresh "
            "Database jobs: status, errors/warnings and duration against the baseline.",
            "For failures capture the job log and check for metadata or rule changes in the last 24 "
            "hours (EDMCS synchronization, deployments).",
            "Note entities left Impacted by a failed job and pass the list to Finance for information "
            "(FSSG does not judge business results).",
        ],
        expected=("All FCCS scheduled loads and consolidation-related jobs succeeded within expected "
                  "duration."),
        evidence="Job names, job IDs, status, duration, log extract for failures, incident numbers.",
        failure=("Raise an incident with the job log. During the close window do not re-run consolidation "
                 "without agreement from the Finance close lead, because a re-run changes data status. "
                 "Significant performance degradation: incident and Oracle SR with the Activity Report."),
        fssg="Monitors job health and investigates technical failures.",
        finance="Runs or approves consolidations; validates consolidated results and data status.",
        question=("Did all FCCS scheduled data loads and consolidation-related jobs complete successfully "
                  "within their expected duration?"),
        deps=["FSSG_D003", "FSSG_D004"], pred="FSSG_D003",
        config="FCCS scheduled jobs in the Job Schedule Register; duration baselines.",
        fin_confirm="No",
        automation="Medium: job failure notification; duration trend from the Activity Report.",
        draft="FSSG_006 re-scoped (removed overlap with FSSG_003/004)",
    ),
    dict(
        id="FSSG_D007",
        name="[DAILY][ARCS] Verify ARCS data imports and matching jobs",
        app="ARCS", process="Reconciliation data integration",
        timing="After the scheduled ARCS import window",
        priority="Medium",
        purpose=("Confirms balance and transaction data needed by preparers was imported and that "
                 "scheduled matching jobs ran without technical errors."),
        steps=[
            "In the ARCS Jobs view review scheduled balance imports (Reconciliation Compliance) and, if "
            "Transaction Matching is used, transaction loads and auto-match runs.",
            "Confirm each job ended with Success and processed the expected period.",
            "Review import logs for rejected records and records not assigned to a reconciliation "
            "profile.",
            "Record results; raise incidents for technical failures.",
        ],
        expected="All scheduled ARCS imports and matching jobs succeeded; no unresolved rejected records.",
        evidence="Job names, job IDs, status, period, rejected record counts, incident numbers.",
        failure=("Technical failure: correct the cause and re-run per runbook, raise an incident. "
                 "Records without a profile or unmatched business items are passed to the Finance "
                 "reconciliation owner; FSSG does not change profiles or match transactions."),
        fssg="Monitors imports and matching job health.",
        finance="Prepares, reviews and signs off reconciliations; resolves unmatched items; owns profiles.",
        question=("Did all scheduled ARCS data imports and matching jobs complete successfully with no "
                  "unresolved rejected records?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config="ARCS data load definitions, schedules, use of Transaction Matching.",
        fin_confirm="No",
        automation="Medium: job failure notification.",
        draft="FSSG_007 kept and re-worded",
    ),
    dict(
        id="FSSG_D008",
        name="[DAILY][EDMCS] Verify EDMCS request, subscription and export processing",
        app="EDMCS", process="Metadata management",
        timing="During the FSSG business day",
        priority="Medium",
        purpose=("Detects technical failures in metadata requests, subscriptions and exports before they "
                 "cause metadata gaps in target applications."),
        steps=[
            "Review requests with failed validations or stuck in processing; report requests waiting "
            "on approval beyond the agreed time to the data owners (information only).",
            "Review subscription requests generated in the last 24 hours and any subscription errors.",
            "Review scheduled exports/extracts and the related metadata import jobs in the target "
            "applications (FCCS, Planning, ARCS) for errors.",
            "Record results; raise incidents for technical failures.",
        ],
        expected=("No technical failures in requests, subscriptions, exports or target metadata imports; "
                  "pending approvals reported to data owners."),
        evidence="Request IDs, subscription/export job references, target import job IDs, incident numbers.",
        failure=("Technical error: capture the validation/export message, correct the technical cause "
                 "and re-run, raise an incident. Business validation failures go back to the requester "
                 "or data owner; FSSG does not approve metadata requests."),
        fssg="Monitors processing and target imports; fixes technical causes.",
        finance="Data owners raise and approve metadata requests.",
        question=("Were all EDMCS requests, subscriptions and scheduled exports processed without "
                  "technical errors in the last 24 hours?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config="Subscriptions, export/extract definitions, target import jobs, data owner list.",
        fin_confirm="No",
        automation="Medium: scheduled export/import scripts with failure alerting.",
        draft="FSSG_008 kept",
    ),
    dict(
        id="FSSG_D009",
        name="[DAILY][Planning] Verify Planning data loads, business rules and data maps",
        app="Planning", process="Calculations and integrations",
        timing="After the overnight Planning job window",
        priority="Medium",
        purpose=("Confirms scheduled Planning loads, calculations and data movements ran without "
                 "technical errors, so planners see current data."),
        steps=[
            "Review the Planning Jobs console for scheduled data loads, business rules/rulesets, data "
            "maps/Smart Push and Refresh Database jobs.",
            "Confirm each job ended with Success and within expected duration.",
            "For data loads review rejected records; for rules review error messages in the job log.",
            "Record results; raise incidents for failures.",
        ],
        expected="All scheduled Planning jobs succeeded within expected duration with no rejected records.",
        evidence="Job names, job IDs, status, duration, rejected record counts, incident numbers.",
        failure=("Correct the technical cause and re-run per runbook; raise an incident. Do not re-run "
                 "calculations that overwrite planner data without agreement from the FP&A owner."),
        fssg="Monitors job health; re-runs per runbook.",
        finance="FP&A validates plan/forecast results.",
        question=("Did all scheduled Planning data loads, business rules and data maps complete "
                  "successfully?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config="Planning scheduled jobs in the Job Schedule Register.",
        fin_confirm="No",
        automation="Medium: job failure notification.",
        draft="FSSG_009 kept and expanded",
    ),
    dict(
        id="FSSG_D010",
        name="[DAILY][Narrative Reporting] Verify scheduled report refresh and distribution jobs",
        app="Narrative Reporting", process="Reporting refresh and distribution",
        timing="After scheduled Narrative Reporting jobs",
        priority="Low",
        purpose=("Detects failed scheduled report refreshes or distributions. Applies only where daily "
                 "Narrative Reporting jobs are configured; otherwise set Active = No."),
        steps=[
            "Review scheduled report refresh, bursting and distribution jobs in Narrative Reporting.",
            "Confirm each job ended successfully and the data source connections (FCCS, Planning) "
            "responded.",
            "Record results; raise incidents for failures.",
        ],
        expected="All scheduled refresh and distribution jobs succeeded.",
        evidence="Job/report names, status, timestamps, incident numbers.",
        failure=("Data source connection error: check the connection and credentials (see FSSG_M003) and "
                 "raise an incident. Do not re-distribute reports without the report owner's approval."),
        fssg="Monitors technical refresh and distribution.",
        finance="Report owners approve content and distribution.",
        question=("Did all scheduled Narrative Reporting refresh and distribution jobs for the day "
                  "complete successfully?"),
        deps=["FSSG_D002"], pred="FSSG_D002",
        config="Narrative Reporting scheduled jobs and data source connections.",
        fin_confirm="No",
        automation="Medium: job notifications where available.",
        draft="FSSG_010 kept; made conditional",
    ),
    dict(
        id="FSSG_D011",
        name="[DAILY][ALL] Review SSO, provisioning and access incidents",
        app=APPS_ALL, process="Security and access",
        timing="During the FSSG business day",
        priority="Medium",
        purpose=("Ensures authentication, provisioning and authorization issues are triaged quickly and "
                 "that every access change is backed by an approved request."),
        steps=[
            "Review new incidents and requests for SSO/authentication failures, missing roles or groups "
            "and access-denied errors across the five services.",
            "For SSO failures check whether multiple users are affected; a pattern indicates an "
            "identity provider or certificate issue and is escalated as P1/P2.",
            "For each provisioning change confirm an approved access request exists before execution "
            "and that group synchronization from the identity provider completed.",
            "Confirm any emergency or temporary access granted is time-bound and recorded.",
        ],
        expected=("All new access incidents triaged; no access change without approval; widespread SSO "
                  "issues escalated."),
        evidence="Incident/request numbers, approval references, affected users count for SSO patterns.",
        failure=("Widespread SSO failure: P1/P2, engage the identity provider team and Oracle SR if the "
                 "EPM identity domain is affected. Access change without approval: do not execute; "
                 "return to the requester. Unauthorized access found: revoke per security procedure "
                 "and inform the application owner and IT Security."),
        fssg="Triages incidents; executes approved access changes.",
        finance="Application owners approve access requests.",
        question=("Have all new SSO, provisioning and access incidents been triaged, with every access "
                  "change executed against an approved request?"),
        deps=["FSSG_D001"], pred="FSSG_D001",
        config="Incident tool queue, access request workflow, identity provider contacts.",
        fin_confirm="No",
        automation="Low: incident queue reporting.",
        draft="FSSG_011 kept and expanded",
    ),
    dict(
        id="FSSG_D012",
        name="[DAILY][ALL] Review open incidents and Oracle SRs and issue daily status",
        app=APPS_ALL, process="Incident management and reporting",
        timing="End of the FSSG business day",
        priority="Medium",
        purpose=("Keeps open incidents and Oracle SRs moving and gives Finance one daily view of EPM "
                 "operational health."),
        steps=[
            "Review open EPM incidents and Oracle SRs: severity, owner, age and next action; escalate "
            "overdue items per the escalation matrix.",
            "Update Oracle SRs that are waiting on FSSG.",
            "Issue the daily status (services, jobs, pipelines, FX, open P1/P2 items) to the FSSG Lead "
            "and the agreed Finance distribution list.",
            "Hand over open items to the next shift where applicable.",
        ],
        expected="All open items reviewed; overdue items escalated; daily status issued.",
        evidence="Daily status message or report reference; list of escalated items.",
        failure="Unowned or overdue P1/P2 item: escalate to the FSSG Lead the same day.",
        fssg="Owns incident follow-up and the daily status.",
        finance="Receives the status.",
        question=("Has the daily EPM operations status been issued with all open incidents and Oracle "
                  "SRs reviewed for ageing and escalation?"),
        deps=["FSSG_D002", "FSSG_D003", "FSSG_D004", "FSSG_D005", "FSSG_D006", "FSSG_D007",
              "FSSG_D008", "FSSG_D009", "FSSG_D010", "FSSG_D011"],
        pred="FSSG_D011",
        config="Status distribution list; incident tool.",
        fin_confirm="No",
        automation="Medium: status report generated from incident tool and job alerts.",
        draft="FSSG_012 merged with daily handover",
    ),
]

MONTHLY = [
    # ---------------- Pre-close ----------------
    dict(
        id="FSSG_M001",
        name="[MONTHLY][ALL] Verify next-month job schedules and time zone settings",
        app=APPS_ALL, process="Job scheduling",
        timing="Pre-close: last week of the month, before the calendar month changes",
        priority="High",
        purpose=("Prevents missed or mistimed loads and jobs in the coming month, including the close "
                 "window, holiday calendar and daylight saving changes."),
        steps=[
            "Export or list scheduled jobs and Data Integration schedules in each service and compare "
            "them with the Job Schedule Register: frequency, start time, end date, parameters and "
            "run-as account.",
            "Confirm the time zone of every schedule and the daily maintenance window; check daylight "
            "saving transitions in the coming month for each time zone involved, including GB and EBS "
            "source extract times.",
            "Confirm the sequence of upstream and downstream jobs still holds after any time zone "
            "shift (source extract before EPM load, load before calculation).",
            "Confirm schedules are aligned with the Finance close calendar for the coming close.",
            "Confirm next month's FSSG Task Manager schedules (daily and monthly) are created from the "
            "current templates.",
            "Correct differences under the approved change process and update the Register.",
        ],
        expected=("All schedules match the Register and the close calendar; time zones and DST effects "
                  "verified; next month's Task Manager schedules exist."),
        evidence=("Schedule export or screenshots per service, Register reconciliation note, change "
                  "references for corrections, Task Manager schedule names."),
        failure=("Difference found: correct under change control before month end; if the close window "
                 "is affected, inform the Finance close lead. Close calendar not available: request it "
                 "from the Finance close lead and record the dependency."),
        fssg="Verifies and maintains schedules.",
        finance="Provides and confirms the close calendar.",
        question=("Are all next-month job schedules, time zones and Task Manager schedules verified "
                  "against the Job Schedule Register and the Finance close calendar?"),
        deps=[], pred="",
        config="Job Schedule Register; Finance close calendar; Task Manager template names.",
        fin_confirm="Yes - Finance confirms the close calendar",
        automation=("Medium: schedule extract and diff against the Register; Task Manager schedules can "
                    "be deployed with the Schedule Deployer in this repository."),
        draft="FSSG_001 (future-month part) + FSSG_013 merged",
    ),
    dict(
        id="FSSG_M002",
        name="[MONTHLY][ALL] Assess Oracle monthly update impact on the close",
        app=APPS_ALL, process="Change and release management",
        timing="Pre-close: after the Test environment receives the monthly update",
        priority="Medium",
        purpose=("Confirms the Oracle monthly update does not break close-critical processes and does "
                 "not land in the close window unassessed."),
        steps=[
            "Review the Oracle readiness / What's New documents for the upcoming update for each service.",
            "Confirm the Test and Production update dates for each service against the close calendar.",
            "After the Test update, regression-test close-critical processes in Test: GB and EBS loads, "
            "FX load, consolidation, ARCS import, EDMCS export, Planning rules, Narrative Reporting "
            "refresh.",
            "If a defect is found or the Production update conflicts with the close, raise an Oracle "
            "SR and, with FSSG Lead approval, evaluate deferring the update (EPM Automate skipUpdate, "
            "subject to Oracle policy).",
            "Communicate the outcome to the FSSG Lead and the Finance close lead.",
        ],
        expected="Regression tests passed in Test; no unresolved update issue affecting the close.",
        evidence="Regression test results, update dates, Oracle SR numbers, deferral decision if any.",
        failure=("Regression defect: Oracle SR, workaround plan and decision on deferral before the "
                 "Production update date; inform the Finance close lead."),
        fssg="Performs regression testing and update risk assessment.",
        finance="Informed; may support business regression checks.",
        question=("Has the upcoming Oracle monthly update been regression-tested in the Test environment "
                  "with no unresolved issues affecting the close?"),
        deps=[], pred="",
        config="Test environment URLs; regression test checklist.",
        fin_confirm="No",
        automation="Medium: scripted regression runs of integrations in Test.",
        draft="New (gap in draft)",
    ),
    dict(
        id="FSSG_M003",
        name="[MONTHLY][ALL] Verify integration credentials and SSO certificate validity",
        app=APPS_ALL + " and GB/EBS integrations", process="Security and integration continuity",
        timing="Pre-close",
        priority="High",
        purpose=("Prevents close failures caused by expired passwords, tokens or certificates used by "
                 "integrations, scripts and SSO."),
        steps=[
            "List the service and integration accounts used by EPM Automate scripts, Data Integration "
            "connections (GB, EBS), Narrative Reporting data source connections and scheduled jobs.",
            "Check password and token expiry dates and the validity of encrypted password files used by "
            "scripts.",
            "Check expiry dates of the SAML/SSO signing certificates (identity provider and EPM identity "
            "domain) and any certificates used by integration agents.",
            "For anything expiring within 60 days (recommended threshold), schedule renewal under change "
            "control outside the close window.",
        ],
        expected="All credentials and certificates valid beyond the next close; renewals scheduled.",
        evidence="Credential and certificate register with expiry dates; change references for renewals.",
        failure=("Expired or expiring before the close: P2, renew immediately under emergency change and "
                 "test the affected integration; inform the FSSG Lead."),
        fssg="Maintains the credential and certificate register.",
        finance="None.",
        question=("Are all integration credentials and SSO certificates valid beyond the next close "
                  "cycle, with renewals scheduled for any expiring within 60 days?"),
        deps=[], pred="",
        config="Credential and certificate register (accounts, owners, expiry dates).",
        fin_confirm="No",
        automation="Medium: expiry reminders in the credential register.",
        draft="New (gap in draft)",
    ),
    dict(
        id="FSSG_M004",
        name="[MONTHLY][EDMCS] Verify metadata synchronization to target applications",
        app="EDMCS to FCCS, Planning, ARCS", process="Metadata synchronization",
        timing="Pre-close: at the metadata cut-off in the close calendar",
        priority="High",
        purpose=("Confirms every approved metadata change for the period is in each target application "
                 "before data loads and consolidation, preventing rejected loads and wrong roll-ups."),
        steps=[
            "Agree with the data owners the list of requests approved for the period and the metadata "
            "cut-off.",
            "Confirm all those requests are Completed in EDMCS and none intended for the period are "
            "still in flight.",
            "Run or verify the subscriptions and exports to FCCS, Planning and ARCS and the metadata "
            "import job in each target.",
            "Confirm Refresh Database / cube refresh completed in FCCS and Planning after the import.",
            "Compare EDMCS viewpoints with the target dimensions (EDMCS compare or member counts) for "
            "the changed dimensions.",
            "Obtain data owner confirmation of the metadata freeze for the close.",
        ],
        expected=("All approved changes present in every target; imports and refreshes succeeded; "
                  "metadata freeze confirmed."),
        evidence=("Request list, export/import job IDs, refresh job IDs, comparison result, data owner "
                  "confirmation."),
        failure=("Import or refresh failure: correct and re-run before data loads start; if not possible, "
                 "P2 and inform the Finance close lead because loads may reject. Differences in "
                 "comparison: investigate with the data owner before the freeze."),
        fssg="Runs and verifies synchronization.",
        finance="Data owners approve requests and confirm the metadata freeze.",
        question=("Have all approved metadata changes for the period been synchronized from EDMCS to "
                  "every target application without errors?"),
        deps=[], pred="",
        config="Subscriptions/exports per target, metadata cut-off date, data owner list.",
        fin_confirm="Yes - data owners confirm the metadata freeze",
        automation="Medium: scheduled export/import scripts; compare remains manual.",
        draft="FSSG_018 kept and expanded",
    ),
    dict(
        id="FSSG_M005",
        name="[MONTHLY][ALL] Verify period mappings and substitution variables for the close",
        app="FCCS, Planning, ARCS, Narrative Reporting", process="Period configuration",
        timing="Pre-close: before the first close data load",
        priority="High",
        purpose=("Ensures loads, forms, rules and reports point to the close period that Finance "
                 "confirmed, preventing loads into the wrong period."),
        steps=[
            "Obtain the close period values from the Finance close lead.",
            "Confirm Data Integration period mappings (global, application and source mappings) exist "
            "for the close period for FCCS, Planning and ARCS integrations, including the GB and EBS "
            "source period names.",
            "Update or verify substitution variables in FCCS and Planning (for example current period "
            "and year) per the Finance instruction.",
            "Confirm Narrative Reporting points of view or variables used by report packages reference "
            "the close period.",
            "Confirm scheduled jobs and scripts with period parameters use the correct period.",
        ],
        expected="All period mappings, variables and job parameters set to the Finance-confirmed values.",
        evidence="Screenshots or exports of period mappings and variables; Finance period confirmation.",
        failure=("Missing mapping or wrong variable: correct before loads; if loads already ran into a "
                 "wrong period, P2, stop further loads and inform the Finance close lead."),
        fssg="Configures and verifies period settings.",
        finance="Confirms period values.",
        question=("Are period mappings and substitution variables set to the Finance-confirmed close "
                  "period in all applicable applications?"),
        deps=["FSSG_M001"], pred="FSSG_M001",
        config="Period mapping names, substitution variable names, report package POVs.",
        fin_confirm="Yes - Finance confirms period values",
        automation="High: EPM Automate setSubstVars driven by the close calendar.",
        draft="FSSG_016 and FSSG_019 period parts merged",
    ),
    dict(
        id="FSSG_M006",
        name="[MONTHLY][ARCS] Verify ARCS period and reconciliation creation readiness",
        app="ARCS", process="Reconciliation period setup",
        timing="Pre-close: before the ARCS balance load",
        priority="High",
        purpose=("Confirms the ARCS period and reconciliations exist and are ready to receive balances, "
                 "so preparers can start on time."),
        steps=[
            "Confirm the period exists in ARCS and its status matches the Finance instruction.",
            "Create or verify period reconciliations (EPM Automate createReconciliations or the UI) using "
            "the Finance-defined filter; compare the reconciliation count with the expected profile count.",
            "Confirm ARCS data load definitions and period mappings for the period.",
            "If ARCS multi-currency is used, confirm the ARCS rate load is planned (see FSSG_M007).",
        ],
        expected="Period ready; reconciliation count matches expected profiles; load definitions ready.",
        evidence="Period status screenshot, reconciliation count, job IDs, Finance instruction reference.",
        failure=("Count difference: investigate profile changes with the Finance reconciliation owner "
                 "before balances load. Job failure: correct, re-run, raise incident."),
        fssg="Performs technical period setup on Finance instruction.",
        finance="Decides period status; owns profiles; signs off reconciliations.",
        question=("Is the ARCS period set up with the expected number of reconciliations and ready to "
                  "receive balances?"),
        deps=["FSSG_M005"], pred="FSSG_M005",
        config="ARCS period names, saved filter for reconciliation creation, expected profile count.",
        fin_confirm="Yes - Finance instructs period status",
        automation="High: createReconciliations in a scheduled script.",
        draft="FSSG_017 split (readiness part)",
    ),
    dict(
        id="FSSG_M007",
        name="[MONTHLY][FX] Verify month-end FX rates completeness and accuracy",
        app="FCCS, Planning, ARCS (where rates are used)", process="FX rates",
        timing="Pre-close / Close: when Finance releases month-end rates",
        priority="High",
        purpose=("Confirms month-end average and closing rates are complete and identical to the approved "
                 "source in every application before translation and consolidation."),
        steps=[
            "Obtain the approved month-end rate source (average and closing rates) from Finance/Treasury.",
            "Confirm the rate loads to FCCS and, where used, Planning and ARCS ended with Success.",
            "Completeness: confirm every currency pair in the Currency Pair Register is loaded for every "
            "rate type, the close period and every target application; no blank or zero rates.",
            "Accuracy: compare 100% of loaded rates with the approved source (export vs source file); "
            "rates must match to the precision of the source.",
            "Confirm the rate convention (direct/indirect, triangulation currency) matches the "
            "application configuration.",
            "Send the comparison result to the Finance rate owner for confirmation.",
        ],
        expected="100% of required pairs and rate types loaded in every application and matching the source.",
        evidence=("Approved source reference, load job IDs, comparison output per application, Finance "
                  "confirmation."),
        failure=("Missing or mismatched rates: reload from the approved source and repeat the comparison "
                 "before consolidation; if consolidation already ran, inform the Finance close lead that "
                 "translation must be re-run. FSSG never keys or changes rate values without written "
                 "Finance approval."),
        fssg="Loads and technically compares rates.",
        finance="Owns and approves rates; confirms the comparison.",
        question=("Are month-end average and closing FX rates loaded for every required currency pair and "
                  "application, and do they fully match the approved source?"),
        deps=["FSSG_M005"], pred="FSSG_M005",
        config="Approved rate source, Currency Pair Register, rate types, applications using rates.",
        fin_confirm="Yes - Finance rate owner confirms (Approver1 recommended)",
        automation="High: scripted comparison; manual review and Finance confirmation remain.",
        draft="FSSG_015 kept and expanded",
    ),
    dict(
        id="FSSG_M008",
        name="[MONTHLY][ALL] Month-end technical readiness checkpoint",
        app=APPS_ALL, process="Month-end technical readiness",
        timing="Pre-close: last business day before close day 1",
        priority="High",
        purpose=("Gives the Finance close lead one documented technical go/no-go status before the close "
                 "starts."),
        steps=[
            "Review the results of FSSG_M001 to FSSG_M007 and any open exceptions.",
            "Review open P1/P2 incidents and Oracle SRs that could affect the close.",
            "Confirm no Oracle update or maintenance falls in the close window (FSSG_M002).",
            "Confirm the change freeze for the close window with change management and the FSSG support "
            "coverage for close days.",
            "Send the status (Ready / Ready with exceptions / Not ready) with exceptions to the Finance "
            "close lead and record the acknowledgement.",
        ],
        expected="Readiness status communicated and acknowledged; no open P1/P2 affecting the close.",
        evidence="Readiness status message, exception list, Finance acknowledgement.",
        failure=("Not ready: agree mitigation and timing with the Finance close lead and the FSSG Lead; "
                 "record the decision. The task is completed once the status is communicated."),
        fssg="Prepares and communicates the readiness status.",
        finance="Finance close lead acknowledges and decides on close start.",
        question=("Are all pre-close technical checks complete with no open P1/P2 incident affecting the "
                  "close?"),
        deps=["FSSG_M001", "FSSG_M002", "FSSG_M003", "FSSG_M004", "FSSG_M005", "FSSG_M006", "FSSG_M007"],
        pred="FSSG_M007",
        config="Finance close lead; change freeze process.",
        fin_confirm="Yes - Finance close lead acknowledges (Approver1 recommended)",
        automation="None - judgement and communication.",
        draft="New (gap in draft)",
    ),
    # ---------------- Close ----------------
    dict(
        id="FSSG_M009",
        name="[MONTHLY][GB] Verify month-end GB loads completeness",
        app="GB source to target EPM applications", process="Month-end data integration",
        timing="Close: after each month-end GB load",
        priority="High",
        purpose=("Provides Finance with technical assurance that month-end GB data was loaded completely "
                 "for the close period."),
        steps=[
            "Confirm with Finance whether the load is the final load (source period closed) or an "
            "agreed interim load.",
            "Confirm each month-end GB integration ended with Success for the close period.",
            "Reconcile source record counts and control totals (as provided by the GB interface) with "
            "loaded values per integration.",
            "Confirm zero unresolved rejected or unmapped records.",
            "Inform Finance that data is available for financial validation.",
        ],
        expected="Month-end GB loads complete; counts and control totals reconcile; no unresolved rejections.",
        evidence="Process IDs, counts and control totals source vs target, rejection report, notification.",
        failure=("As FSSG_D003; during the close window treat as P2 and agree re-load timing with the "
                 "Finance close lead."),
        fssg="Technical completeness of the load.",
        finance="Validates balances (financial validation is a Finance task).",
        question=("Were all month-end GB loads completed for the close period with reconciled counts and "
                  "control totals and no unresolved rejections?"),
        deps=["FSSG_M008"], pred="FSSG_M008",
        config="Month-end GB integrations, control total source.",
        fin_confirm="Yes - Finance validates balances",
        automation="High: scripted count/total reconciliation.",
        draft="FSSG_014 split (GB)",
    ),
    dict(
        id="FSSG_M010",
        name="[MONTHLY][EBS] Verify month-end Oracle EBS loads completeness",
        app="Oracle EBS source to target EPM applications", process="Month-end data integration",
        timing="Close: after each month-end EBS load",
        priority="High",
        purpose=("Provides Finance with technical assurance that month-end EBS data was loaded completely "
                 "for the close period."),
        steps=[
            "Confirm with Finance whether the EBS GL period is closed (final load) or the load is an "
            "agreed interim load.",
            "Confirm each month-end EBS integration ended with Success for the close period.",
            "Reconcile source record counts and control totals (for example EBS trial balance totals "
            "provided by Finance or the EBS team) with loaded values.",
            "Confirm zero unresolved rejected or unmapped records.",
            "Inform Finance that data is available for financial validation.",
        ],
        expected="Month-end EBS loads complete; counts and control totals reconcile; no unresolved rejections.",
        evidence="Process IDs, counts and control totals source vs target, rejection report, notification.",
        failure=("As FSSG_D004; during the close window treat as P2 and agree re-load timing with the "
                 "Finance close lead."),
        fssg="Technical completeness of the load.",
        finance="Closes the EBS period; validates balances.",
        question=("Were all month-end Oracle EBS loads completed for the close period with reconciled "
                  "counts and control totals and no unresolved rejections?"),
        deps=["FSSG_M008"], pred="FSSG_M008",
        config="Month-end EBS integrations, control total source.",
        fin_confirm="Yes - Finance validates balances",
        automation="High: scripted count/total reconciliation.",
        draft="FSSG_014 split (EBS)",
    ),
    dict(
        id="FSSG_M011",
        name="[MONTHLY][FCCS] Verify consolidation and translation job execution",
        app="FCCS", process="Consolidation technical jobs",
        timing="Close: after loads (FSSG_M009, FSSG_M010) and FX (FSSG_M007)",
        priority="High",
        purpose=("Confirms consolidation, translation and related rules ran without technical errors, "
                 "so Finance reviews results produced by a complete technical run."),
        steps=[
            "Confirm month-end loads and FX rates are complete before consolidation is run.",
            "Run (where FSSG runs scheduled jobs) or monitor consolidation, translation and related "
            "business rule jobs per the agreed close process.",
            "Confirm each job ended with Success within expected duration; review logs for errors and "
            "warnings.",
            "List entities left Impacted or not consolidated because of a technical failure and send "
            "the list to Finance.",
        ],
        expected="All consolidation-related jobs succeeded; no entity left unconsolidated by a technical error.",
        evidence="Job IDs, status, duration, log extracts, list of affected entities (if any).",
        failure=("Raise a P2 incident with the log; check recent metadata or rule changes; re-run only "
                 "with agreement of the Finance close lead; open an Oracle SR for product errors or "
                 "severe performance degradation."),
        fssg="Technical execution and job health.",
        finance="Runs/approves consolidation; validates results, journals and data status.",
        question=("Did all FCCS consolidation, translation and related business rule jobs for the period "
                  "complete without technical errors?"),
        deps=["FSSG_M007", "FSSG_M009", "FSSG_M010"], pred="FSSG_M010",
        config="Agreed split of who runs consolidation (FSSG job vs Finance user).",
        fin_confirm="Yes - Finance validates consolidated results",
        automation="Medium: Task Manager or scheduled job execution once agreed.",
        draft="FSSG_016 split (consolidation part)",
    ),
    dict(
        id="FSSG_M012",
        name="[MONTHLY][ARCS] Verify ARCS period balance load for reconciliations",
        app="ARCS", process="Reconciliation data integration",
        timing="Close: after the source balances for ARCS are final",
        priority="High",
        purpose="Confirms reconciliations received complete period balances so preparers can work.",
        steps=[
            "Confirm the ARCS balance imports (source system and subsystem) for the period ended with "
            "Success.",
            "Compare the number of reconciliations with loaded balances against the reconciliations "
            "created in FSSG_M006.",
            "Review import errors and balances not assigned to a profile.",
            "Confirm auto-reconciliation and, if used, Transaction Matching period jobs ran.",
            "Inform the Finance reconciliation owner that balances are available.",
        ],
        expected="All period balances loaded; no unresolved import errors; Finance informed.",
        evidence="Job IDs, counts, error report, notification.",
        failure=("Technical failure: correct and re-load, P2 during close. Balances without a profile: "
                 "send to the Finance reconciliation owner."),
        fssg="Technical load completeness.",
        finance="Prepares, reviews and signs off reconciliations.",
        question=("Were ARCS period balances imported successfully for all reconciliations with no "
                  "unresolved import errors?"),
        deps=["FSSG_M006", "FSSG_M008", "FSSG_M009", "FSSG_M010"], pred="FSSG_M008",
        config="Source of ARCS balances (FCCS, GB or EBS) - adjust Predecessor1 once confirmed.",
        fin_confirm="No",
        automation="High: Task Manager ARCS integration task type or scheduled import.",
        draft="FSSG_017 split (balance load part)",
    ),
    dict(
        id="FSSG_M013",
        name="[MONTHLY][Planning] Verify Planning actuals load, calculations and data maps",
        app="Planning", process="Calculations and integrations",
        timing="Close: after the actuals source is final",
        priority="Medium",
        purpose=("Confirms Planning received the period actuals and that related calculations and data "
                 "movements ran without technical errors."),
        steps=[
            "Confirm the actuals load to Planning (from FCCS, GB or EBS per configuration) ended with "
            "Success for the close period; reconcile counts and control totals with the source.",
            "Confirm Refresh Database ran if metadata changed this period (FSSG_M004).",
            "Confirm aggregation and actuals-related business rules ran successfully.",
            "Confirm data maps/Smart Push to reporting cubes completed.",
            "Inform FP&A that actuals are available.",
        ],
        expected="Actuals loaded and reconciled; calculations and data maps succeeded.",
        evidence="Job IDs, counts/control totals, rule and data map job status, notification.",
        failure="Correct and re-run per runbook; raise an incident; inform FP&A of any delay.",
        fssg="Technical execution and completeness.",
        finance="FP&A validates actuals and forecast impact.",
        question=("Did the Planning actuals load, calculations and data maps for the period complete "
                  "successfully with reconciled control totals?"),
        deps=["FSSG_M005", "FSSG_M008", "FSSG_M009", "FSSG_M010"], pred="FSSG_M008",
        config="Planning actuals source - adjust Predecessor1 once confirmed (FSSG_M011 if from FCCS).",
        fin_confirm="Yes - FP&A validates actuals",
        automation="Medium: pipeline chaining load and rules.",
        draft="FSSG_019 (integration part)",
    ),
    dict(
        id="FSSG_M014",
        name="[MONTHLY][Narrative Reporting] Verify report package refresh and distribution",
        app="Narrative Reporting", process="Reporting refresh and distribution",
        timing="Close: after Finance confirms consolidated results",
        priority="Medium",
        purpose=("Confirms report packages refresh from the correct sources and period and that the "
                 "Finance-approved distribution was delivered."),
        steps=[
            "Confirm data source connections to FCCS and Planning are valid and point to Production.",
            "Confirm report package POVs/variables reference the close period (FSSG_M005).",
            "Confirm report and doclet refresh completed without errors.",
            "After Finance approves content, run or verify bursting/distribution to the approved "
            "distribution list and confirm delivery.",
        ],
        expected="Refresh succeeded for the correct period; approved distribution delivered.",
        evidence="Refresh job status, package names, distribution log, Finance release reference.",
        failure=("Refresh error: fix connection or POV and re-refresh; raise an incident. Never distribute "
                 "before Finance release."),
        fssg="Technical refresh and distribution.",
        finance="Approves report content and releases distribution.",
        question=("Did the report package refresh and the Finance-approved distribution complete without "
                  "technical errors?"),
        deps=["FSSG_M011"], pred="FSSG_M011",
        config="Report packages, data source connections, distribution lists.",
        fin_confirm="Yes - Finance releases distribution",
        automation="Medium: scheduled bursting once content is approved.",
        draft="FSSG_020 kept and expanded",
    ),
    # ---------------- Post-close ----------------
    dict(
        id="FSSG_M015",
        name="[MONTHLY][ALL] Verify period lock and roll schedules forward",
        app="FCCS, Planning, ARCS and integrations", process="Post-close controls",
        timing="Post-close: after Finance locks the period",
        priority="Medium",
        purpose=("Ensures no scheduled load or script writes into a locked period and that schedules "
                 "point to the next period."),
        steps=[
            "Obtain Finance confirmation that the period is locked/closed in FCCS and ARCS (Finance "
            "performs or instructs locking).",
            "Review Data Integration schedules and scripts with period parameters and confirm none "
            "target the locked period.",
            "Roll period parameters and substitution variables forward per the Finance instruction.",
            "Update the Job Schedule Register.",
        ],
        expected="No scheduled load targets the locked period; schedules point to the next period.",
        evidence="Finance lock confirmation, list of updated schedules/scripts, Register update.",
        failure=("Load into a locked period attempted or found: stop the schedule, raise an incident and "
                 "inform the Finance close lead."),
        fssg="Rolls schedules forward; verifies technical lock effect.",
        finance="Locks the period.",
        question="Are all scheduled loads and scripts rolled forward so that none target the locked period?",
        deps=["FSSG_M011", "FSSG_M012", "FSSG_M013", "FSSG_M014"], pred="FSSG_M014",
        config="Lock procedure per application.",
        fin_confirm="Yes - Finance confirms period lock",
        automation="High: period parameters driven by substitution variables instead of hard-coded periods.",
        draft="New (gap in draft)",
    ),
    dict(
        id="FSSG_M016",
        name="[MONTHLY][ALL] Archive post-close application snapshots",
        app=APPS_ALL, process="Backup and audit retention",
        timing="Post-close: after FSSG_M015",
        priority="Medium",
        purpose=("Keeps a recoverable, auditable copy of each application as at period close beyond "
                 "Oracle's own backup retention."),
        steps=[
            "After the period lock, download the latest Artifact Snapshot (or a snapshot created after "
            "the lock) for each production service.",
            "Store it in the approved secure repository with the period in the file name.",
            "Confirm file size and date are plausible and the retention rule is applied.",
        ],
        expected="One post-close snapshot per service stored in the approved repository.",
        evidence="Repository path, file names, sizes and dates.",
        failure="Download failure: retry, raise an incident if persistent; record the gap.",
        fssg="Performs the archive.",
        finance="None.",
        question="Has a post-close snapshot of every production service been archived in the approved repository?",
        deps=["FSSG_M015"], pred="FSSG_M015",
        config="Approved archive repository and retention rule.",
        fin_confirm="No",
        automation="High: EPM Automate downloadFile of the Artifact Snapshot in a scheduled script.",
        draft="New (gap in draft)",
    ),
    dict(
        id="FSSG_M017",
        name="[MONTHLY][ALL] Compile month-end technical evidence and exception summary",
        app=APPS_ALL, process="Audit evidence and handover",
        timing="Post-close: after FSSG_M016",
        priority="High",
        purpose="Provides an auditable record of the month-end technical controls and their exceptions.",
        steps=[
            "Export or reference the completed FSSG Task Manager tasks with answers, comments and "
            "attachments for the period.",
            "List all incidents and Oracle SRs raised in the period with status and resolution.",
            "Confirm every exception has an incident and a resolution or an agreed carry-forward.",
            "Summarise recurring issues and improvement actions.",
            "Send the summary to the FSSG Lead and the Finance close lead and store it in the evidence "
            "repository.",
        ],
        expected="Evidence pack complete; every exception linked and resolved or carried forward.",
        evidence="Evidence pack location and distribution reference.",
        failure="Missing evidence: obtain it from the task assignee before the pack is issued.",
        fssg="Compiles the evidence pack.",
        finance="Receives the summary; acknowledges carried-forward exceptions.",
        question=("Has the month-end evidence pack been compiled with every exception linked to an "
                  "incident and its resolution or agreed carry-forward?"),
        deps=["FSSG_M016"], pred="FSSG_M016",
        config="Evidence repository location.",
        fin_confirm="No (FSSG Lead review recommended as Approver1)",
        automation="Medium: Task Manager reports for task status and answers.",
        draft="FSSG_021 kept and expanded",
    ),
    dict(
        id="FSSG_M018",
        name="[MONTHLY][ALL] Review EPM user access, privileged roles and leavers",
        app=APPS_ALL, process="Security and access review",
        timing="Post-close (any time in the month, once per month)",
        priority="High",
        purpose=("Detects inappropriate, privileged or orphaned access and obtains application owner "
                 "certification."),
        steps=[
            "Generate the role assignment and user/group reports for each service (Access Control or "
            "EPM Automate roleAssignmentReport).",
            "Compare Service Administrator and other privileged assignments with the approved list.",
            "Identify leavers and movers by comparing with the identity provider/HR source.",
            "Review FSSG support, service and emergency accounts and access granted during the month.",
            "Send exceptions to the application owners for approval or removal; remediate through "
            "approved requests.",
        ],
        expected="All exceptions approved or remediated; application owners certified access.",
        evidence="Reports per service, exception list, owner approvals, remediation request numbers.",
        failure=("Unapproved privileged access: remove under the security procedure and inform IT "
                 "Security; unresolved exceptions are tracked as incidents."),
        fssg="Produces reports and remediates approved changes.",
        finance="Application owners certify access.",
        question=("Has the monthly access review been completed with all exceptions approved or "
                  "remediated by the application owners?"),
        deps=[], pred="",
        config="Approved privileged user list, application owners, identity provider source.",
        fin_confirm="Yes - application owners certify",
        automation="High: scheduled report extraction; review and certification remain manual.",
        draft="FSSG_022 kept and expanded",
    ),
]

# Draft tasks that were not carried forward one-to-one: original ID -> (disposition, new IDs, rationale)
DRAFT_REVIEW = [
    ("FSSG_001", "Split / merged", "FSSG_D002, FSSG_M001",
     "Mixed daily job check with next-month schedule review. Daily part merged into D002 (next 24 h); "
     "future-month part belongs to month-end (M001). Evidence text referred to job run IDs that do "
     "not apply to a schedule review."),
    ("FSSG_002", "Merged", "FSSG_D002",
     "Same console and same evidence as FSSG_001; one daily job review avoids duplicate effort."),
    ("FSSG_003", "Kept, expanded", "FSSG_D003", "Added source delivery check, count reconciliation and mapping boundary."),
    ("FSSG_004", "Kept, expanded", "FSSG_D004", "Added extract method, period check and counts."),
    ("FSSG_005", "Kept, made conditional", "FSSG_D005",
     "Needed only if daily rates are loaded; accuracy check made explicit (100% comparison)."),
    ("FSSG_006", "Re-scoped", "FSSG_D006",
     "Duplicated FSSG_003/004 (same loads). Now covers FCCS-only loads and consolidation-related jobs."),
    ("FSSG_007", "Kept", "FSSG_D007", "Added Transaction Matching and Finance boundary for unmatched items."),
    ("FSSG_008", "Kept", "FSSG_D008", "Added target metadata import check."),
    ("FSSG_009", "Kept, expanded", "FSSG_D009", "Added data maps and Refresh Database."),
    ("FSSG_010", "Kept, made conditional", "FSSG_D010", "Low value if no daily jobs exist - deactivate in that case."),
    ("FSSG_011", "Kept, expanded", "FSSG_D011", "Added approval check for every access change and SSO pattern escalation."),
    ("FSSG_012", "Merged", "FSSG_D012", "Combined with daily status/handover so incident review has an output."),
    ("FSSG_013", "Merged", "FSSG_M001", "Merged with the future-month part of FSSG_001; DST and Task Manager schedules added."),
    ("FSSG_014", "Split", "FSSG_M009, FSSG_M010",
     "GB and EBS have different owners, timing and failure paths; one combined answer hid which failed."),
    ("FSSG_015", "Kept, expanded", "FSSG_M007", "Added accuracy against approved source, all applications and Finance confirmation."),
    ("FSSG_016", "Split", "FSSG_M005, FSSG_M011", "Period readiness and consolidation are separate controls at different times."),
    ("FSSG_017", "Split", "FSSG_M006, FSSG_M012", "Period/reconciliation creation precedes the balance load."),
    ("FSSG_018", "Kept, expanded", "FSSG_M004", "Added cut-off, refresh, comparison and metadata freeze."),
    ("FSSG_019", "Split / merged", "FSSG_M005, FSSG_M013", "Period settings consolidated across apps in M005; integrations in M013."),
    ("FSSG_020", "Kept, expanded", "FSSG_M014", "Added distribution after Finance release."),
    ("FSSG_021", "Kept, expanded", "FSSG_M017", "Added exception linkage and carry-forward."),
    ("FSSG_022", "Kept, expanded", "FSSG_M018", "Added leavers/movers and owner certification."),
    ("(all)", "Changed", "-",
     "Draft used one generic question ('Was the verification completed successfully?') and one generic "
     "evidence sentence for all tasks, and instructed 'do not mark complete' on failure. Replaced with "
     "task-specific Yes/No questions and an explicit rule that a No answer is documented, not blocked."),
]
