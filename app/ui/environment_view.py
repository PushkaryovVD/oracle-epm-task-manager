"""Environment sidebar, Add/Edit Environment dialog, Update Password dialog and Manage Environments dialog.

Oracle is contacted only when the user clicks Test Connection. Passwords go straight from the entry widget to
Windows Credential Manager (CredentialStore) or into the Oracle client; they are never logged, displayed or kept
in an attribute.
"""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from app.models import Environment
from app.services.credential_store import CredentialStore, CredentialStoreError
from app.services.environment_store import (
    EnvironmentStore, guess_is_production, make_environment_id, normalize_base_url,
)
from app.services.oracle_client import AuthenticationError, ConnectionReport, OracleError
from app.services.period_service import DAY_ZERO_STRATEGIES, PeriodMapping
from app.ui import theme as t
from app.ui.dialogs import BaseDialog, ask_confirm, show_message
from app.ui.oracle_support import ClientFactory, connection_text, default_client_factory
from app.ui.widgets import (
    BackgroundTask, Badge, Tooltip, checkbox, entry, field_label, hint_label, link_button, option_menu,
    primary_button, secondary_button,
)
from app.utils.config import service_types

log = logging.getLogger(__name__)

TM_DEPLOY = "tm_deploy"
ARCS_NOTE = ("Task Manager is not available in Account Reconciliation; "
             "ARCS module planned separately.")
FIXED_DAYS = [str(d) for d in range(1, 32)]
MAX_JOB_TIMEOUT_MINUTES = 24 * 60
NOT_TESTED_NOTE = "Connection not tested yet – you can save now and test later."


# --------------------------------------------------------------------------- helpers (no business rules)
def service_type_info(type_id: str) -> dict:
    for item in service_types():
        if item["id"] == type_id:
            return item
    return {"id": type_id, "display_name": type_id, "capabilities": []}


def supports_tm_deploy(env: Environment | None) -> bool:
    return bool(env) and TM_DEPLOY in service_type_info(env.service_type).get("capabilities", [])


def credentials_saved(cred: CredentialStore, env: Environment) -> bool | None:
    """True / False, or None when Windows Credential Manager cannot be read."""
    try:
        return cred.has(env.id, env.username)
    except CredentialStoreError:
        log.warning("Credential Manager could not be read for environment '%s'", env.id)
        return None


def replace_credential(cred: CredentialStore, env_id: str, old_username: str | None,
                       new_username: str, password: str) -> None:
    """Store a password; CredentialStore.save removes any previous entry for the environment."""
    if old_username and old_username != new_username:
        cred.delete(env_id, old_username)
    cred.save(env_id, new_username, password)


# --------------------------------------------------------------------------- password entry with show/hide
class PasswordField(ctk.CTkFrame):
    def __init__(self, master, width: int = 260):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.entry = entry(self, width=width, show="•")
        self.entry.grid(row=0, column=0, sticky="ew")
        self._visible = False
        self._toggle = secondary_button(self, "Show", self._toggle_visibility, width=64)
        self._toggle.grid(row=0, column=1, padx=(t.SM, 0))
        Tooltip(self._toggle, "Show or hide the password while typing")

    def _toggle_visibility(self):
        self._visible = not self._visible
        self.entry.configure(show="" if self._visible else "•")
        self._toggle.configure(text="Hide" if self._visible else "Show")

    def get(self) -> str:
        return self.entry.get()

    def clear(self) -> None:
        self.entry.delete(0, "end")


