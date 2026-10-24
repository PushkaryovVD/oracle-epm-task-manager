"""Step 1: choose and validate the Excel Task Manager template (validation runs in a worker thread)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from app.services.template_importer import TemplateInspection, inspect_workbook
from app.ui import theme as t
from app.ui.dialogs import IssuesDialog
from app.ui.widgets import (
    Badge, BackgroundTask, Card, Tooltip, entry, field_label, hint_label, link_button, primary_button,
    secondary_button, set_primary_enabled,
)

log = logging.getLogger(__name__)


class TemplateView(Card):
    """Exposes `inspection`, `validating`, `template_name` and `file_path`; calls on_change() on any change."""

    def __init__(self, master, on_change: Callable[[], None]):
        super().__init__(master, "Excel template", step="1")
        self.on_change = on_change
        self.file_path: Path | None = None
        self.inspection: TemplateInspection | None = None
        self.validating = False
        self._task: BackgroundTask | None = None
        self._auto_name = ""  # last name suggested by the inspection

        b = self.body
        b.grid_columnconfigure(1, weight=1)

        # Row 0: file picker + validation status
        self.browse_btn = secondary_button(b, "Browse…", self.browse, width=100)
        self.browse_btn.grid(row=0, column=0, sticky="w")
        self.file_label = ctk.CTkLabel(b, text="No file selected", font=t.font("body"),
                                       text_color=t.TEXT_MUTED, anchor="w")
        self.file_label.grid(row=0, column=1, sticky="ew", padx=(t.MD, t.MD))
        self._file_tip = Tooltip(self.file_label, lambda: str(self.file_path) if self.file_path else "")

        status = ctk.CTkFrame(b, fg_color="transparent")
        status.grid(row=0, column=2, sticky="e")
        self.status_badge = Badge(status, "Not validated", "neutral")
        self.status_badge.pack(side="left")
        self.details_btn = link_button(status, "Details", self.show_details, state="disabled")
        self.details_btn.pack(side="left", padx=(t.XS, t.SM))
        self.validate_btn = primary_button(status, "Validate", self.validate, width=96)
        self.validate_btn.pack(side="left")
        set_primary_enabled(self.validate_btn, False)

        # Row 1: template name + facts
        name_row = ctk.CTkFrame(b, fg_color="transparent")
        name_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(t.MD, 0))
        name_row.grid_columnconfigure(1, weight=1)
        field_label(name_row, "Oracle template name").grid(row=0, column=0, sticky="w", padx=(0, t.MD))
        self.name_entry = entry(name_row, width=320, placeholder_text="Name of the template in Task Manager")
        self.name_entry.grid(row=0, column=1, sticky="ew")
        self.name_entry.bind("<KeyRelease>", lambda _e: self.on_change())
        Tooltip(self.name_entry, "Must match the template name in Oracle Task Manager exactly. "
                                 "Prefilled from the workbook; you can change it.")
        self.facts = hint_label(name_row, "")
        self.facts.grid(row=0, column=2, sticky="e", padx=(t.MD, 0))

    # ---- public state
    @property
    def template_name(self) -> str:
        return self.name_entry.get().strip()

    @property
    def is_valid(self) -> bool:
        return bool(self.inspection and self.inspection.is_valid and not self.validating)

    def set_locked(self, locked: bool) -> None:
        """Configuration cannot change while Oracle work (check / deployment) is running."""
        self._locked = locked
        state = "disabled" if locked else "normal"
        self.browse_btn.configure(state="disabled" if locked or self.validating else "normal")
        self.name_entry.configure(state=state)
        set_primary_enabled(self.validate_btn, not locked and bool(self.file_path) and not self.validating)

    # ---- actions
    def browse(self):
        if getattr(self, "_locked", False):
            return
        path = ctk.filedialog.askopenfilename(
            parent=self.winfo_toplevel(), title="Select the Task Manager template",
            filetypes=[("Task Manager export or Excel", "*.csv *.txt *.xlsx *.xlsm"),
                       ("Task Manager export (CSV)", "*.csv *.txt"),
                       ("Excel workbook", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if not path:
            return
        self.file_path = Path(path)
        self.file_label.configure(text=self.file_path.name, text_color=t.TEXT)
        set_primary_enabled(self.validate_btn, True)
        self.validate()  # validate straight away: one less click for the user

    def validate(self):
        if not self.file_path:
            return
        if self._task:
            self._task.cancel()  # a newer request supersedes the running one
        self.validating = True
        self.inspection = None
        self.status_badge.set("Validating…", "info")
        self.details_btn.configure(state="disabled")
        set_primary_enabled(self.validate_btn, False)
        self.browse_btn.configure(state="disabled")
        self.facts.configure(text="Reading the workbook…")
        path = self.file_path
        log.info("Validating template %s", path.name)
        self._task = BackgroundTask(self, lambda: inspect_workbook(path), self._on_done, self._on_error).start()
        self.on_change()

    def _finish(self):
        self.validating = False
        self._task = None
        set_primary_enabled(self.validate_btn, True)
        self.browse_btn.configure(state="normal")

    def _on_done(self, result: TemplateInspection):
        self._finish()
        self.inspection = result
        errors, warnings = len(result.errors), len(result.warnings)
        if result.is_valid:
            text = "Valid" + (f" · {warnings} warning(s)" if warnings else "")
            self.status_badge.set(text, "warning" if warnings else "success")
        else:
            self.status_badge.set(f"{errors} error(s), {warnings} warning(s)", "danger")
        self.details_btn.configure(state="normal")
        facts = [f"{result.task_count} task(s)", result.kind]
        if result.sheet:
            facts.append(f"sheet “{result.sheet}”")
        self.facts.configure(text=" · ".join(facts))
        # Prefill the Oracle name unless the user typed their own
        current = self.name_entry.get().strip()
        if result.template_name and (not current or current == self._auto_name):
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, result.template_name)
        self._auto_name = result.template_name
        log.info("Template validated: %d task(s), %d error(s), %d warning(s)", result.task_count, errors, warnings)
        self.on_change()

    def _on_error(self, exc: BaseException):
        self._finish()
        self.inspection = None
        self.status_badge.set("Validation failed", "danger")
        self.facts.configure(text=f"The workbook could not be read: {exc}")
        self.on_change()

    def show_details(self):
        if self.inspection:
            IssuesDialog(self.winfo_toplevel(), self.inspection.path.name, self.inspection.issues,
                         self.inspection.unknown_columns).show()
