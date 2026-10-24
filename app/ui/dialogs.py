"""Modal dialogs: base dialog, message/confirm boxes, template issues list, deployment confirmation."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass

import customtkinter as ctk

from app.models import Severity, ValidationIssue
from app.ui import theme as t
from app.ui.widgets import Badge, checkbox, hint_label, primary_button, secondary_button, set_primary_enabled


class BaseDialog(ctk.CTkToplevel):
    """Modal, centred over its parent. Escape cancels. `show()` blocks until closed and returns `result`."""

    def __init__(self, parent, title: str, width: int = 520, height: int | None = None, resizable: bool = False):
        super().__init__(parent)
        self.withdraw()
        self.parent = parent
        self.result = None
        self.title(title)
        self.configure(fg_color=t.BG)
        self.resizable(resizable, resizable)
        self.transient(parent.winfo_toplevel())
        self._req_width, self._req_height = width, height
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())
        self.grid_columnconfigure(0, weight=1)

    def _place_and_show(self):
        self.update_idletasks()
        scale = self._get_window_scaling()
        width = self._req_width
        height = self._req_height or round(self.winfo_reqheight() / scale)
        top = self.parent.winfo_toplevel()
        px, py = top.winfo_rootx(), top.winfo_rooty()
        pw, ph = top.winfo_width(), top.winfo_height()
        x = max(0, px + (pw - int(width * scale)) // 2)
        y = max(0, py + (ph - int(height * scale)) // 3)
        # CTkToplevel.geometry() scales width/height (logical px) but takes x/y as screen pixels
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.deiconify()
        self.lift()
        # CustomTkinter re-draws the title bar shortly after creation on Windows: grab once viewable.
        self.after(120, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
            self.initial_focus()
        except tk.TclError:
            self.after(100, self._grab)

    def initial_focus(self):
        """Override to focus a specific widget."""

    def show(self):
        self._place_and_show()
        self.wait_window()
        return self.result

    def close(self, result=None):
        self.result = result
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def cancel(self):
        self.close(None)

    # helpers
    def button_bar(self, row: int) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(self, fg_color=t.SURFACE_ALT, corner_radius=0, height=60)
        bar.grid(row=row, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        return bar


# --------------------------------------------------------------------------- message boxes
_KIND_TONE = {"info": "info", "success": "success", "warning": "warning", "error": "danger"}
_KIND_TITLE_ICON = {"info": "i", "success": "✓", "warning": "!", "error": "✕"}


class MessageDialog(BaseDialog):
    def __init__(self, parent, title: str, message: str, kind: str = "info",
                 confirm_text: str | None = None, cancel_text: str = "Cancel", danger: bool = False):
        super().__init__(parent, title, width=460)
        tone = _KIND_TONE.get(kind, "info")
        fg, bg = t.STATUS_TONES[tone]
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=t.XL, pady=(t.XL, t.LG))
        body.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(body, text=_KIND_TITLE_ICON.get(kind, "i"), width=32, height=32, corner_radius=16,
                     fg_color=bg, text_color=fg, font=t.font("h2")).grid(row=0, column=0, rowspan=2,
                                                                          sticky="n", padx=(0, t.MD))
        ctk.CTkLabel(body, text=title, font=t.font("h2"), text_color=t.TEXT, anchor="w").grid(
            row=0, column=1, sticky="w")
        ctk.CTkLabel(body, text=message, font=t.font("body"), text_color=t.TEXT, anchor="w",
                     justify="left", wraplength=360).grid(row=1, column=1, sticky="w", pady=(t.XS, 0))

        bar = self.button_bar(1)
        if confirm_text:
            self._ok = primary_button(bar, confirm_text, lambda: self.close(True), width=120, danger=danger)
            self._ok.grid(row=0, column=2, padx=(t.SM, t.LG), pady=t.MD)
            self._cancel_btn = secondary_button(bar, cancel_text, self.cancel, width=100)
            self._cancel_btn.grid(row=0, column=1, pady=t.MD)
            self._focus = self._cancel_btn if danger else self._ok
        else:
            self._ok = primary_button(bar, "OK", lambda: self.close(True), width=100)
            self._ok.grid(row=0, column=1, padx=t.LG, pady=t.MD)
            self._focus = self._ok
        self.bind("<Return>", lambda _e: self._focus.invoke())

    def initial_focus(self):
        self._focus.focus_set()


def show_message(parent, title: str, message: str, kind: str = "info") -> None:
    MessageDialog(parent, title, message, kind).show()


def ask_confirm(parent, title: str, message: str, confirm_text: str = "OK", danger: bool = False,
                kind: str = "warning", cancel_text: str = "Cancel") -> bool:
    return bool(MessageDialog(parent, title, message, kind, confirm_text=confirm_text, cancel_text=cancel_text,
                              danger=danger).show())


# --------------------------------------------------------------------------- template issues
_SEVERITY_TONE = {Severity.ERROR: "danger", Severity.WARNING: "warning", Severity.INFO: "info"}
_SEVERITY_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
MAX_ISSUES_SHOWN = 300


class IssuesDialog(BaseDialog):
    """Scrollable list of template validation issues (severity, location, message)."""

    def __init__(self, parent, file_name: str, issues: list[ValidationIssue], unknown_columns: list[str]):
        super().__init__(parent, "Template validation details", width=760, height=540, resizable=True)
        self.minsize(560, 380)
        self.grid_rowconfigure(1, weight=1)

        errors = sum(1 for i in issues if i.severity == Severity.ERROR)
        warnings = sum(1 for i in issues if i.severity == Severity.WARNING)
        infos = len(issues) - errors - warnings

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=t.XL, pady=(t.LG, t.SM))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=file_name, font=t.font("h2"), text_color=t.TEXT, anchor="w").grid(
            row=0, column=0, sticky="w")
        chips = ctk.CTkFrame(head, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="w", pady=(t.XS, 0))
        Badge(chips, f"{errors} error(s)", "danger" if errors else "neutral").pack(side="left", padx=(0, t.XS))
        Badge(chips, f"{warnings} warning(s)", "warning" if warnings else "neutral").pack(side="left", padx=(0, t.XS))
        Badge(chips, f"{infos} info", "info").pack(side="left")

        lst = ctk.CTkScrollableFrame(self, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD,
                                     border_width=1, border_color=t.BORDER)
        lst.grid(row=1, column=0, sticky="nsew", padx=t.XL, pady=(0, t.MD))
        lst.grid_columnconfigure(2, weight=1)

        ordered = sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))
        if not ordered:
            hint_label(lst, "No issues were found.").grid(row=0, column=0, columnspan=3, sticky="w", pady=t.SM)
        for r, issue in enumerate(ordered[:MAX_ISSUES_SHOWN]):
            Badge(lst, issue.severity.value, _SEVERITY_TONE.get(issue.severity, "neutral"), width=70).grid(
                row=r, column=0, sticky="nw", padx=(t.SM, t.MD), pady=(t.SM, 0))
            ctk.CTkLabel(lst, text=issue.location or "Workbook", font=t.font("small"), text_color=t.TEXT_MUTED,
                         anchor="w", justify="left", wraplength=180, width=180).grid(
                row=r, column=1, sticky="nw", padx=(0, t.MD), pady=(t.SM, 0))
            ctk.CTkLabel(lst, text=issue.message, font=t.font("body"), text_color=t.TEXT, anchor="w",
                         justify="left", wraplength=470).grid(row=r, column=2, sticky="nw", pady=(t.SM, 0))
        if len(ordered) > MAX_ISSUES_SHOWN:
            hint_label(lst, f"{len(ordered) - MAX_ISSUES_SHOWN} more issue(s) not shown. Use Export Preview "
                            "to get the complete list.").grid(row=MAX_ISSUES_SHOWN, column=0, columnspan=3,
                                                              sticky="w", pady=t.SM, padx=t.SM)

        if unknown_columns:
            hint_label(self, "Unrecognised columns (kept in the report, not sent to Oracle): "
                             + ", ".join(unknown_columns), wraplength=700).grid(
                row=2, column=0, sticky="w", padx=t.XL, pady=(0, t.SM))

        bar = self.button_bar(3)
        self._close = primary_button(bar, "Close", self.cancel, width=100)
        self._close.grid(row=0, column=1, padx=t.LG, pady=t.MD)

    def initial_focus(self):
        self._close.focus_set()


# --------------------------------------------------------------------------- deployment confirmation
@dataclass
class DeploymentSummary:
    environment_name: str
    host: str
    is_production: bool
    service_name: str
    source_template: str
    template_file: str
    name_pattern: str
    example_name: str
    period_label: str
    requested: int
    new: int
    skip: int
    blocked: int
    members_verified: bool      # Year/Period members verified with Get Member (False = switched off)
    org_unit: str
    application: str = ""
    members_note: str = ""


class ConfirmDeploymentDialog(BaseDialog):
    """'Confirm Schedule Deployment' exactly as specified. Returns True only on 'Confirm and Deploy'."""

    def __init__(self, parent, s: DeploymentSummary):
        super().__init__(parent, "Confirm Schedule Deployment", width=660)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=t.XL, pady=(t.XL, t.MD))
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(body, text="Confirm Schedule Deployment", font=t.font("title"), text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(body, text=f"You are about to deploy monthly Task Manager schedules to the selected "
                                f"Oracle {s.service_name} environment.",
                     font=t.font("body"), text_color=t.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=550).grid(row=1, column=0, sticky="w", pady=(t.XS, t.MD))

        # Environment block: name + badge + full host, visually prominent
        env = ctk.CTkFrame(body, fg_color=t.DANGER_BG if s.is_production else t.SURFACE,
                           corner_radius=t.RADIUS_CARD, border_width=1,
                           border_color=t.DANGER_FG if s.is_production else t.BORDER)
        env.grid(row=2, column=0, sticky="ew")
        env.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(env, text="Environment", font=t.font("small_bold"), text_color=t.TEXT_MUTED,
                     anchor="w", width=150).grid(row=0, column=0, sticky="w", padx=(t.LG, t.SM), pady=(t.MD, 0))
        name_row = ctk.CTkFrame(env, fg_color="transparent")
        name_row.grid(row=0, column=1, sticky="w", pady=(t.MD, 0))
        ctk.CTkLabel(name_row, text=s.environment_name, font=t.font("body_bold"), text_color=t.TEXT).pack(side="left")
        Badge(name_row, "PRODUCTION" if s.is_production else "TEST",
              "danger_solid" if s.is_production else "info").pack(side="left", padx=(t.SM, 0))
        ctk.CTkLabel(env, text="Host", font=t.font("small_bold"), text_color=t.TEXT_MUTED, anchor="w",
                     width=150).grid(row=1, column=0, sticky="w", padx=(t.LG, t.SM), pady=(0, t.MD))
        ctk.CTkLabel(env, text=s.host, font=t.font("mono"), text_color=t.TEXT, anchor="w", justify="left",
                     wraplength=400).grid(row=1, column=1, sticky="w", pady=(0, t.MD), padx=(0, t.LG))

        # Details grid
        details = ctk.CTkFrame(body, fg_color="transparent")
        details.grid(row=3, column=0, sticky="ew", pady=(t.MD, 0))
        details.grid_columnconfigure(1, weight=1)
        rows = [
            ("Source Template", s.source_template),
            ("Template file", s.template_file),
            ("Schedule name pattern", s.name_pattern),
            ("Example name", s.example_name),
            ("Period", s.period_label),
        ]
        if s.org_unit:
            rows.append(("Organizational Unit", s.org_unit))
        if s.application:
            rows.append(("Oracle application", s.application))
        if s.members_note:
            rows.append(("Year/Period members", s.members_note))
        rows += [
            ("Total Schedules Requested", str(s.requested)),
            ("New Schedules to Create", str(s.new)),
            ("Existing Schedules to Skip", str(s.skip)),
        ]
        if s.blocked:
            rows.append(("Blocked (not deployed)", str(s.blocked)))
        for r, (label, value) in enumerate(rows):
            ctk.CTkLabel(details, text=label, font=t.font("small_bold"), text_color=t.TEXT_MUTED, anchor="w",
                         width=150).grid(row=r, column=0, sticky="nw", padx=(t.LG, t.SM), pady=2)
            ctk.CTkLabel(details, text=value, font=t.font("body"), text_color=t.TEXT, anchor="w",
                         justify="left", wraplength=380).grid(row=r, column=1, sticky="w", pady=2)

        # Warnings
        warn = ctk.CTkFrame(body, fg_color=t.WARNING_BG, corner_radius=t.RADIUS_CONTROL)
        warn.grid(row=4, column=0, sticky="ew", pady=(t.LG, 0))
        warn.grid_columnconfigure(0, weight=1)
        texts = ["Deployment creates Oracle Task Manager schedules and may initiate changes in the "
                 "selected environment."]
        texts.append("Oracle offers no API that lists existing schedules. Duplicates are detected from this "
                     "computer’s deployment history and from Oracle’s answer during deployment.")
        if not s.members_verified:
            texts.append("Year/Period members were not verified in Oracle (switched off in the environment "
                         "settings). Deployment may fail for months whose members do not exist.")
        for i, text in enumerate(texts):
            ctk.CTkLabel(warn, text="⚠  " + text if i == 0 else text, font=t.font("small"),
                         text_color=t.WARNING_FG, anchor="w", justify="left", wraplength=520).grid(
                row=i, column=0, sticky="w", padx=t.MD, pady=(t.SM if i == 0 else 0, t.SM))

        self._ack = tk.BooleanVar(value=False)
        if s.is_production:
            checkbox(body, "I understand this changes the PRODUCTION environment", variable=self._ack,
                     command=self._sync, text_color=t.DANGER_FG, font=t.font("body_bold")).grid(
                row=5, column=0, sticky="w", pady=(t.MD, 0))

        bar = self.button_bar(1)
        self._cancel_btn = secondary_button(bar, "Cancel", self.cancel, width=100)
        self._cancel_btn.grid(row=0, column=1, pady=t.MD)
        self._confirm = primary_button(bar, "Confirm and Deploy", lambda: self.close(True), width=170,
                                       danger=s.is_production)
        self._confirm.grid(row=0, column=2, padx=(t.SM, t.LG), pady=t.MD)
        self._production = s.is_production
        self._sync()

    def _sync(self):
        enabled = (not self._production) or self._ack.get()
        set_primary_enabled(self._confirm, enabled, danger=self._production)

    def initial_focus(self):
        self._cancel_btn.focus_set()  # safe default: Enter / Space never deploys by accident
