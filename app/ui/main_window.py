"""Main application window: wires the views to the services.

Oracle is contacted only after an explicit click: Test Connection (environment dialog), Check in Oracle,
or Deploy Schedules → Confirm and Deploy. Oracle work runs in worker threads; Tk widgets are touched only on
the main thread (results come back through queues drained with after()).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from app.models import Environment, MonthRef, PlannedSchedule, ScheduleStatus
from app.services.credential_store import CredentialStore, CredentialStoreError
from app.services.deployment_ledger import DeploymentLedger, LedgerStatus
from app.services.environment_store import EnvironmentStore
from app.services.member_validator import MemberCheckResult, check_members
from app.services.naming_service import MONTH_NAMES
from app.services.period_service import DAY_ZERO_STRATEGIES, load_period_mappings
from app.services.report_service import DeploymentRunInfo, ReportData, write_report_xlsx
from app.services.schedule_deployer import (
    DeployEvent, DeploySummary, ScheduleDeployer, StopReason, recheck_unknown,
)
from app.services.schedule_planner import PlanRequest, PlanResult, plan
from app.ui import theme as t
from app.ui.deployment_dialogs import (
    CompletionInfo, CompletionSummaryDialog, DeploymentHistoryDialog, ResolveUnknownDialog, RetryConfirmDialog,
    format_duration,
)
from app.ui.deployment_view import DeploymentView
from app.ui.dialogs import ConfirmDeploymentDialog, DeploymentSummary, ask_confirm, show_message
from app.ui.environment_view import EnvironmentSidebar, service_type_info, supports_tm_deploy
from app.ui.help import open_user_guide
from app.ui.oracle_support import AuthWatchClient, ClientFactory, default_client_factory
from app.ui.preview_table import PreviewTable
from app.ui.schedule_view import ScheduleParams, ScheduleView
from app.ui.template_view import TemplateView
from app.ui.widgets import BackgroundTask, Tooltip, secondary_button
from app.utils.paths import app_data_dir, logs_dir

log = logging.getLogger(__name__)

APP_TITLE = "Oracle EPM Task Manager – Schedule Deployer"
MIN_W, MIN_H = 1200, 760
REPLAN_DELAY_MS = 200
DRAIN_MS = 100
APPEARANCE_MODES = ("Light", "Dark", "System")
CHECK_MAX_AGE = timedelta(minutes=30)   # an older Oracle check is repeated before deploying
CHECKING_TEXT = "Checking Year/Period in Oracle…"

RUNNING_STATUSES = (ScheduleStatus.DEPLOYING, ScheduleStatus.VALIDATING)
RETRYABLE = (ScheduleStatus.FAILED, ScheduleStatus.PENDING)

# What the user should do next, per stop reason (shown in the completion summary)
STOP_HINTS = {
    StopReason.CANCELLED: "The remaining months were not started. Press Retry Failed to deploy them later.",
    StopReason.AUTHENTICATION: "Update the password, then press Retry Failed to deploy the remaining months. "
                               "Schedules already created are never deployed twice.",
    StopReason.PERMISSION: "Ask your Oracle administrator for the Service Administrator role (or use a service "
                           "account that has it), then press Retry Failed.",
    StopReason.TEMPLATE: "Check that the Oracle template name in step 1 matches the template in Task Manager "
                         "exactly, then press Retry Failed.",
    StopReason.HISTORY: "Do not retry yet. The deployment history could not be written, so duplicates cannot be "
                        "prevented. Fix the disk space or permissions of %APPDATA%\\EPMTaskDeployer\\ledger "
                        "first, then check the unknown schedule in Oracle.",
    StopReason.ERROR: "An unexpected error stopped the run. Check the schedule marked Unknown in Oracle and see "
                      "the application log for details.",
}


# --------------------------------------------------------------------------- small UI preferences
def _settings_path() -> Path:
    return app_data_dir() / "ui_settings.json"


def load_ui_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ui_settings(data: dict) -> None:
    try:
        with open(_settings_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        log.warning("UI settings could not be saved")


def period_label(start: MonthRef, end: MonthRef) -> str:
    """'January–December 2027', 'March 2027' or 'November 2026–February 2027'."""
    s, e = MONTH_NAMES[start.month - 1], MONTH_NAMES[end.month - 1]
    if start == end:
        return f"{s} {start.year}"
    if start.year == end.year:
        return f"{s}–{e} {end.year}"
    return f"{s} {start.year}–{e} {end.year}"


@dataclass
class RunState:
    """One deployment run (the last one stays available for Retry Failed and Export Report)."""

    env: Environment
    started: datetime
    all_rows: list[PlannedSchedule]       # every preview row of the run (shared with the deployer)
    rows: list[PlannedSchedule]           # rows handed to the deployer
    template_name: str
    params: ScheduleParams
    retry: bool = False
    finished: datetime | None = None
    summary: DeploySummary | None = None
    report_path: str = ""
    plan_errors: list[str] = field(default_factory=list)

    def count(self, *statuses: ScheduleStatus) -> int:
        return sum(1 for r in self.all_rows if r.status in statuses)

    @property
    def not_started(self) -> int:
        """Rows handed to the deployer that were never deployed (run stopped or cancelled)."""
        return sum(1 for r in self.rows if r.status in (ScheduleStatus.PENDING, ScheduleStatus.READY,
                                                       ScheduleStatus.READY_UNCHECKED, *RUNNING_STATUSES))


# --------------------------------------------------------------------------- window
class MainWindow(ctk.CTk):
    def __init__(self, env_store: EnvironmentStore | None = None, cred_store: CredentialStore | None = None,
                 client_factory: ClientFactory | None = None, deploy_options: dict | None = None):
        """client_factory(base_url, username, password) -> OracleClient; deploy_options: extra keyword
        arguments for ScheduleDeployer (tests use them to shorten job polling)."""
        settings = load_ui_settings()
        mode = settings.get("appearance_mode", "System")
        ctk.set_appearance_mode(mode if mode in APPEARANCE_MODES else "System")
        super().__init__(fg_color=t.BG)
        self._settings = settings
        self.title(APP_TITLE)
        self._fit_to_screen()

        self.env_store = env_store or EnvironmentStore()
        self.cred_store = cred_store or CredentialStore()
        self.client_factory = client_factory or default_client_factory
        self.deploy_options = dict(deploy_options or {})
        self.mappings = load_period_mappings()
        self.env: Environment | None = None
        self.result: PlanResult | None = None
        self.ledger: DeploymentLedger | None = None
        self._ledger_warned: set[str] = set()
        self._replan_job: str | None = None
        self._export_task: BackgroundTask | None = None
        # Oracle member check (invalidated when environment, range, pattern, mapping or template change)
        self.oracle_check: MemberCheckResult | None = None
        self._check_key: tuple | None = None
        self._check_time: datetime | None = None
        self._check_task: BackgroundTask | None = None
        # Deployment
        self.run: RunState | None = None
        self._deployer: ScheduleDeployer | None = None
        self._deploy_queue: queue.Queue | None = None
        self._showing_results = False
        self._close_after_run = False

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header(mode)

        self.sidebar = EnvironmentSidebar(self, self.env_store, self.cred_store, self.mappings,
                                          on_change=self._on_environment, client_factory=self.client_factory)
        self.sidebar.grid(row=1, column=0, sticky="nsw")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=1, sticky="nsew", padx=t.LG, pady=t.LG)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=1)

        self.template = TemplateView(content, on_change=self.request_replan)
        self.template.grid(row=0, column=0, sticky="ew")
        self.schedule = ScheduleView(content, self.mappings, on_change=self.request_replan)
        self.schedule.grid(row=1, column=0, sticky="ew", pady=(t.MD, 0))
        self.preview = PreviewTable(content, on_check=self.check_in_oracle)
        self.preview.grid(row=2, column=0, sticky="nsew", pady=(t.MD, 0))
        self.deployment = DeploymentView(content, on_deploy=self.deploy, on_export=self.export_report,
                                         on_retry=self.retry_failed, on_cancel=self.cancel_remaining,
                                         on_resolve=self.resolve_unknown, on_history=self.show_history)
        self.deployment.grid(row=3, column=0, sticky="ew", pady=(t.MD, 0))

        self.bind("<Control-o>", lambda _e: self.template.browse())
        self.bind_all("<F1>", self.open_help)  # also works while a dialog has the focus
        self.report_callback_exception = self._on_callback_error
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # The sidebar fired on_change before the schedule view existed: apply the environment now.
        self._on_environment(self.sidebar.current)
        self.after(300, self._startup_checks)

    # ------------------------------------------------------------------ layout helpers
    def _fit_to_screen(self):
        scale = self._get_window_scaling()
        screen_w = int(self.winfo_screenwidth() / scale)
        screen_h = int(self.winfo_screenheight() / scale)
        min_w, min_h = min(MIN_W, screen_w - 40), min(MIN_H, screen_h - 80)
        self.minsize(min_w, min_h)
        width, height = min(max(1320, min_w), screen_w - 40), min(max(840, min_h), screen_h - 80)
        x = int((self.winfo_screenwidth() - width * scale) / 2)
        y = max(0, int((self.winfo_screenheight() - height * scale) / 2) - 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_header(self, mode: str):
        header = ctk.CTkFrame(self, fg_color=t.SURFACE, corner_radius=0, height=56, border_width=0)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="EPM", width=40, height=28, corner_radius=6, fg_color=t.PRIMARY,
                     text_color=t.TEXT_ON_ACCENT, font=t.font("small_bold")).grid(
            row=0, column=0, padx=(t.LG, t.MD), pady=t.MD)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(titles, text=APP_TITLE, font=t.font("h2"), text_color=t.TEXT, anchor="w",
                     height=20).pack(anchor="w")
        ctk.CTkLabel(titles, text="Create monthly Task Manager schedules from one template", height=16,
                     font=t.font("caption"), text_color=t.TEXT_MUTED, anchor="w").pack(anchor="w")
        theme_box = ctk.CTkFrame(header, fg_color="transparent")
        theme_box.grid(row=0, column=2, padx=t.LG)
        ctk.CTkLabel(theme_box, text="Theme", font=t.font("small"), text_color=t.TEXT_MUTED).pack(
            side="left", padx=(0, t.SM))
        self.mode_switch = ctk.CTkSegmentedButton(
            theme_box, values=list(APPEARANCE_MODES), command=self._set_mode, font=t.font("small"),
            height=28, fg_color=t.SURFACE_ALT, selected_color=t.PRIMARY, selected_hover_color=t.PRIMARY_HOVER,
            unselected_color=t.SURFACE_ALT, unselected_hover_color=t.BORDER, text_color=t.TEXT,
        )
        self.mode_switch.set(mode if mode in APPEARANCE_MODES else "System")
        self.mode_switch.pack(side="left")
        self.help_btn = secondary_button(theme_box, "?  Help", self.open_help, width=84)
        self.help_btn.configure(height=28)
        self.help_btn.pack(side="left", padx=(t.MD, 0))
        Tooltip(self.help_btn, "Open the user guide (F1)")
        ctk.CTkFrame(self, height=1, fg_color=t.BORDER, corner_radius=0).grid(
            row=0, column=0, columnspan=2, sticky="sew")

    def open_help(self, _event=None):
        open_user_guide(self.grab_current() or self)

    def _set_mode(self, mode: str):
        ctk.set_appearance_mode(mode)
        self._settings["appearance_mode"] = mode
        save_ui_settings(self._settings)
        self.after(50, self.preview.apply_style)

    def _on_callback_error(self, exc_type, exc, tb):
        log.error("Unhandled UI error", exc_info=(exc_type, exc, tb))
        try:
            show_message(self, "Unexpected error",
                         f"Something went wrong: {exc}\n\nDetails were written to the application log.", "error")
        except Exception:
            pass

    def _startup_checks(self):
        if self.env_store.load_error:
            show_message(self, "Saved environments", self.env_store.load_error, "warning")
        self._warn_ledger()
        if not self.env_store.list():
            self.sidebar.add_environment(first_run=True)

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self._deployer is not None

    @property
    def busy(self) -> bool:
        return self.running or self._check_task is not None

    def _on_environment(self, env: Environment | None):
        previous = self.env
        self.env = env
        if not hasattr(self, "deployment"):
            return  # still building the window
        same = env is not None and self.ledger is not None and self.ledger.environment_id == env.id
        if not same:
            self._load_ledger(env)
            self.run = None           # Retry Failed belongs to the environment of the run
            self.deployment.set_progress(0, "No deployment in progress")
            self.deployment.set_progress_idle_colour(None)
            self.deployment.set_counters(0, 0, 0, 0)
            self.schedule.apply_environment_defaults(env)
            self.after(400, self._warn_ledger)
        if same and self._showing_results and previous == env and not self.running:
            # e.g. the password was updated after a run: keep the run results on screen
            self._update_readiness()
            self._refresh_run_buttons()
            return
        self._update_readiness()
        self.request_replan()

    def _load_ledger(self, env: Environment | None):
        self.ledger = None
        if env is None:
            return
        try:
            self.ledger = DeploymentLedger(env.id)
        except OSError as exc:
            log.error("Deployment ledger for '%s' could not be opened: %s", env.id, exc)
            show_message(self, "Deployment history",
                         f"The deployment history of this environment could not be opened: {exc}", "error")

    def _warn_ledger(self):
        ledger = self.ledger
        if ledger and ledger.load_error and ledger.environment_id not in self._ledger_warned:
            self._ledger_warned.add(ledger.environment_id)
            show_message(self, "Deployment history", ledger.load_error, "warning")

    def _fingerprint(self) -> tuple:
        env = self.env
        params = self.schedule.params()
        return (
            env.id if env else None, env.base_url if env else None, env.username if env else None,
            env.application_name if env else None, env.year_dimension if env else None,
            env.period_dimension if env else None, env.verify_members if env else None,
            params.start, params.end, params.name_pattern, params.mapping_id,
            self.template.template_name, str(self.template.file_path or ""), self.template.is_valid,
        )

    def _fresh_check(self) -> MemberCheckResult | None:
        """The stored Oracle check, or None (and dropped) when it no longer matches the configuration."""
        if self.oracle_check is None:
            return None
        if self._check_key != self._fingerprint() or (
                self._check_time and datetime.now() - self._check_time > CHECK_MAX_AGE):
            log.info("Oracle check invalidated (configuration changed or check too old)")
            self.oracle_check = None
            self._check_key = self._check_time = None
        return self.oracle_check

    def request_replan(self):
        """Debounced: the preview is recomputed 200 ms after the last change."""
        if self.running:
            return  # the preview shows the live deployment
        if self._replan_job:
            self.after_cancel(self._replan_job)
        self._replan_job = self.after(REPLAN_DELAY_MS, self.replan)

    def _plan_request(self) -> PlanRequest | None:
        params = self.schedule.params()
        mapping = self.mappings.get(params.mapping_id)
        if mapping is None:
            return None
        return PlanRequest(
            start=params.start, end=params.end, name_pattern=params.name_pattern,
            template_name=self.template.template_name, mapping=mapping,
            day_zero_strategy=params.day_zero_strategy, day_zero_fixed_day=params.day_zero_fixed_day,
            org_unit=params.org_unit, template_valid=self.template.is_valid,
            existing_names=None,  # Oracle has no API that lists schedules (DESIGN §10a, Q2)
            oracle_check=self._fresh_check(), ledger=self.ledger,
        )

    def replan(self) -> PlanResult | None:
        if self._replan_job:
            self.after_cancel(self._replan_job)
        self._replan_job = None
        if self.running:
            return self.result
        if self._showing_results:  # leaving the results of the last run: reset the run display
            self._showing_results = False
            self.deployment.set_progress(0, "No deployment in progress")
            self.deployment.set_progress_idle_colour(None)
            self.deployment.set_counters(0, 0, 0, 0)
        request = self._plan_request()
        if request is None:
            self.result = None
            self.preview.show(None, "No period mapping is configured (config/period_mappings).")
            self._after_plan_change()
            return None
        try:
            self.result = plan(request)
        except Exception as exc:  # defensive: the preview must never crash the window
            log.exception("Planning failed")
            self.result = None
            self.preview.show(None, f"The preview could not be calculated: {exc}")
            self._after_plan_change()
            return None
        self.preview.show(self.result)
        self._after_plan_change()
        return self.result

    def _after_plan_change(self):
        self._update_check_status()
        self._update_readiness()
        self._refresh_run_buttons()

    def _readiness_plan(self) -> PlanResult | None:
        """While the preview shows run results, readiness is judged on what a new plan would contain."""
        if not self._showing_results:
            return self.result
        request = self._plan_request()
        try:
            return plan(request) if request else None
        except Exception:
            return None

    def _readiness_reasons(self) -> list[str]:
        reasons = []
        env = self.env
        if self.running:
            reasons.append("wait for the deployment to finish.")
        if env is None:
            reasons.append("add or select an Oracle environment.")
        elif not supports_tm_deploy(env):
            reasons.append(f"Task Manager is not available in {service_type_info(env.service_type)['display_name']}.")
        elif not self.sidebar.has_credentials:
            reasons.append("save the password for this environment (Manage… → Update Password).")
        if self.template.validating:
            reasons.append("wait for the template validation to finish.")
        elif self.template.inspection is None:
            reasons.append("select and validate the Excel template.")
        elif not self.template.inspection.is_valid:
            reasons.append("fix the template errors (see Details).")
        result = self._readiness_plan()
        if result is None:
            reasons.append("the preview could not be calculated.")
        elif result.errors:
            reasons.append("resolve the errors shown above the preview.")
        elif not result.deployable:
            reasons.append("no schedule in the preview is ready to deploy (select a row to see why).")
        return [r[0].upper() + r[1:] for r in reasons]

    def _update_readiness(self):
        reasons = self._readiness_reasons()
        ready_text = ""
        production = bool(self.env and self.env.is_production)
        if not reasons:
            result = self._readiness_plan()
            n = len(result.deployable) if result else 0
            target = f"{self.env.display_name} ({'PRODUCTION' if production else 'TEST'})"
            ready_text = f"Ready: {n} schedule(s) for {target}. You will review everything before deployment."
            if self.oracle_check is None and self.env.verify_members:
                ready_text += " Year/Period are checked in Oracle first."
        self.deployment.set_readiness(
            reasons, ready_text, production,
            can_export=bool(self.result and self.result.rows) and self._export_task is None and not self.busy,
            busy_text=CHECKING_TEXT if self._check_task else "")

    def _refresh_run_buttons(self):
        run = self.run
        retry = [r for r in run.rows if r.status in RETRYABLE] if run and run.finished and self.env and \
            run.env.id == self.env.id else []
        self.deployment.set_retry(0 if self.busy else len(retry))
        unknown = len(self.ledger.with_status(LedgerStatus.UNKNOWN)) if self.ledger else 0
        self.deployment.set_unknown(unknown)

    def _set_locked(self, locked: bool):
        self.sidebar.set_locked(locked)
        self.template.set_locked(locked)
        self.schedule.set_locked(locked)
        self.preview.set_check_enabled(not locked, busy=self._check_task is not None)

    def _update_check_status(self):
        check, env = self.oracle_check, self.env
        if self._check_task:
            self.preview.set_check_status(CHECKING_TEXT, "info")
        elif check is None:
            if env and not env.verify_members:
                self.preview.set_check_status("Year/Period verification is switched off for this environment "
                                              "(Manage… → Edit). Members are not checked in Oracle.", "warning")
            else:
                self.preview.set_check_status("Not checked in Oracle yet. Year/Period members are verified "
                                              "automatically before deployment, or click Check in Oracle.")
        elif check.error:
            self.preview.set_check_status("✕ " + check.error, "danger")
        elif check.skipped:
            self.preview.set_check_status("Year/Period verification is switched off for this environment – "
                                          "members were not checked in Oracle.", "warning")
        else:
            missing = sorted(check.missing_years | check.missing_periods)
            text = (f"✓ Checked in Oracle {self._check_time:%H:%M} · application {check.application} · "
                    f"{len(missing)} missing member{'' if len(missing) == 1 else 's'}")
            if missing:
                text += ": " + ", ".join(missing) + " (those months are blocked)"
            self.preview.set_check_status(text, "warning" if missing else "success")

    # ------------------------------------------------------------------ Oracle helpers
    def _password(self, env: Environment) -> str | None:
        try:
            password = self.cred_store.get(env.id, env.username)
        except CredentialStoreError as exc:
            show_message(self, "Password not available", str(exc), "error")
            return None
        if not password:
            show_message(self, "No password saved",
                         f"No password is saved for “{env.display_name}”. Use Manage… → Update Password.",
                         "warning")
            return None
        return password

    def _offer_password_update(self, env: Environment) -> bool:
        if not ask_confirm(self, "Oracle rejected the password",
                           f"Oracle rejected the user name or password for “{env.display_name}” ({env.username}). "
                           "Update the saved password now?", confirm_text="Update Password…", kind="error"):
            return False
        if self.sidebar.update_password():
            # The failed check is no longer meaningful with the new password
            self.oracle_check = None
            self._check_key = self._check_time = None
            if not self._showing_results:
                self.replan()
            self._update_check_status()
            show_message(self, "Password saved", "The password was updated. Try again.", "success")
            return True
        return False

    # ------------------------------------------------------------------ Check in Oracle
    def check_in_oracle(self, then: Callable[[], None] | None = None):
        if self.busy:
            return
        env = self.env
        if env is None:
            show_message(self, "No environment", "Add or select an Oracle environment first.", "info")
            return
        if not supports_tm_deploy(env):
            show_message(self, "Not available", f"Task Manager is not available in "
                                                f"{service_type_info(env.service_type)['display_name']}.", "info")
            return
        result = self.replan() if not self._showing_results else self._readiness_plan()
        if result is None or not result.rows:
            show_message(self, "Nothing to check", "The preview contains no schedules to check.", "info")
            return
        if result.errors and not any(r.year_member or r.period_member for r in result.rows):
            show_message(self, "Nothing to check", "Resolve the errors shown above the preview first.", "info")
            return
        password = self._password(env)
        if password is None:
            return
        key = self._fingerprint()
        rows = copy.deepcopy(result.rows)
        env_snapshot = dataclasses.replace(env)
        client = AuthWatchClient(self.client_factory(env.base_url, env.username, password))
        del password
        log.info("Oracle member check started for environment '%s' (%d month(s))", env.id, len(rows))
        self._check_task = BackgroundTask(
            self, lambda: check_members(client, env_snapshot, rows),
            lambda res: self._check_done(res, key, client, env_snapshot, then), self._check_failed)
        self._set_locked(True)
        self._update_check_status()
        self._update_readiness()
        self._refresh_run_buttons()
        self._check_task.start()

    def _check_finished(self):
        self._check_task = None
        self._set_locked(False)

    def _check_done(self, check: MemberCheckResult, key: tuple, client: AuthWatchClient, env: Environment,
                    then: Callable[[], None] | None):
        self._check_finished()
        self.oracle_check, self._check_key, self._check_time = check, key, datetime.now()
        log.info("Oracle member check finished: verified=%s skipped=%s missing=%d error=%s", check.verified,
                 check.skipped, len(check.missing_years | check.missing_periods), bool(check.error))
        self.replan()
        if client.authentication_failed:
            self._offer_password_update(env)
            return
        if then is not None:
            then()

    def _check_failed(self, exc: BaseException):
        self._check_finished()
        self._update_check_status()
        self._update_readiness()
        self._refresh_run_buttons()
        self.preview.set_check_status(f"✕ The Oracle check failed: {exc}", "danger")

    # ------------------------------------------------------------------ deploy
    def deploy(self):
        if self.busy:
            return
        result = self.replan()  # use the latest validation results
        if self._readiness_reasons() or result is None:
            return
        if self.oracle_check is None:
            self.check_in_oracle(then=self._confirm_and_deploy)
            return
        self._confirm_and_deploy()

    def _members_note(self) -> tuple[bool, str]:
        check = self.oracle_check
        if check is None:
            return False, "Not verified"
        if check.skipped:
            return False, "Not verified (switched off in the environment settings)"
        missing = check.missing_years | check.missing_periods
        note = f"Verified in Oracle at {self._check_time:%H:%M}"
        if missing:
            note += f" · {len(missing)} missing: " + ", ".join(sorted(missing)) + " (months blocked)"
        return check.verified, note

    def _confirm_and_deploy(self):
        result = self.result
        if result is None or self.env is None:
            return
        deployable = result.deployable
        if not deployable:
            check = self.oracle_check
            reason = check.error if check and check.error else "Select a row in the preview to see why."
            show_message(self, "Nothing to deploy", f"No schedule is ready to deploy.\n\n{reason}", "warning")
            return
        env, params = self.env, self.schedule.params()
        verified, note = self._members_note()
        summary = DeploymentSummary(
            environment_name=env.display_name,
            host=env.base_url,
            is_production=env.is_production,
            service_name=service_type_info(env.service_type)["id"],
            source_template=self.template.template_name,
            template_file=self.template.file_path.name if self.template.file_path else "",
            name_pattern=params.name_pattern,
            example_name=deployable[0].name,
            period_label=period_label(params.start, params.end),
            requested=len(result.rows),
            new=len(deployable),
            skip=result.count(ScheduleStatus.SKIPPED_EXISTS),
            blocked=result.count(ScheduleStatus.BLOCKED),
            members_verified=verified,
            org_unit=params.org_unit,
            application=self.oracle_check.application if self.oracle_check else "",
            members_note=note,
        )
        log.info("Deployment confirmation opened for environment '%s' (%d schedule(s))", env.id, len(deployable))
        if ConfirmDeploymentDialog(self, summary).show():
            log.info("Deployment confirmed by user")
            self._start_deployment(result, deployable, retry=False)
        else:
            log.info("Deployment cancelled in the confirmation dialog")

    def _start_deployment(self, result: PlanResult, rows: list[PlannedSchedule], retry: bool):
        env = self.env
        if env is None or self.ledger is None:
            show_message(self, "Cannot deploy", "The deployment history of this environment is not available.",
                         "error")
            return
        password = self._password(env)
        if password is None:
            return
        client = self.client_factory(env.base_url, env.username, password)
        del password
        params = self.schedule.params()
        self.run = RunState(env=dataclasses.replace(env), started=datetime.now(), all_rows=result.rows,
                            rows=list(rows), template_name=self.template.template_name, params=params, retry=retry,
                            plan_errors=list(result.errors))
        q: queue.Queue = queue.Queue()
        options = {"job_timeout_seconds": env.job_timeout_minutes * 60, **self.deploy_options}
        self._deployer = ScheduleDeployer(client, self.ledger, self.template.template_name, params.org_unit,
                                          rows, q.put, **options)
        self._deploy_queue = q
        self._showing_results = True
        self._close_after_run = False

        self._set_locked(True)
        self.deployment.set_running(True, production=env.is_production)
        self.deployment.set_counters(0, self.run.count(ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED),
                                     0, 0)
        self.deployment.set_progress(0.01, f"Starting deployment of {len(rows)} schedule(s)…", "info")
        self._update_readiness()
        self._refresh_run_buttons()
        self.preview.show(result)
        log.info("Deployment started: environment '%s', %d schedule(s)%s", env.id, len(rows),
                 " (retry)" if retry else "")
        threading.Thread(target=self._deploy_worker, args=(self._deployer, q), daemon=True,
                         name="schedule-deployer").start()
        self.after(DRAIN_MS, self._drain_deploy_queue)

    @staticmethod
    def _deploy_worker(deployer: ScheduleDeployer, q: queue.Queue):
        try:
            deployer.run()
        except BaseException as exc:  # the deployer reports "finished" itself; this is a last resort
            log.exception("Deployment worker crashed")
            q.put(("crash", exc))

    def _drain_deploy_queue(self):
        q = self._deploy_queue
        if q is None:
            return
        latest: DeployEvent | None = None
        for _ in range(500):
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                self._deploy_finished(None, crash=item[1])
                return
            if item.kind == "row" and item.row is not None:
                self.preview.update_row(item.row, reveal=item.row.status in RUNNING_STATUSES)
                self._update_counters()
            elif item.kind == "progress":
                latest = item
            elif item.kind == "finished":
                self._deploy_finished(item.summary)
                return
        if latest is not None:
            self._show_progress(latest)
        self.after(DRAIN_MS, self._drain_deploy_queue)

    def _show_progress(self, event: DeployEvent):
        if self._close_after_run:
            return
        name = event.row.name if event.row else ""
        total = max(1, event.total)
        self.deployment.set_progress(max(0.01, (event.position - 1) / total),
                                     f"Deploying schedule {event.position} of {event.total}: {name} — "
                                     f"{event.operation}", "info")

    def _update_counters(self):
        run = self.run
        if run:
            self.deployment.set_counters(run.count(ScheduleStatus.COMPLETED),
                                         run.count(ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED),
                                         run.count(ScheduleStatus.FAILED), run.count(ScheduleStatus.UNKNOWN))

    def _deploy_finished(self, summary: DeploySummary | None, crash: BaseException | None = None):
        run = self.run
        self._deployer = None
        self._deploy_queue = None
        if run is not None:
            run.finished = datetime.now()
            if summary is None:
                summary = DeploySummary(stop_reason=StopReason.ERROR, stop_message=str(crash or ""))
            run.summary = summary
        self._set_locked(False)
        self.deployment.set_running(False)
        self.preview.show(self.result)
        self._update_counters()
        self._update_readiness()
        self._refresh_run_buttons()
        if run is None:
            return
        problems = run.count(ScheduleStatus.FAILED, ScheduleStatus.UNKNOWN, ScheduleStatus.PENDING)
        tone = "danger" if summary.stop_reason else ("warning" if problems else "success")
        duration = format_duration((run.finished - run.started).total_seconds())
        text = (f"Finished in {duration}: {run.count(ScheduleStatus.COMPLETED)} created, "
                f"{run.count(ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED)} skipped, "
                f"{run.count(ScheduleStatus.FAILED)} failed, {run.count(ScheduleStatus.UNKNOWN)} unknown")
        if run.count(ScheduleStatus.PENDING):
            text += f", {run.count(ScheduleStatus.PENDING)} not started"
        if summary.stop_reason:
            text += f". Stopped: {summary.stop_reason.value}"
        self.deployment.set_progress(1.0, text + ".", tone)
        self.deployment.set_progress_idle_colour(tone)
        self.deployment.reason.configure(text="Results are shown in the preview. Changing any setting starts a "
                                              "new preview.", text_color=t.TEXT_MUTED)
        log.info("Deployment finished for environment '%s': %s", run.env.id, text)
        if self._close_after_run:
            self.destroy()
            return
        self._show_completion()

    def _show_completion(self):
        run = self.run
        if run is None or run.summary is None:
            return
        summary = run.summary
        reason = summary.stop_reason
        auth = reason == StopReason.AUTHENTICATION
        info = CompletionInfo(
            environment_name=run.env.display_name, host=run.env.host, is_production=run.env.is_production,
            requested=len(run.all_rows),
            completed=run.count(ScheduleStatus.COMPLETED),
            skipped=run.count(ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED),
            failed=run.count(ScheduleStatus.FAILED),
            unknown=run.count(ScheduleStatus.UNKNOWN),
            not_started=run.not_started,
            blocked=run.count(ScheduleStatus.BLOCKED),
            duration_seconds=(run.finished - run.started).total_seconds() if run.finished else 0,
            stop_reason=reason.value if reason else "",
            stop_message=summary.stop_message,
            stop_hint=STOP_HINTS.get(reason, "") if reason else "",
            authentication_stop=auth,
            log_path=str(logs_dir() / "app.log"),
            report_path=run.report_path,
        )
        choice = CompletionSummaryDialog(
            self, info, on_export=lambda saved: self.export_report(on_saved=saved),
            on_update_password=self._update_password_after_stop if auth else None).show()
        if choice == "view":
            self.preview.focus_results()

    def _update_password_after_stop(self) -> bool:
        if self.sidebar.update_password():
            self._refresh_run_buttons()
            return True
        return False

    # ------------------------------------------------------------------ cancel / close
    def cancel_remaining(self):
        if not self.running:
            return
        if not ask_confirm(self, "Cancel remaining schedules?",
                           "Stop after the current month? A running Oracle job cannot be cancelled.",
                           confirm_text="Stop After Current", cancel_text="Keep Deploying", danger=True):
            return
        if self._deployer is not None:
            self._deployer.cancel()
            self.deployment.set_cancelling()
            log.info("User requested to stop the deployment after the current month")

    def _on_close(self):
        if not self.running:
            self.destroy()
            return
        if self._close_after_run:
            return
        if ask_confirm(self, "Deployment in progress",
                       "A deployment is running. Stop after the current month and then close the application? "
                       "A running Oracle job cannot be cancelled; its result is recorded before closing.",
                       confirm_text="Stop and Close", cancel_text="Keep Deploying", danger=True):
            if self._deployer is not None:
                self._close_after_run = True
                self._deployer.cancel()
                self.deployment.set_cancelling()
                self.deployment.set_progress(0.99, "Closing after the current month…", "warning")

    # ------------------------------------------------------------------ retry / unknown / history
    def retry_failed(self):
        run = self.run
        if self.busy or run is None or self.env is None or run.env.id != self.env.id:
            return
        names = {r.name.casefold() for r in run.rows if r.status in RETRYABLE}
        if not names:
            return
        result = self.replan()  # the ledger turns everything already created into "skipped"
        if result is None:
            return
        if self.oracle_check is None:
            self.check_in_oracle(then=lambda: self._confirm_retry(names))
            return
        self._confirm_retry(names)

    def _confirm_retry(self, names: set[str]):
        result = self.result
        if result is None or self.env is None:
            return
        rows = [r for r in result.rows if r.name.casefold() in names
                and r.status in (ScheduleStatus.READY, ScheduleStatus.READY_UNCHECKED)]
        excluded = [r.name for r in result.rows if r.name.casefold() in names and r not in rows]
        if not rows:
            detail = ("\n\n" + "\n".join(f"• {r.name}: {r.status.value}" for r in result.rows
                                         if r.name.casefold() in names)) if excluded else ""
            show_message(self, "Nothing to retry",
                         "None of the failed schedules can be deployed again with the current settings. Select a "
                         "row in the preview to see why." + detail, "warning")
            return
        if RetryConfirmDialog(self, self.env, rows, excluded).show():
            log.info("Retry confirmed for %d schedule(s)", len(rows))
            self._start_deployment(result, rows, retry=True)

    def resolve_unknown(self):
        if self.busy or self.env is None or self.ledger is None:
            return
        env = self.env

        def recheck_factory():
            password = self._password(env)
            if password is None:
                return None
            client = self.client_factory(env.base_url, env.username, password)
            return lambda: recheck_unknown(client, self.ledger)

        if ResolveUnknownDialog(self, env, self.ledger, recheck_factory).show():
            log.info("Unknown deployment results resolved by the user")
            self.replan()
        self._refresh_run_buttons()

    def show_history(self):
        if self.env is None or self.ledger is None:
            show_message(self, "Deployment history", "Select an environment first.", "info")
            return
        DeploymentHistoryDialog(self, self.env, self.ledger).show()

    # ------------------------------------------------------------------ export
    def export_report(self, on_saved: Callable[[Path], None] | None = None):
        if not self.result or not self.result.rows or self._export_task or self.running:
            return
        with_run = self._showing_results and self.run is not None
        stamp = f"{datetime.now():%Y%m%d_%H%M}"
        default = f"Schedule_Deployment_Report_{stamp}.xlsx" if with_run else f"Schedule_Preview_{stamp}.xlsx"
        parent = self.grab_current() or self
        path = ctk.filedialog.asksaveasfilename(
            parent=parent, title="Export report", defaultextension=".xlsx", initialfile=default,
            filetypes=[("Excel workbook", "*.xlsx")])
        if not path:
            return
        data = self._report_data(with_run)
        self.deployment.set_exporting(True)
        self._export_task = BackgroundTask(self, lambda: write_report_xlsx(path, data),
                                           lambda p: self._export_done(p, with_run, on_saved),
                                           self._export_failed).start()

    def _report_data(self, with_run: bool) -> ReportData:
        run = self.run if with_run else None
        params = run.params if run else self.schedule.params()
        env = run.env if run else self.env
        inspection = self.template.inspection
        mapping = self.mappings.get(params.mapping_id)
        check = self.oracle_check
        if check is None:
            check_text = "Not checked in Oracle"
        elif check.error:
            check_text = check.error
        elif check.skipped:
            check_text = "Switched off in the environment settings"
        else:
            missing = sorted(check.missing_years | check.missing_periods)
            check_text = (f"Verified {self._check_time:%Y-%m-%d %H:%M} in application {check.application}; "
                          f"{len(missing)} missing member(s)" + (": " + ", ".join(missing) if missing else ""))
        summary = [
            ("Environment", env.display_name if env else "(none)"),
            ("Host", env.base_url if env else ""),
            ("Environment type", ("PRODUCTION" if env.is_production else "TEST") if env else ""),
            ("Oracle user", env.username if env else ""),
            ("Template file", str(self.template.file_path) if self.template.file_path else "(none)"),
            ("Oracle template name", run.template_name if run else self.template.template_name),
            ("Template status", ("Valid" if inspection.is_valid else "Invalid") if inspection else "Not validated"),
            ("Tasks in template", str(inspection.task_count) if inspection else ""),
            ("Schedule name pattern", params.name_pattern),
            ("Period", period_label(params.start, params.end)),
            ("Period mapping", mapping.display_name if mapping else params.mapping_id),
            ("Day Zero date", DAY_ZERO_STRATEGIES.get(params.day_zero_strategy, params.day_zero_strategy)
             + (f" (day {params.day_zero_fixed_day})" if params.day_zero_strategy == "fixed_day" else "")),
            ("Organizational Unit", params.org_unit or "(standard date mapping)"),
            ("Totals", self.preview.summary.cget("text")),
            ("Year/Period check", check_text),
            ("Duplicate protection", "Local deployment history and Oracle's response during deployment "
                                     "(Oracle has no API that lists schedules)"),
        ]
        rows = copy.deepcopy(self.result.rows)
        run_info = None
        job_ids: dict[str, str] = {}
        if run is not None:
            run_info = DeploymentRunInfo(
                started=run.started, finished=run.finished, requested=len(run.all_rows),
                completed=run.count(ScheduleStatus.COMPLETED),
                skipped=run.count(ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED),
                failed=run.count(ScheduleStatus.FAILED), unknown=run.count(ScheduleStatus.UNKNOWN),
                not_started=run.not_started,
                blocked=run.count(ScheduleStatus.BLOCKED),
                stop_reason=run.summary.stop_reason.value if run.summary and run.summary.stop_reason else "",
                stop_message=run.summary.stop_message if run.summary else "",
            )
            if self.ledger:
                for r in rows:
                    entry = self.ledger.get(r.name)
                    if entry and entry.job_id:
                        job_ids[r.name] = entry.job_id
        return ReportData(
            rows=rows, summary=summary, plan_errors=list(self.result.errors),
            issues=list(inspection.issues) if inspection else [],
            unknown_columns=list(inspection.unknown_columns) if inspection else [],
            run=run_info, job_ids=job_ids, history=self.ledger.entries() if self.ledger else [],
        )

    def _export_done(self, path: Path, with_run: bool, on_saved: Callable[[Path], None] | None):
        self._export_task = None
        self.deployment.set_exporting(False)
        self._update_readiness()
        if with_run and self.run is not None:
            self.run.report_path = str(path)
        log.info("Report exported to %s", path)
        if on_saved is not None:
            on_saved(path)
        else:
            show_message(self, "Report exported", f"The report was saved to:\n{path}", "success")

    def _export_failed(self, exc: BaseException):
        self._export_task = None
        self.deployment.set_exporting(False)
        self._update_readiness()
        show_message(self, "Export failed",
                     f"The report could not be saved. If the file is open in Excel, close it and try again.\n\n{exc}",
                     "error")