# --------------------------------------------------------------------------- Add / Edit environment
class EnvironmentDialog(BaseDialog):
    """Returns the saved Environment, or None when cancelled."""

    def __init__(self, parent, store: EnvironmentStore, cred: CredentialStore,
                 mappings: dict[str, PeriodMapping], env: Environment | None = None, first_run: bool = False,
                 client_factory: ClientFactory = default_client_factory):
        title = "Edit Environment" if env else "Add Oracle Environment"
        super().__init__(parent, title, width=800)
        self.store, self.cred, self.mappings, self.env = store, cred, mappings, env
        self.client_factory = client_factory
        self._test_task: BackgroundTask | None = None
        self._tested_ok = False
        self._prod_touched = env is not None
        self._types = service_types()
        self._type_by_label = {s["display_name"]: s["id"] for s in self._types}
        self._mapping_by_label = {m.display_name: m.id for m in mappings.values()}
        self._strategy_by_label = {v: k for k, v in DAY_ZERO_STRATEGIES.items()}

        # Header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=t.XL, pady=(t.LG, 0))
        ctk.CTkLabel(head, text=title, font=t.font("title"), text_color=t.TEXT, anchor="w").pack(anchor="w")
        intro = ("Welcome! Add the Oracle EPM environment you want to deploy schedules to. "
                 "You enter the password once; it is stored securely in Windows Credential Manager."
                 if first_run else
                 "Connection details are saved on this computer. The password is stored in Windows Credential "
                 "Manager, never in a file.")
        hint_label(head, intro, wraplength=720).pack(anchor="w", pady=(t.XS, 0))

        form = ctk.CTkFrame(self, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD, border_width=1,
                            border_color=t.BORDER)
        form.grid(row=1, column=0, sticky="nsew", padx=t.XL, pady=t.MD)
        form.grid_columnconfigure((0, 1), weight=1, uniform="col")
        left = ctk.CTkFrame(form, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(t.LG, t.MD), pady=t.LG)
        right = ctk.CTkFrame(form, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(t.MD, t.LG), pady=t.LG)
        left.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # ---- left: connection
        ctk.CTkLabel(left, text="CONNECTION", font=t.font("overline"), text_color=t.TEXT_MUTED,
                     anchor="w").grid(row=0, column=0, sticky="w")
        field_label(left, "Display name").grid(row=1, column=0, sticky="w", pady=(t.SM, 0))
        self.name = entry(left, placeholder_text="e.g. FCCS – Test")
        self.name.grid(row=2, column=0, sticky="ew")

        field_label(left, "Service type").grid(row=3, column=0, sticky="w", pady=(t.SM, 0))
        self.service = option_menu(left, list(self._type_by_label), command=lambda _v: self._on_service())
        self.service.grid(row=4, column=0, sticky="ew")
        self.service_note = hint_label(left, "", tone="warning", wraplength=320)
        self.service_note.grid(row=5, column=0, sticky="w")

        field_label(left, "Environment URL").grid(row=6, column=0, sticky="w", pady=(t.SM, 0))
        self.url = entry(left, placeholder_text="https://<service>-<tenant>.epm.<region>.ocs.oraclecloud.com")
        self.url.grid(row=7, column=0, sticky="ew")
        self.url.bind("<FocusOut>", lambda _e: self._validate_url())
        self.url_hint = hint_label(left, "Paste the address of your Oracle EPM login page.", wraplength=320)
        self.url_hint.grid(row=8, column=0, sticky="w")

        self._prod_var = tk.BooleanVar(value=False)
        self.prod = checkbox(left, "This is a production environment", variable=self._prod_var,
                             command=self._on_prod_toggled)
        self.prod.grid(row=9, column=0, sticky="w", pady=(t.SM, 0))
        hint_label(left, "Detected from the URL – please confirm. Production requires an extra "
                         "confirmation before deployment.", wraplength=320).grid(row=10, column=0, sticky="w")

        field_label(left, "Username").grid(row=11, column=0, sticky="w", pady=(t.SM, 0))
        self.username = entry(left, placeholder_text="name@company.com")
        self.username.grid(row=12, column=0, sticky="ew")

        field_label(left, "Password").grid(row=13, column=0, sticky="w", pady=(t.SM, 0))
        self.password = PasswordField(left)
        self.password.grid(row=14, column=0, sticky="ew")
        self.password_hint = hint_label(left, "", wraplength=320)
        self.password_hint.grid(row=15, column=0, sticky="w")

        # Test connection: read-only calls (REST versions, applications)
        tc = ctk.CTkFrame(left, fg_color=t.SURFACE_ALT, corner_radius=t.RADIUS_CONTROL)
        tc.grid(row=16, column=0, sticky="ew", pady=(t.MD, 0))
        tc.grid_columnconfigure(1, weight=1)
        self.test_btn = secondary_button(tc, "Test Connection", self._test_connection, width=140)
        self.test_btn.grid(row=0, column=0, sticky="nw", padx=t.SM, pady=t.SM)
        Tooltip(self.test_btn, "Signs in to Oracle with the URL, user name and password above and lists the "
                               "applications. Nothing is changed in Oracle.")
        self.test_result = hint_label(tc, "Checks the URL, user name and password. Read-only.", wraplength=190)
        self.test_result.grid(row=0, column=1, sticky="w", padx=(0, t.SM), pady=t.SM)
        self.test_note = hint_label(tc, "", tone="warning", wraplength=320)
        self.test_note.grid(row=1, column=0, columnspan=2, sticky="w", padx=t.SM, pady=(0, t.SM))
        self.test_note.grid_remove()
        self.update_pwd_btn = link_button(tc, "Update Password…", self._update_password)
        self.update_pwd_btn.grid(row=2, column=0, columnspan=2, sticky="w", padx=t.XS, pady=(0, t.SM))
        self.update_pwd_btn.grid_remove()

        # ---- right: schedule defaults
        ctk.CTkLabel(right, text="SCHEDULE DEFAULTS", font=t.font("overline"), text_color=t.TEXT_MUTED,
                     anchor="w").grid(row=0, column=0, sticky="w")
        field_label(right, "Default Organizational Unit (optional)").grid(row=1, column=0, sticky="w", pady=(t.SM, 0))
        self.org_unit = entry(right, placeholder_text="e.g. Corporate")
        self.org_unit.grid(row=2, column=0, sticky="ew")
        hint_label(right, "Used for holiday rules. Leave blank to use Oracle’s standard date mapping.",
                   wraplength=320).grid(row=3, column=0, sticky="w")

        field_label(right, "Period mapping").grid(row=4, column=0, sticky="w", pady=(t.SM, 0))
        self.mapping = option_menu(right, list(self._mapping_by_label))
        self.mapping.grid(row=5, column=0, sticky="ew")

        field_label(right, "Day Zero date").grid(row=6, column=0, sticky="w", pady=(t.SM, 0))
        self.strategy = option_menu(right, list(self._strategy_by_label), command=lambda _v: self._on_strategy())
        self.strategy.grid(row=7, column=0, sticky="ew")
        self.fixed_row = ctk.CTkFrame(right, fg_color="transparent")
        field_label(self.fixed_row, "Day of month").pack(side="left", padx=(0, t.SM))
        self.fixed_day = option_menu(self.fixed_row, FIXED_DAYS, width=80)
        self.fixed_day.pack(side="left")
        hint_label(self.fixed_row, "Short months use their last day.").pack(side="left", padx=(t.SM, 0))
        self.fixed_row.grid(row=8, column=0, sticky="w", pady=(t.SM, 0))

        # ---- right: Oracle application (Year/Period verification, job timeout)
        ctk.CTkLabel(right, text="ORACLE APPLICATION", font=t.font("overline"), text_color=t.TEXT_MUTED,
                     anchor="w").grid(row=9, column=0, sticky="w", pady=(t.LG, 0))
        field_label(right, "Application name (optional)").grid(row=10, column=0, sticky="w", pady=(t.SM, 0))
        self.application = entry(right, placeholder_text="e.g. FCCS1")
        self.application.grid(row=11, column=0, sticky="ew")
        Tooltip(self.application, "Leave blank to use the only application available")
        dims = ctk.CTkFrame(right, fg_color="transparent")
        dims.grid(row=12, column=0, sticky="ew", pady=(t.SM, 0))
        dims.grid_columnconfigure((0, 1), weight=1, uniform="dim")
        field_label(dims, "Year dimension").grid(row=0, column=0, sticky="w")
        field_label(dims, "Period dimension").grid(row=0, column=1, sticky="w", padx=(t.SM, 0))
        self.year_dim = entry(dims, width=120)
        self.year_dim.grid(row=1, column=0, sticky="ew")
        self.period_dim = entry(dims, width=120)
        self.period_dim.grid(row=1, column=1, sticky="ew", padx=(t.SM, 0))
        self._verify_var = tk.BooleanVar(value=True)
        checkbox(right, "Verify Year/Period in Oracle before deploying", variable=self._verify_var,
                 command=self._on_verify).grid(row=13, column=0, sticky="w", pady=(t.SM, 0))
        self.verify_warning = hint_label(right, "Not recommended: months whose Year or Period member does not exist "
                                                "are only detected when the Oracle deployment fails.",
                                         tone="warning", wraplength=320)
        self.verify_warning.grid(row=14, column=0, sticky="w")
        timeout = ctk.CTkFrame(right, fg_color="transparent")
        timeout.grid(row=15, column=0, sticky="w", pady=(t.SM, 0))
        field_label(timeout, "Job timeout (minutes)").pack(side="left", padx=(0, t.SM))
        self.job_timeout = entry(timeout, width=70)
        self.job_timeout.pack(side="left")
        Tooltip(self.job_timeout, "How long to wait for one Oracle deployment job. After this the result is "
                                  "recorded as unknown and must be checked in Oracle.")

        self.error = hint_label(self, "", tone="danger", wraplength=740)
        self.error.grid(row=2, column=0, sticky="w", padx=t.XL)

        bar = self.button_bar(3)
        self.tested_note = hint_label(bar, NOT_TESTED_NOTE, wraplength=420)
        self.tested_note.grid(row=0, column=0, sticky="w", padx=t.LG)
        secondary_button(bar, "Cancel", self.cancel, width=100).grid(row=0, column=1, pady=t.MD)
        self.save_btn = primary_button(bar, "Save", self._save, width=110)
        self.save_btn.grid(row=0, column=2, padx=(t.SM, t.LG), pady=t.MD)
        self.bind("<Return>", lambda _e: self._save())
        for widget in (self.url, self.username, self.password.entry):
            widget.bind("<KeyRelease>", self._connection_edited, add="+")

        self._load()

    def _connection_edited(self, event=None):
        """A successful test no longer applies once the URL, user name or password is edited."""
        if event is not None and event.keysym in ("Tab", "Return", "Escape", "Shift_L", "Shift_R"):
            return
        if self._tested_ok:
            self._tested_ok = False
            self.tested_note.configure(text=NOT_TESTED_NOTE, text_color=t.TEXT_MUTED)
            self._test_result("Connection details changed – test again.", None)

    # ---- state
    def _load(self):
        env = self.env
        default_type = self._types[0]["display_name"]
        if env:
            self.name.insert(0, env.display_name)
            self.service.set(service_type_info(env.service_type)["display_name"])
            self.url.insert(0, env.base_url)
            self._prod_var.set(env.is_production)
            self.username.insert(0, env.username)
            if env.default_org_unit:
                self.org_unit.insert(0, env.default_org_unit)
            mapping = self.mappings.get(env.period_mapping)
            if mapping:
                self.mapping.set(mapping.display_name)
            self.strategy.set(DAY_ZERO_STRATEGIES.get(env.day_zero_strategy, DAY_ZERO_STRATEGIES["last_calendar_day"]))
            self.fixed_day.set(str(env.day_zero_fixed_day))
            if env.application_name:
                self.application.insert(0, env.application_name)
            self.year_dim.insert(0, env.year_dimension)
            self.period_dim.insert(0, env.period_dimension)
            self._verify_var.set(env.verify_members)
            self.job_timeout.insert(0, str(env.job_timeout_minutes))
            saved = credentials_saved(self.cred, env)
            self.password_hint.configure(
                text="Leave blank to keep the saved password." if saved else
                "No password is saved yet for this environment. Enter it to enable deployment.")
            self._validate_url()
        else:
            self.service.set(default_type)
            default_mapping = self.mappings.get("standard_monthly") or next(iter(self.mappings.values()), None)
            if default_mapping:
                self.mapping.set(default_mapping.display_name)
            self.strategy.set(DAY_ZERO_STRATEGIES["last_calendar_day"])
            self.fixed_day.set("5")
            defaults = Environment(id="", display_name="", service_type="", base_url="", username="")
            self.year_dim.insert(0, defaults.year_dimension)
            self.period_dim.insert(0, defaults.period_dimension)
            self._verify_var.set(defaults.verify_members)
            self.job_timeout.insert(0, str(defaults.job_timeout_minutes))
            self.password_hint.configure(text="Entered once and stored in Windows Credential Manager.")
        self._on_service()
        self._on_strategy()
        self._on_verify()

    def initial_focus(self):
        self.name.focus_set()

    def _on_service(self):
        type_id = self._type_by_label.get(self.service.get())
        caps = service_type_info(type_id).get("capabilities", [])
        self.service_note.configure(text="" if TM_DEPLOY in caps else ARCS_NOTE)

    def _on_strategy(self):
        if self._strategy_by_label.get(self.strategy.get()) == "fixed_day":
            self.fixed_row.grid()
        else:
            self.fixed_row.grid_remove()

    def _on_prod_toggled(self):
        self._prod_touched = True

    def _on_verify(self):
        if self._verify_var.get():
            self.verify_warning.grid_remove()
        else:
            self.verify_warning.grid()

    def close(self, result=None):
        if self._test_task:
            self._test_task.cancel()  # the result would arrive after the dialog is gone
            self._test_task = None
        super().close(result)

    # ---- test connection (read-only Oracle calls, worker thread)
    def _test_result(self, text: str, tone: str | None = None):
        self.test_result.configure(text=text, text_color=t.STATUS_TONES[tone][0] if tone else t.TEXT_MUTED)

    def _test_connection(self):
        if self._test_task:
            return
        self.test_note.grid_remove()
        self.update_pwd_btn.grid_remove()
        base_url = self._validate_url()
        if base_url is None:
            self._test_result("Enter a valid environment URL first.", "danger")
            return
        username = self.username.get().strip()
        if not username:
            self._test_result("Enter the user name first.", "danger")
            return
        used_stored = False
        password = self.password.get()
        if not password and self.env is not None and username == self.env.username:
            try:
                password = self.cred.get(self.env.id, self.env.username) or ""
            except CredentialStoreError as exc:
                self._test_result(str(exc), "danger")
                return
            used_stored = bool(password)
        if not password:
            self._test_result("Enter the password to test the connection.", "danger")
            return
        client = self.client_factory(base_url, username, password)
        del password
        self.test_btn.configure(state="disabled", text="Testing…")
        self._test_result(f"Connecting to {base_url.split('://', 1)[-1]}…", "info")
        log.info("Test connection started for %s (user %s)", base_url, username)
        self._test_task = BackgroundTask(self, client.test_connection,
                                         lambda report: self._test_done(report),
                                         lambda exc: self._test_failed(exc, used_stored)).start()

    def _test_finished(self):
        self._test_task = None
        self.test_btn.configure(state="normal", text="Test Connection")

    def _test_done(self, report: ConnectionReport):
        self._test_finished()
        self._tested_ok = True
        self._test_result("✓ " + connection_text(report), "success")
        if report.applications_note:
            self.test_note.configure(text=report.applications_note)
            self.test_note.grid()
        apps = report.applications or []
        if len(apps) == 1 and not self.application.get().strip():
            self.application.delete(0, "end")
            self.application.insert(0, apps[0])
        self.tested_note.configure(text="✓ Connection tested successfully.", text_color=t.SUCCESS_FG)
        log.info("Test connection succeeded (%d application(s))", len(apps))

    def _test_failed(self, exc: BaseException, used_stored: bool):
        self._test_finished()
        message = str(exc) if isinstance(exc, OracleError) else f"The connection test failed: {exc}"
        if isinstance(exc, AuthenticationError):
            if used_stored and self.env is not None:
                message += " The saved password may have changed."
                self.update_pwd_btn.grid()
            else:
                message += " Check the user name and password above."
        self._test_result("✕ " + message, "danger")
        log.warning("Test connection failed: %s", type(exc).__name__)

    def _update_password(self):
        if self.env is not None and PasswordDialog(self, self.cred, self.env).show():
            self.update_pwd_btn.grid_remove()
            self._test_result("Password updated. Press Test Connection again.", "info")

    def _validate_url(self) -> str | None:
        raw = self.url.get()
        if not raw.strip():
            self.url_hint.configure(text="Paste the address of your Oracle EPM login page.", text_color=t.TEXT_MUTED)
            return None
        try:
            normalized = normalize_base_url(raw)
        except ValueError as exc:
            self.url_hint.configure(text=str(exc), text_color=t.DANGER_FG)
            return None
        host = normalized.split("://", 1)[-1]
        self.url_hint.configure(text=f"✓ Host: {host}", text_color=t.SUCCESS_FG)
        if raw.strip() != normalized:
            self.url.delete(0, "end")
            self.url.insert(0, normalized)
        if not self._prod_touched:
            self._prod_var.set(guess_is_production(normalized))
        return normalized

    # ---- save
    def _save(self):
        self.error.configure(text="")
        base_url = self._validate_url()
        if base_url is None:
            if not self.url.get().strip():
                self.url_hint.configure(text="Enter the Oracle environment URL.", text_color=t.DANGER_FG)
            self.error.configure(text="Check the environment URL.")
            return
        name = self.name.get().strip()
        username = self.username.get().strip()
        password = self.password.get()
        editing = self.env is not None
        old_username = self.env.username if editing else None
        username_changed = editing and username != old_username

        if not password:
            if not editing:
                self.error.configure(text="Enter the password.")
                return
            if username_changed:
                self.error.configure(text="The user name changed. Enter the password for the new user name.")
                return

        year_dim, period_dim = self.year_dim.get().strip(), self.period_dim.get().strip()
        if not year_dim or not period_dim:
            self.error.configure(text="Enter the Year and Period dimension names (defaults: Years, Period).")
            return
        try:
            job_timeout = int(self.job_timeout.get().strip())
            if not 1 <= job_timeout <= MAX_JOB_TIMEOUT_MINUTES:
                raise ValueError
        except ValueError:
            self.error.configure(text=f"Job timeout must be a whole number of minutes between 1 and "
                                      f"{MAX_JOB_TIMEOUT_MINUTES}.")
            return

        env = Environment(
            id=self.env.id if editing else make_environment_id(name, self.store.ids()),
            display_name=name,
            service_type=self._type_by_label[self.service.get()],
            base_url=base_url,
            username=username,
            auth_method=self.env.auth_method if editing else "basic",
            is_production=bool(self._prod_var.get()),
            default_org_unit=self.org_unit.get().strip(),
            period_mapping=self._mapping_by_label.get(self.mapping.get(), "standard_monthly"),
            day_zero_strategy=self._strategy_by_label.get(self.strategy.get(), "last_calendar_day"),
            day_zero_fixed_day=int(self.fixed_day.get() or 5),
            application_name=self.application.get().strip(),
            year_dimension=year_dim,
            period_dimension=period_dim,
            verify_members=bool(self._verify_var.get()),
            job_timeout_minutes=job_timeout,
        )
        try:
            self.store.save(env)
        except ValueError as exc:
            self.error.configure(text=str(exc))
            return
        except OSError as exc:
            self.error.configure(text=f"The environment could not be saved: {exc}")
            return

        if password:
            try:
                replace_credential(self.cred, env.id, old_username, username, password)
            except CredentialStoreError as exc:
                self.password.clear()
                show_message(self, "Password not saved",
                             f"The environment was saved, but the password was not.\n\n{exc}", "error")
                self.close(env)
                return
        self.password.clear()
        log.info("Environment '%s' %s%s", env.id, "updated" if editing else "added",
                 "" if self._tested_ok else " (connection not tested)")
        self.close(env)


