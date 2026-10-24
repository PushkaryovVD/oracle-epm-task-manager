"""Dialogs used around a deployment run: completion summary, retry confirmation, unknown-result resolution
and the read-only deployment history."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Callable

import customtkinter as ctk

from app.models import Environment, PlannedSchedule
from app.services.deployment_ledger import DeploymentLedger, LedgerEntry, LedgerStatus
from app.ui import theme as t
from app.ui.dialogs import BaseDialog
from app.ui.widgets import (
    BackgroundTask, Badge, checkbox, hint_label, primary_button, secondary_button, set_primary_enabled,
)


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def _env_line(master, env_name: str, host: str, production: bool) -> ctk.CTkFrame:
    row = ctk.CTkFrame(master, fg_color="transparent")
    ctk.CTkLabel(row, text=env_name, font=t.font("body_bold"), text_color=t.TEXT).pack(side="left")
    Badge(row, "PRODUCTION" if production else "TEST", "danger_solid" if production else "info").pack(
        side="left", padx=(t.SM, t.SM))
    ctk.CTkLabel(row, text=host, font=t.font("mono"), text_color=t.TEXT_MUTED).pack(side="left")
    return row


# --------------------------------------------------------------------------- completion summary
@dataclass
class CompletionInfo:
    environment_name: str
    host: str
    is_production: bool
    requested: int
    completed: int
    skipped: int
    failed: int
    unknown: int
    not_started: int
    blocked: int
    duration_seconds: float
    stop_reason: str = ""
    stop_message: str = ""
    stop_hint: str = ""                 # what the user should do next for this stop reason
    authentication_stop: bool = False
    log_path: str = ""
    report_path: str = ""


class CompletionSummaryDialog(BaseDialog):
    """Returns "view" (View Results) or None (Close / Escape)."""

    def __init__(self, parent, info: CompletionInfo,
                 on_export: Callable[[Callable[[Path], None]], None],
                 on_update_password: Callable[[], bool] | None = None):
        super().__init__(parent, "Deployment Summary", width=640)
        self.info = info
        self._on_export = on_export
        self._on_update_password = on_update_password

        problems = info.failed + info.unknown + info.not_started
        if info.stop_reason:
            tone, icon, title = "danger", "✕", "Deployment stopped"
        elif problems:
            tone, icon, title = "warning", "!", "Deployment finished with problems"
        else:
            tone, icon, title = "success", "✓", "Deployment finished"
        fg, bg = t.STATUS_TONES[tone]

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=t.XL, pady=(t.XL, t.MD))
        body.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(body, text=icon, width=36, height=36, corner_radius=18, fg_color=bg, text_color=fg,
                     font=t.font("h2")).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, t.MD))
        ctk.CTkLabel(body, text=title, font=t.font("title"), text_color=t.TEXT, anchor="w").grid(
            row=0, column=1, sticky="w")
        _env_line(body, info.environment_name, info.host, info.is_production).grid(row=1, column=1, sticky="w")

        # Stat tiles
        tiles = ctk.CTkFrame(body, fg_color="transparent")
        tiles.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(t.LG, 0))
        stats = [("Requested", info.requested, None), ("Created", info.completed, "success"),
                 ("Skipped", info.skipped, "neutral"), ("Failed", info.failed, "danger"),
                 ("Unknown", info.unknown, "unknown"), ("Not started", info.not_started, "neutral")]
        if info.blocked:
            stats.append(("Blocked", info.blocked, "danger"))
        for col, (label, value, stat_tone) in enumerate(stats):
            tiles.grid_columnconfigure(col, weight=1, uniform="tile")
            active = bool(value) and stat_tone not in (None, "neutral")
            tile = ctk.CTkFrame(tiles, corner_radius=t.RADIUS_CONTROL, border_width=1,
                                fg_color=t.STATUS_TONES[stat_tone][1] if active else t.SURFACE,
                                border_color=t.STATUS_TONES[stat_tone][0] if active else t.BORDER)
            tile.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else t.XS, 0))
            ctk.CTkLabel(tile, text=str(value), font=t.font("title"),
                         text_color=t.STATUS_TONES[stat_tone][0] if active else t.TEXT).pack(pady=(t.SM, 0))
            ctk.CTkLabel(tile, text=label, font=t.font("caption"), text_color=t.TEXT_MUTED).pack(pady=(0, t.SM))

        # Facts
        facts = ctk.CTkFrame(body, fg_color="transparent")
        facts.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(t.MD, 0))
        facts.grid_columnconfigure(1, weight=1)
        rows = [("Duration", format_duration(info.duration_seconds))]
        if info.stop_reason:
            rows.append(("Stopped because", info.stop_reason))
        rows.append(("Deployment log", info.log_path))
        for r, (label, value) in enumerate(rows):
            ctk.CTkLabel(facts, text=label, font=t.font("small_bold"), text_color=t.TEXT_MUTED, anchor="w",
                         width=130).grid(row=r, column=0, sticky="nw", pady=2)
            ctk.CTkLabel(facts, text=value, font=t.font("mono") if label == "Deployment log" else t.font("body"),
                         text_color=t.TEXT, anchor="w", justify="left", wraplength=440).grid(
                row=r, column=1, sticky="w", pady=2)
        ctk.CTkLabel(facts, text="Exported report", font=t.font("small_bold"), text_color=t.TEXT_MUTED,
                     anchor="w", width=130).grid(row=len(rows), column=0, sticky="nw", pady=2)
        self.report_label = ctk.CTkLabel(facts, text="", font=t.font("body"), text_color=t.TEXT_MUTED,
                                         anchor="w", justify="left", wraplength=440)
        self.report_label.grid(row=len(rows), column=1, sticky="w", pady=2)
        self._show_report(info.report_path)

        # Messages
        notes: list[tuple[str, str]] = []
        if info.stop_message:
            notes.append((info.stop_message, "danger"))
        if info.stop_hint:
            notes.append((info.stop_hint, "warning"))
        if info.unknown:
            notes.append((f"{info.unknown} schedule(s) have an unknown result. Check them in Oracle Task Manager, "
                          "then record the result with “Resolve Unknown…”. They are never retried automatically.",
                          "unknown"))
        if info.failed and not info.stop_reason:
            notes.append(("Failed schedules can be deployed again with “Retry Failed”.", "neutral"))
        for i, (text, note_tone) in enumerate(notes):
            n_fg, n_bg = t.STATUS_TONES[note_tone]
            box = ctk.CTkFrame(body, fg_color=n_bg, corner_radius=t.RADIUS_CONTROL)
            box.grid(row=4 + i, column=0, columnspan=2, sticky="ew", pady=(t.SM, 0))
            ctk.CTkLabel(box, text=text, font=t.font("small"), text_color=n_fg, anchor="w", justify="left",
                         wraplength=560).pack(anchor="w", padx=t.MD, pady=t.SM)

        bar = self.button_bar(1)
        col = 1
        if on_update_password is not None:
            self.password_btn = secondary_button(bar, "Update Password…", self._update_password, width=150)
            self.password_btn.grid(row=0, column=0, sticky="w", padx=t.LG, pady=t.MD)
        self.export_btn = secondary_button(bar, "Export Report…", self._export, width=130)
        self.export_btn.grid(row=0, column=col, pady=t.MD)
        secondary_button(bar, "Close", self.cancel, width=90).grid(row=0, column=col + 1, padx=(t.SM, 0), pady=t.MD)
        self._view = primary_button(bar, "View Results", lambda: self.close("view"), width=130)
        self._view.grid(row=0, column=col + 2, padx=(t.SM, t.LG), pady=t.MD)
        self.bind("<Return>", lambda _e: self.close("view"))

    def initial_focus(self):
        self._view.focus_set()

    def _show_report(self, path: str) -> None:
        if path:
            self.report_label.configure(text=path, text_color=t.TEXT, font=t.font("mono"))
        else:
            self.report_label.configure(text="Not exported yet – use Export Report.", text_color=t.TEXT_MUTED,
                                        font=t.font("body"))

    def _export(self):
        def saved(path: Path):
            if self.winfo_exists():
                self._show_report(str(path))
                self.export_btn.configure(state="normal", text="Export Report…")
        self._on_export(saved)

    def _update_password(self):
        if self._on_update_password and self._on_update_password():
            self.password_btn.configure(state="disabled", text="Password updated")


# --------------------------------------------------------------------------- retry confirmation
class RetryConfirmDialog(BaseDialog):
    """Short confirmation listing the schedules that will be deployed again. True = confirmed."""

    MAX_LISTED = 14

    def __init__(self, parent, env: Environment, rows: list[PlannedSchedule], excluded: list[str]):
        super().__init__(parent, "Retry Failed Schedules", width=660)
        self._production = env.is_production
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=t.XL, pady=(t.XL, t.MD))
        body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(body, text="Retry Failed Schedules", font=t.font("title"), text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        _env_line(body, env.display_name, env.host, env.is_production).grid(row=1, column=0, sticky="w",
                                                                            pady=(t.XS, t.MD))
        hint_label(body, f"These {len(rows)} schedule(s) will be deployed again, one at a time:",
                   wraplength=500).grid(row=2, column=0, sticky="w")
        lst = ctk.CTkFrame(body, fg_color=t.SURFACE, corner_radius=t.RADIUS_CONTROL, border_width=1,
                           border_color=t.BORDER)
        lst.grid(row=3, column=0, sticky="ew", pady=(t.XS, 0))
        lst.grid_columnconfigure(1, weight=1)
        for r, row in enumerate(rows[:self.MAX_LISTED]):
            ctk.CTkLabel(lst, text=row.name, font=t.font("body"), text_color=t.TEXT, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(t.MD, t.SM), pady=(t.XS if r == 0 else 0, 0))
            ctk.CTkLabel(lst, text=f"{row.period_member} · {row.year_member} · Day Zero "
                                   f"{row.day_zero.isoformat() if row.day_zero else '—'}",
                         font=t.font("small"), text_color=t.TEXT_MUTED, anchor="e").grid(
                row=r, column=1, sticky="e", padx=(0, t.MD), pady=(t.XS if r == 0 else 0, 0))
        if len(rows) > self.MAX_LISTED:
            hint_label(lst, f"… and {len(rows) - self.MAX_LISTED} more").grid(
                row=self.MAX_LISTED, column=0, columnspan=2, sticky="w", padx=t.MD)
        ctk.CTkFrame(lst, height=t.XS, fg_color="transparent").grid(row=99, column=0)
        next_row = 4
        if excluded:
            hint_label(body, "Not retried (already created, existing or unknown – see the preview): "
                             + ", ".join(excluded), wraplength=500).grid(row=next_row, column=0, sticky="w",
                                                                         pady=(t.SM, 0))
            next_row += 1
        warn = ctk.CTkFrame(body, fg_color=t.WARNING_BG, corner_radius=t.RADIUS_CONTROL)
        warn.grid(row=next_row, column=0, sticky="ew", pady=(t.MD, 0))
        ctk.CTkLabel(warn, text="⚠  Deployment creates Oracle Task Manager schedules. Schedules recorded as created "
                                "or existing are skipped automatically.",
                     font=t.font("small"), text_color=t.WARNING_FG, anchor="w", justify="left",
                     wraplength=480).pack(anchor="w", padx=t.MD, pady=t.SM)
        self._ack = tk.BooleanVar(value=False)
        if self._production:
            checkbox(body, "I understand this changes the PRODUCTION environment", variable=self._ack,
                     command=self._sync, text_color=t.DANGER_FG, font=t.font("body_bold")).grid(
                row=next_row + 1, column=0, sticky="w", pady=(t.MD, 0))

        bar = self.button_bar(1)
        self._cancel_btn = secondary_button(bar, "Cancel", self.cancel, width=100)
        self._cancel_btn.grid(row=0, column=1, pady=t.MD)
        self._confirm = primary_button(bar, f"Retry {len(rows)} Schedule(s)", lambda: self.close(True), width=170,
                                       danger=self._production)
        self._confirm.grid(row=0, column=2, padx=(t.SM, t.LG), pady=t.MD)
        self._sync()

    def _sync(self):
        set_primary_enabled(self._confirm, (not self._production) or self._ack.get(), danger=self._production)

    def initial_focus(self):
        self._cancel_btn.focus_set()


# --------------------------------------------------------------------------- resolve unknown results
class ResolveUnknownDialog(BaseDialog):
    """Lets the user record what they found in Oracle for every deployment with an unknown result.

    Returns True when at least one entry was resolved (the preview must be re-planned)."""

    def __init__(self, parent, env: Environment, ledger: DeploymentLedger,
                 recheck_factory: Callable[[], Callable[[], list] | None] | None = None):
        """recheck_factory() returns a callable that re-checks the jobs in Oracle (run in a worker thread),
        or None when the user cancelled the password prompt."""
        super().__init__(parent, "Resolve Unknown Results", width=760, height=520, resizable=True)
        self.minsize(600, 380)
        self.ledger = ledger
        self.changed = False
        self._recheck_factory = recheck_factory
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=t.XL, pady=(t.LG, t.SM))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Resolve Unknown Results", font=t.font("title"), text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        _env_line(head, env.display_name, env.host, env.is_production).grid(row=1, column=0, sticky="w")
        hint_label(head, "Oracle did not confirm the result of these deployments. Open Task Manager → Schedules "
                         "in Oracle, look for each schedule name, and record what you found. “Not in Oracle” "
                         "allows the schedule to be deployed again; “Exists in Oracle” skips it from now on.",
                   wraplength=700).grid(row=2, column=0, sticky="w", pady=(t.XS, 0))

        self.list = ctk.CTkScrollableFrame(self, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD,
                                           border_width=1, border_color=t.BORDER)
        self.list.grid(row=1, column=0, sticky="nsew", padx=t.XL)
        self.list.grid_columnconfigure(0, weight=1)
        self._fill_list()

        bar = self.button_bar(2)
        bar.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=t.LG, pady=t.MD)
        self._recheck_btn = secondary_button(left, "Re-check job status in Oracle", self._recheck, width=220)
        self._recheck_btn.pack(side="left")
        self._recheck_note = hint_label(left, "", wraplength=300)
        self._recheck_note.pack(side="left", padx=(t.SM, 0))
        if self._recheck_factory is None or not any(e.status_url or e.job_id for e in self._unknown()):
            self._recheck_btn.configure(state="disabled")
        self._close = primary_button(bar, "Close", self.cancel, width=100)
        self._close.grid(row=0, column=1, padx=t.LG, pady=t.MD)

    def _unknown(self) -> list[LedgerEntry]:
        return sorted(self.ledger.with_status(LedgerStatus.UNKNOWN), key=lambda e: e.schedule_name)

    def _fill_list(self):
        for child in self.list.winfo_children():
            child.destroy()
        entries = self._unknown()
        if not entries:
            hint_label(self.list, "There are no unknown results for this environment.").grid(
                row=0, column=0, sticky="w", padx=t.MD, pady=t.MD)
        for r, entry_ in enumerate(entries):
            self._make_row(r, entry_)

    def _recheck(self):
        run = self._recheck_factory() if self._recheck_factory else None
        if run is None:
            return
        self._recheck_btn.configure(state="disabled")
        self._recheck_note.configure(text="Asking Oracle…")
        BackgroundTask(self, run, self._recheck_done, self._recheck_failed).start()

    def _recheck_done(self, results: list):
        resolved = [r for r in results if r.status != LedgerStatus.UNKNOWN]
        if resolved:
            self.changed = True
        self._recheck_note.configure(
            text=f"{len(resolved)} of {len(results)} resolved by Oracle." +
                 ("" if len(resolved) == len(results) else " Resolve the rest manually."))
        self._fill_list()
        self._recheck_btn.configure(state="normal" if any(e.status_url or e.job_id for e in self._unknown())
                                    else "disabled")

    def _recheck_failed(self, exc: BaseException):
        self._recheck_note.configure(text=f"Re-check failed: {exc}")
        self._recheck_btn.configure(state="normal")

    def initial_focus(self):
        self._close.focus_set()

    def cancel(self):
        self.close(self.changed)

    def _make_row(self, r: int, e: LedgerEntry):
        row = ctk.CTkFrame(self.list, fg_color="transparent")
        row.grid(row=r, column=0, sticky="ew", padx=t.SM, pady=(t.SM, 0))
        row.grid_columnconfigure(0, weight=1)
        if r:
            ctk.CTkFrame(row, height=1, fg_color=t.BORDER).grid(row=0, column=0, columnspan=2, sticky="ew",
                                                                pady=(0, t.SM))
        ctk.CTkLabel(row, text=e.schedule_name, font=t.font("body_bold"), text_color=t.TEXT, anchor="w").grid(
            row=1, column=0, sticky="w")
        facts = [f"Year {e.year}", f"Period {e.period}", f"Day Zero {e.day_zero}"]
        if e.job_id:
            facts.append(f"Oracle job {e.job_id}")
        facts.append(f"attempted {e.updated_at.replace('T', ' ')}")
        hint_label(row, " · ".join(facts), wraplength=380).grid(row=2, column=0, sticky="w")
        if e.details:
            hint_label(row, e.details, tone="unknown", wraplength=380).grid(row=3, column=0, sticky="w")
        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.grid(row=1, column=1, rowspan=3, sticky="e", padx=(t.MD, 0))
        exists = secondary_button(actions, "Exists in Oracle", width=130)
        missing = secondary_button(actions, "Not in Oracle", width=120)
        exists.configure(command=lambda: self._resolve(e.schedule_name, True, actions))
        missing.configure(command=lambda: self._resolve(e.schedule_name, False, actions))
        exists.pack(side="left", padx=(0, t.SM))
        missing.pack(side="left")

    def _resolve(self, name: str, exists: bool, actions: ctk.CTkFrame):
        try:
            self.ledger.resolve_unknown(name, exists)
        except OSError as exc:
            hint_label(actions, f"Not saved: {exc}", tone="danger").pack(side="left", padx=(t.SM, 0))
            return
        self.changed = True
        for child in actions.winfo_children():
            child.destroy()
        Badge(actions, "Recorded: exists in Oracle – will be skipped" if exists else
              "Recorded: not in Oracle – can be deployed again", "neutral" if exists else "info").pack(side="left")


# --------------------------------------------------------------------------- deployment history
HISTORY_COLUMNS = (
    ("name", "Schedule", 260, True, "w"),
    ("status", "Status", 100, False, "w"),
    ("year", "Year", 70, False, "w"),
    ("period", "Period", 70, False, "w"),
    ("dayzero", "Day Zero", 95, False, "w"),
    ("job", "Job ID", 80, False, "w"),
    ("updated", "Updated", 140, False, "w"),
    ("details", "Details", 320, True, "w"),
)
_HISTORY_TONE = {LedgerStatus.COMPLETED: "success", LedgerStatus.EXISTS: "neutral", LedgerStatus.FAILED: "danger",
                 LedgerStatus.UNKNOWN: "unknown"}
_HISTORY_LABEL = {LedgerStatus.COMPLETED: "Completed", LedgerStatus.EXISTS: "Exists", LedgerStatus.FAILED: "Failed",
                  LedgerStatus.UNKNOWN: "Unknown"}


class DeploymentHistoryDialog(BaseDialog):
    """Read-only view of the local deployment ledger of one environment."""

    STYLE = "History.Treeview"

    def __init__(self, parent, env: Environment, ledger: DeploymentLedger):
        super().__init__(parent, "Deployment History", width=1000, height=560, resizable=True)
        self.minsize(720, 380)
        self.grid_rowconfigure(1, weight=1)
        entries = ledger.entries()
        self._by_iid: dict[str, LedgerEntry] = {}

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=t.XL, pady=(t.LG, t.SM))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Deployment History", font=t.font("title"), text_color=t.TEXT,
                     anchor="w").grid(row=0, column=0, sticky="w")
        _env_line(head, env.display_name, env.host, env.is_production).grid(row=1, column=0, sticky="w")
        counts = {s: sum(1 for e in entries if e.status == s) for s in _HISTORY_LABEL}
        chips = ctk.CTkFrame(head, fg_color="transparent")
        chips.grid(row=0, column=1, rowspan=2, sticky="e")
        for status, label in _HISTORY_LABEL.items():
            Badge(chips, f"{counts[status]} {label.lower()}",
                  _HISTORY_TONE[status] if counts[status] else "neutral").pack(side="left", padx=(t.XS, 0))
        hint_label(head, "Recorded on this computer for every deployment attempt. Oracle has no API that lists "
                         "schedules, so this history is what prevents duplicates on retry.",
                   wraplength=900).grid(row=2, column=0, columnspan=2, sticky="w", pady=(t.XS, 0))

        table = ctk.CTkFrame(self, fg_color=t.BORDER, corner_radius=t.RADIUS_CONTROL)
        table.grid(row=1, column=0, sticky="nsew", padx=t.XL)
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        style = ttk.Style(self)
        bg, fg = t.resolve(t.SURFACE_SUNKEN), t.resolve(t.TEXT)
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        style.configure(self.STYLE, background=bg, fieldbackground=bg, foreground=fg, borderwidth=0,
                        rowheight=int(26 * scaling), font=t.tk_font("small"))
        style.configure(f"{self.STYLE}.Heading", background=t.resolve(t.SURFACE_ALT),
                        foreground=t.resolve(t.TEXT_MUTED), relief="flat", borderwidth=0,
                        font=t.tk_font("small_bold"), padding=(8, 6))
        style.map(f"{self.STYLE}.Heading", background=[("active", t.resolve(t.BORDER))])
        style.map(self.STYLE, background=[("selected", t.resolve(t.ROW_SELECTED))], foreground=[("selected", fg)])
        style.layout(self.STYLE, [("Treeview.treearea", {"sticky": "nswe"})])
        self.tree = ttk.Treeview(table, columns=[c[0] for c in HISTORY_COLUMNS], show="headings",
                                 style=self.STYLE, selectmode="browse")
        for cid, heading, width, stretch, anchor in HISTORY_COLUMNS:
            self.tree.heading(cid, text=heading, anchor=anchor)
            self.tree.column(cid, width=int(width * scaling), minwidth=int(40 * scaling), stretch=stretch,
                             anchor=anchor)
        for status, tone in _HISTORY_TONE.items():
            self.tree.tag_configure(str(status), background=t.resolve(t.STATUS_TONES[tone][1]), foreground=fg)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        scroll = ctk.CTkScrollbar(table, command=self.tree.yview, fg_color=t.SURFACE_SUNKEN,
                                  button_color=t.BORDER_STRONG, button_hover_color=t.TEXT_MUTED)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.tree.configure(yscrollcommand=scroll.set)
        for i, e in enumerate(entries):
            iid = str(i)
            self._by_iid[iid] = e
            self.tree.insert("", "end", iid=iid, tags=(e.status,), values=(
                e.schedule_name, _HISTORY_LABEL.get(e.status, e.status), e.year, e.period, e.day_zero,
                e.job_id or "—", e.updated_at.replace("T", " "), e.details.replace("\n", " "),
            ))
        if not entries:
            ctk.CTkLabel(table, text="Nothing has been deployed to this environment from this computer yet.",
                         font=t.font("body"), text_color=t.TEXT_MUTED, fg_color=t.SURFACE_SUNKEN).place(
                relx=0.5, rely=0.5, anchor="center")

        self.detail = hint_label(self, "Select a row to see the full details.", wraplength=940)
        self.detail.grid(row=2, column=0, sticky="ew", padx=t.XL, pady=(t.SM, t.SM))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.bind("<Configure>", lambda e: self.detail.configure(wraplength=max(300, e.width - 60))
                  if e.widget is self else None, add="+")

        bar = self.button_bar(3)
        self._close = primary_button(bar, "Close", self.cancel, width=100)
        self._close.grid(row=0, column=1, padx=t.LG, pady=t.MD)

    def initial_focus(self):
        self._close.focus_set()

    def _show_selected(self):
        sel = self.tree.selection()
        e = self._by_iid.get(sel[0]) if sel else None
        if not e:
            return
        parts = [f"{e.schedule_name}: {_HISTORY_LABEL.get(e.status, e.status)}",
                 f"template “{e.template_name}”" if e.template_name else "",
                 f"org unit {e.org_unit}" if e.org_unit else "", f"{e.attempts} attempt(s)",
                 e.details]
        self.detail.configure(text=" · ".join(p for p in parts if p),
                              text_color=t.STATUS_TONES[_HISTORY_TONE.get(e.status, "neutral")][0])