# --------------------------------------------------------------------------- Update password
class PasswordDialog(BaseDialog):
    def __init__(self, parent, cred: CredentialStore, env: Environment):
        super().__init__(parent, "Update Password", width=440)
        self.cred, self.env = cred, env
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=t.XL, pady=(t.LG, t.MD))
        body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(body, text="Update Password", font=t.font("title"), text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        hint_label(body, f"{env.display_name} · {env.host}", wraplength=380).grid(row=1, column=0, sticky="w")
        field_label(body, "Username").grid(row=2, column=0, sticky="w", pady=(t.MD, 0))
        ctk.CTkLabel(body, text=env.username, font=t.font("body"), text_color=t.TEXT, anchor="w").grid(
            row=3, column=0, sticky="w")
        field_label(body, "New password").grid(row=4, column=0, sticky="w", pady=(t.SM, 0))
        self.password = PasswordField(body)
        self.password.grid(row=5, column=0, sticky="ew")
        self.error = hint_label(body, "", tone="danger", wraplength=380)
        self.error.grid(row=6, column=0, sticky="w")
        bar = self.button_bar(1)
        secondary_button(bar, "Cancel", self.cancel, width=100).grid(row=0, column=1, pady=t.MD)
        primary_button(bar, "Save Password", self._save, width=130).grid(row=0, column=2, padx=(t.SM, t.LG), pady=t.MD)
        self.bind("<Return>", lambda _e: self._save())

    def initial_focus(self):
        self.password.entry.focus_set()

    def _save(self):
        secret = self.password.get()
        if not secret:
            self.error.configure(text="Enter the password.")
            return
        try:
            replace_credential(self.cred, self.env.id, None, self.env.username, secret)
        except CredentialStoreError as exc:
            self.error.configure(text=str(exc))
            return
        finally:
            self.password.clear()
        self.close(True)


# --------------------------------------------------------------------------- Manage environments
class ManageEnvironmentsDialog(BaseDialog):
    def __init__(self, parent, store: EnvironmentStore, cred: CredentialStore,
                 mappings: dict[str, PeriodMapping], selected_id: str | None = None,
                 client_factory: ClientFactory = default_client_factory):
        super().__init__(parent, "Manage Environments", width=720, height=500, resizable=True)
        self.minsize(600, 400)
        self.store, self.cred, self.mappings = store, cred, mappings
        self.client_factory = client_factory
        self.selected_id = selected_id
        self.changed = False
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=t.XL, pady=(t.LG, t.SM))
        ctk.CTkLabel(head, text="Manage Environments", font=t.font("title"), text_color=t.TEXT,
                     anchor="w").pack(anchor="w")
        hint_label(head, "Select an environment, then choose an action.").pack(anchor="w")

        self.listbox = ctk.CTkScrollableFrame(self, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD,
                                              border_width=1, border_color=t.BORDER)
        self.listbox.grid(row=1, column=0, sticky="nsew", padx=t.XL)
        self.listbox.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=t.XL, pady=t.MD)
        self.btn_edit = secondary_button(actions, "Edit…", self._edit, width=90)
        self.btn_pwd = secondary_button(actions, "Update Password…", self._update_password, width=150)
        self.btn_forget = secondary_button(actions, "Forget Password", self._forget, width=130)
        self.btn_remove = ctk.CTkButton(actions, text="Remove Environment", command=self._remove, width=160,
                                        height=t.BUTTON_HEIGHT, corner_radius=t.RADIUS_CONTROL, border_width=1,
                                        fg_color=t.SURFACE, hover_color=t.DANGER_BG, border_color=t.DANGER_FG,
                                        text_color=t.DANGER_FG, text_color_disabled=t.TEXT_DISABLED,
                                        font=t.font("body"))
        for i, b in enumerate((self.btn_edit, self.btn_pwd, self.btn_forget)):
            b.grid(row=0, column=i, padx=(0, t.SM))
        actions.grid_columnconfigure(3, weight=1)
        self.btn_remove.grid(row=0, column=4, sticky="e")

        bar = self.button_bar(3)
        primary_button(bar, "Close", self.cancel, width=100).grid(row=0, column=1, padx=t.LG, pady=t.MD)
        self._rows: dict[str, ctk.CTkFrame] = {}
        self._refresh()

    def cancel(self):
        self.close(self.changed)

    def _refresh(self):
        if not self.winfo_exists():
            return
        for child in self.listbox.winfo_children():
            child.destroy()
        self._rows.clear()
        envs = self.store.list()
        if self.selected_id not in {e.id for e in envs}:
            self.selected_id = envs[0].id if envs else None
        if not envs:
            hint_label(self.listbox, "No environments yet. Close this window and use “+ Add Environment”.").grid(
                row=0, column=0, sticky="w", padx=t.MD, pady=t.MD)
        for r, env in enumerate(envs):
            self._rows[env.id] = self._make_row(r, env)
        self._sync()

    def _make_row(self, r: int, env: Environment) -> ctk.CTkFrame:
        row = ctk.CTkFrame(self.listbox, fg_color="transparent", corner_radius=t.RADIUS_CONTROL, cursor="hand2")
        row.grid(row=r, column=0, sticky="ew", pady=2, padx=2)
        row.grid_columnconfigure(1, weight=1)
        name = ctk.CTkLabel(row, text=env.display_name, font=t.font("body_bold"), text_color=t.TEXT, anchor="w")
        name.grid(row=0, column=0, columnspan=2, sticky="w", padx=t.MD, pady=(t.SM, 0))
        host = ctk.CTkLabel(row, text=f"{service_type_info(env.service_type)['display_name']} · {env.host}",
                            font=t.font("small"), text_color=t.TEXT_MUTED, anchor="w", justify="left",
                            wraplength=380)
        host.grid(row=1, column=0, columnspan=2, sticky="w", padx=t.MD, pady=(0, t.SM))
        badges = ctk.CTkFrame(row, fg_color="transparent")
        badges.grid(row=0, column=2, rowspan=2, sticky="e", padx=t.MD)
        saved = credentials_saved(self.cred, env)
        Badge(badges, "Credentials saved" if saved else ("Credential Manager unavailable" if saved is None
                                                          else "No credentials"),
              "success" if saved else "warning").pack(side="left", padx=(0, t.XS))
        Badge(badges, "PRODUCTION" if env.is_production else "TEST",
              "danger_solid" if env.is_production else "info").pack(side="left")
        for w in (row, name, host, badges):
            w.bind("<Button-1>", lambda _e, i=env.id: self._select(i))
            w.bind("<Double-Button-1>", lambda _e, i=env.id: (self._select(i), self._edit()))
        return row

    def _select(self, env_id: str):
        self.selected_id = env_id
        self._sync()

    def _sync(self):
        for env_id, row in self._rows.items():
            row.configure(fg_color=t.ROW_SELECTED if env_id == self.selected_id else "transparent")
        env = self.store.get(self.selected_id) if self.selected_id else None
        for b in (self.btn_edit, self.btn_pwd, self.btn_remove):
            b.configure(state="normal" if env else "disabled")
        has = credentials_saved(self.cred, env) if env else False
        self.btn_forget.configure(state="normal" if has else "disabled")

    def _current(self) -> Environment | None:
        return self.store.get(self.selected_id) if self.selected_id else None

    def _edit(self):
        env = self._current()
        if env and EnvironmentDialog(self, self.store, self.cred, self.mappings, env,
                                     client_factory=self.client_factory).show():
            self.changed = True
            self._refresh()

    def _update_password(self):
        env = self._current()
        if env and PasswordDialog(self, self.cred, env).show():
            self.changed = True
            self._refresh()
            show_message(self, "Password saved", f"The password for “{env.display_name}” was updated.",
                         "success")

    def _forget(self):
        env = self._current()
        if not env:
            return
        if not ask_confirm(self, "Forget password?",
                           f"Remove the saved password for “{env.display_name}” from Windows Credential "
                           "Manager? The environment stays in the list; you can enter the password again later.",
                           confirm_text="Forget Password", danger=True):
            return
        try:
            self.cred.delete(env.id, env.username)
        except CredentialStoreError as exc:
            show_message(self, "Password not removed", str(exc), "error")
            return
        self.changed = True
        self._refresh()

    def _remove(self):
        env = self._current()
        if not env:
            return
        if not ask_confirm(self, "Remove environment?",
                           f"Remove “{env.display_name}” ({env.host}) and its saved password from this "
                           "computer? Nothing is changed in Oracle.",
                           confirm_text="Remove", danger=True):
            return
        try:
            self.cred.delete(env.id, env.username)
        except CredentialStoreError as exc:
            show_message(self, "Password not removed", f"{exc}\n\nThe environment was not removed.", "error")
            return
        self.store.delete(env.id)
        log.info("Environment '%s' removed", env.id)
        self.selected_id = None
        self.changed = True
        self._refresh()


# --------------------------------------------------------------------------- Sidebar
class EnvironmentSidebar(ctk.CTkFrame):
    """Environment picker + status. Calls on_change(env | None) when the selection or its data changes."""

    def __init__(self, master, store: EnvironmentStore, cred: CredentialStore,
                 mappings: dict[str, PeriodMapping], on_change: Callable[[Environment | None], None],
                 client_factory: ClientFactory = default_client_factory):
        super().__init__(master, fg_color=t.SURFACE_ALT, corner_radius=0, width=t.SIDEBAR_WIDTH)
        self.store, self.cred, self.mappings, self.on_change = store, cred, mappings, on_change
        self.client_factory = client_factory
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.current: Environment | None = None
        self.has_credentials: bool = False
        self._id_by_name: dict[str, str] = {}
        pad = t.LG

        ctk.CTkLabel(self, text="ENVIRONMENT", font=t.font("overline"), text_color=t.TEXT_MUTED,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=pad, pady=(t.LG, t.XS))
        self.dropdown = option_menu(self, [], width=t.SIDEBAR_WIDTH - 2 * pad, command=self._on_pick)
        self.dropdown.grid(row=1, column=0, sticky="ew", padx=pad)

        self.status = ctk.CTkFrame(self, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD, border_width=1,
                                   border_color=t.BORDER)
        self.status.grid(row=2, column=0, sticky="ew", padx=pad, pady=(t.MD, 0))
        self.status.grid_columnconfigure(0, weight=1)
        badge_row = ctk.CTkFrame(self.status, fg_color="transparent")
        badge_row.grid(row=0, column=0, sticky="w", padx=t.MD, pady=(t.MD, 0))
        self.env_badge = Badge(badge_row, "TEST", "info")
        self.env_badge.pack(side="left")
        self.service_label = ctk.CTkLabel(self.status, text="", font=t.font("caption_bold"), text_color=t.TEXT_MUTED,
                                          anchor="w", height=18)
        self.service_label.grid(row=5, column=0, sticky="w", padx=t.MD, pady=(t.XS, 0))
        self.host_label = ctk.CTkLabel(self.status, text="", font=t.font("small"), text_color=t.TEXT, anchor="w",
                                       justify="left", wraplength=t.SIDEBAR_WIDTH - 2 * pad - 2 * t.MD)
        self.host_label.grid(row=6, column=0, sticky="w", padx=t.MD)
        self.user_label = hint_label(self.status, "", wraplength=t.SIDEBAR_WIDTH - 2 * pad - 2 * t.MD)
        self.user_label.grid(row=7, column=0, sticky="w", padx=t.MD)
        self.cred_label = ctk.CTkLabel(self.status, text="", font=t.font("small_bold"), anchor="w")
        self.cred_label.grid(row=8, column=0, sticky="w", padx=t.MD, pady=(t.XS, t.MD))
        self.note = hint_label(self.status, "", tone="warning", wraplength=t.SIDEBAR_WIDTH - 2 * pad - 2 * t.MD)
        self.note.grid(row=9, column=0, sticky="w", padx=t.MD, pady=(0, t.MD))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=pad, pady=(t.MD, 0))
        buttons.grid_columnconfigure((0, 1), weight=1)
        self.add_btn = primary_button(buttons, "+ Add Environment", self.add_environment, width=10)
        self.add_btn.grid(row=0, column=0, sticky="ew", padx=(0, t.XS))
        self.manage_btn = secondary_button(buttons, "Manage…", self.manage, width=10)
        self.manage_btn.grid(row=0, column=1, sticky="ew", padx=(t.XS, 0))

        self.grid_rowconfigure(4, weight=1)
        safety = ctk.CTkFrame(self, fg_color=t.INFO_BG, corner_radius=t.RADIUS_CONTROL)
        safety.grid(row=5, column=0, sticky="ew", padx=pad, pady=t.LG)
        ctk.CTkLabel(safety, text="When is Oracle contacted?", font=t.font("small_bold"), text_color=t.INFO_FG,
                     anchor="w").pack(anchor="w", padx=t.MD, pady=(t.SM, 0))
        ctk.CTkLabel(safety, text="Only when you click Test Connection, Check in Oracle or Confirm and Deploy. "
                                  "Schedules are created only after you confirm.",
                     font=t.font("caption"), text_color=t.INFO_FG, anchor="w", justify="left",
                     wraplength=t.SIDEBAR_WIDTH - 2 * pad - 2 * t.MD).pack(anchor="w", padx=t.MD, pady=(0, t.SM))

        self.reload(select_id=store.last_used_id)

    # ---- public
    def reload(self, select_id: str | None = None) -> None:
        envs = self.store.list()
        self._id_by_name = {e.display_name: e.id for e in envs}
        if envs:
            self.dropdown.configure(values=list(self._id_by_name), state="normal")
            self.manage_btn.configure(state="normal")
        else:
            self.dropdown.configure(values=["No environments"], state="disabled")
            self.dropdown.set("No environments")
            self.manage_btn.configure(state="disabled")
        target = self.store.get(select_id) if select_id else None
        if target is None and self.current is not None:
            target = self.store.get(self.current.id)
        if target is None and envs:
            target = envs[0]
        self._select(target)

    def add_environment(self, first_run: bool = False) -> None:
        env = EnvironmentDialog(self.winfo_toplevel(), self.store, self.cred, self.mappings,
                                first_run=first_run, client_factory=self.client_factory).show()
        if env:
            self.reload(select_id=env.id)

    def manage(self) -> None:
        current = self.current.id if self.current else None
        ManageEnvironmentsDialog(self.winfo_toplevel(), self.store, self.cred, self.mappings, current,
                                 client_factory=self.client_factory).show()
        self.reload(select_id=current)  # data may have changed (edit / password / remove)

    def update_password(self) -> bool:
        """Update Password dialog for the current environment. True when a new password was saved."""
        env = self.current
        if env is None or not PasswordDialog(self.winfo_toplevel(), self.cred, env).show():
            return False
        self.reload(select_id=env.id)
        return True

    def set_locked(self, locked: bool) -> None:
        """No environment switching or editing while Oracle work is running."""
        state = "disabled" if locked else "normal"
        has_envs = bool(self.store.list())
        self.dropdown.configure(state=state if has_envs else "disabled")
        self.add_btn.configure(state=state)
        self.manage_btn.configure(state=state if has_envs else "disabled")

    # ---- internal
    def _on_pick(self, name: str) -> None:
        env_id = self._id_by_name.get(name)
        self._select(self.store.get(env_id) if env_id else None)

    def _select(self, env: Environment | None) -> None:
        self.current = env
        if env is None:
            self.has_credentials = False
            self.env_badge.pack_forget()
            self.service_label.configure(text="")
            self.host_label.configure(text="No environment selected.")
            self.user_label.configure(text="Add the Oracle environment you deploy schedules to.")
            self.cred_label.configure(text="")
            self.note.configure(text="")
            self.note.grid_remove()
        else:
            self.dropdown.set(env.display_name)
            if self.store.last_used_id != env.id:
                try:
                    self.store.set_last_used(env.id)
                except OSError:
                    log.warning("Could not remember the last used environment")
            info = service_type_info(env.service_type)
            self.env_badge.pack(side="left")
            self.env_badge.set("PRODUCTION" if env.is_production else "TEST",
                               "danger_solid" if env.is_production else "info")
            self.service_label.configure(text=info["display_name"])
            self.host_label.configure(text=env.host)
            self.user_label.configure(text=env.username)
            saved = credentials_saved(self.cred, env)
            self.has_credentials = bool(saved)
            if saved:
                self.cred_label.configure(text="● Credentials saved", text_color=t.SUCCESS_FG)
            elif saved is None:
                self.cred_label.configure(text="● Credential Manager unavailable", text_color=t.DANGER_FG)
            else:
                self.cred_label.configure(text="● No credentials – use Manage…", text_color=t.WARNING_FG)
            if TM_DEPLOY in info.get("capabilities", []):
                self.note.configure(text="")
                self.note.grid_remove()
            else:
                self.note.configure(text=ARCS_NOTE)
                self.note.grid()
        self.on_change(env)
