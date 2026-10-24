"""Step 3: preview of the planned schedules (ttk.Treeview restyled for light and dark mode)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from app.models import PlannedSchedule, ScheduleStatus
from app.services.naming_service import MONTH_NAMES
from app.services.schedule_planner import PlanResult
from app.ui import theme as t
from app.ui.widgets import Card, Tooltip, hint_label, secondary_button

STYLE = "Preview.Treeview"

COLUMNS = (
    # id, heading, width, stretch, anchor
    ("idx", "#", 44, False, "e"),
    ("name", "Schedule Name", 300, True, "w"),
    ("month", "Month", 125, False, "w"),
    ("period", "Oracle Period", 105, False, "w"),
    ("year", "Oracle Year", 95, False, "w"),
    ("dayzero", "Day Zero", 100, False, "w"),
    ("status", "Status", 275, False, "w"),
)

# status -> (tag, tone, symbol). Symbols keep the status readable without colour.
STATUS_STYLE = {
    ScheduleStatus.READY: ("ready", "success", "✓"),
    ScheduleStatus.READY_UNCHECKED: ("unchecked", "warning", "○"),
    ScheduleStatus.SKIPPED_EXISTS: ("skipped", "neutral", "–"),
    ScheduleStatus.SKIPPED: ("skipped", "neutral", "–"),
    ScheduleStatus.BLOCKED: ("blocked", "danger", "✕"),
    ScheduleStatus.FAILED: ("blocked", "danger", "✕"),
    ScheduleStatus.COMPLETED: ("ready", "success", "✓"),
    ScheduleStatus.UNKNOWN: ("unknown", "unknown", "?"),
    ScheduleStatus.PENDING: ("pending", "neutral", "…"),
    ScheduleStatus.VALIDATING: ("running", "info", "▸"),
    ScheduleStatus.DEPLOYING: ("running", "info", "▸"),
}
# Detail text colour per status (messages of the selected row)
_DETAIL_TONE = {
    ScheduleStatus.COMPLETED: "success",
    ScheduleStatus.READY: "success",
    ScheduleStatus.SKIPPED_EXISTS: "neutral",
    ScheduleStatus.SKIPPED: "neutral",
    ScheduleStatus.PENDING: "neutral",
    ScheduleStatus.READY_UNCHECKED: "warning",
    ScheduleStatus.UNKNOWN: "unknown",
    ScheduleStatus.VALIDATING: "info",
    ScheduleStatus.DEPLOYING: "info",
}


def month_label(row: PlannedSchedule) -> str:
    return f"{MONTH_NAMES[row.month.month - 1]} {row.month.year}"


_RUN_STATUSES = (ScheduleStatus.COMPLETED, ScheduleStatus.FAILED, ScheduleStatus.UNKNOWN, ScheduleStatus.PENDING,
                 ScheduleStatus.DEPLOYING, ScheduleStatus.VALIDATING, ScheduleStatus.SKIPPED)


def summary_text(result: PlanResult) -> str:
    requested = len(result.rows)
    skipped = result.count(ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED)
    blocked = result.count(ScheduleStatus.BLOCKED)
    if not result.count(*_RUN_STATUSES):
        ready = len(result.deployable)
        return f"{requested} requested · {ready} ready · {skipped} skipped · {blocked} blocked"
    parts = [f"{requested} requested", f"{result.count(ScheduleStatus.COMPLETED)} completed"]
    for label, statuses in (("skipped", (ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.SKIPPED)),
                            ("failed", (ScheduleStatus.FAILED,)), ("unknown", (ScheduleStatus.UNKNOWN,)),
                            ("in progress", (ScheduleStatus.DEPLOYING, ScheduleStatus.VALIDATING)),
                            ("pending", (ScheduleStatus.PENDING,)), ("blocked", (ScheduleStatus.BLOCKED,))):
        n = result.count(*statuses)
        if n:
            parts.append(f"{n} {label}")
    return " · ".join(parts)


class PreviewTable(Card):
    def __init__(self, master, on_check=None):
        super().__init__(master, "Preview", step="3")
        self.result: PlanResult | None = None
        self._rows_by_iid: dict[str, PlannedSchedule] = {}
        self._tip: tk.Toplevel | None = None
        self._tip_iid: str | None = None

        self.check_btn = secondary_button(self.header_actions, "Check in Oracle", on_check, width=130)
        self.check_btn.pack(side="right", padx=(t.MD, 0))
        Tooltip(self.check_btn, "Verify the Year and Period members of every month in Oracle (read-only). "
                                "Nothing is changed in Oracle.")
        self.summary = ctk.CTkLabel(self.header_actions, text="", font=t.font("small_bold"),
                                    text_color=t.TEXT_MUTED)
        self.summary.pack(side="right")

        b = self.body
        b.grid_columnconfigure(0, weight=1)
        b.grid_rowconfigure(2, weight=1)

        # Result of the last Oracle check
        self.check_status = hint_label(b, "", wraplength=900)
        self.check_status.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, t.SM))

        # Plan-level errors
        self.errors = ctk.CTkFrame(b, fg_color=t.DANGER_BG, corner_radius=t.RADIUS_CONTROL)
        self.errors.grid_columnconfigure(0, weight=1)
        self.errors.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, t.SM))
        self.errors.grid_remove()

        # Table
        table = ctk.CTkFrame(b, fg_color=t.BORDER, corner_radius=t.RADIUS_CONTROL)
        table.grid(row=2, column=0, columnspan=2, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        self.style = ttk.Style(self)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in COLUMNS], show="headings", style=STYLE,
                                 selectmode="browse", height=8)
        scaling = ctk.ScalingTracker.get_widget_scaling(self)  # ttk sizes are raw pixels: apply DPI scaling
        for cid, heading, width, stretch, anchor in COLUMNS:
            self.tree.heading(cid, text=heading, anchor=anchor)
            self.tree.column(cid, width=int(width * scaling), minwidth=int(40 * scaling), stretch=stretch,
                             anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.scroll = ctk.CTkScrollbar(table, command=self.tree.yview, fg_color=t.SURFACE_SUNKEN,
                                       button_color=t.BORDER_STRONG, button_hover_color=t.TEXT_MUTED)
        self.scroll.grid(row=0, column=1, sticky="ns", padx=(0, 1), pady=1)
        self.tree.configure(yscrollcommand=self.scroll.set)

        self.empty = ctk.CTkLabel(table, text="", font=t.font("body"), text_color=t.TEXT_MUTED,
                                  fg_color=t.SURFACE_SUNKEN)

        # Row details (messages of the selected row)
        self.detail = hint_label(b, "Select a row to see its details.", wraplength=900)
        self.detail.grid(row=3, column=0, sticky="ew", pady=(t.SM, 0))

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", lambda _e: self._hide_tip())
        self.bind("<Configure>", self._on_resize, add="+")

        self.apply_style()
        self.show(None)

    def _on_resize(self, event):
        width = max(300, event.width - 60)
        self.detail.configure(wraplength=width)
        self.check_status.configure(wraplength=width)

    def set_check_status(self, text: str, tone: str | None = None) -> None:
        color = t.STATUS_TONES[tone][0] if tone else t.TEXT_MUTED
        self.check_status.configure(text=text, text_color=color)

    def set_check_enabled(self, enabled: bool, busy: bool = False) -> None:
        self.check_btn.configure(state="normal" if enabled else "disabled",
                                 text="Checking…" if busy else "Check in Oracle")

    # ---- theming
    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        self.after(0, self.apply_style)  # theme.resolve() reads the global mode, already switched

    def apply_style(self):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        bg, fg = t.resolve(t.SURFACE_SUNKEN), t.resolve(t.TEXT)
        try:
            self.style.theme_use("clam")  # the only built-in ttk theme that honours custom colours
        except tk.TclError:
            pass
        self.style.configure(STYLE, background=bg, fieldbackground=bg, foreground=fg, borderwidth=0,
                             rowheight=int(28 * scaling), font=t.tk_font("body"))
        self.style.configure(f"{STYLE}.Heading", background=t.resolve(t.SURFACE_ALT), foreground=t.resolve(t.TEXT_MUTED),
                             relief="flat", borderwidth=0, font=t.tk_font("small_bold"), padding=(8, 6))
        self.style.map(f"{STYLE}.Heading", background=[("active", t.resolve(t.BORDER))])
        self.style.map(STYLE, background=[("selected", t.resolve(t.ROW_SELECTED))],
                       foreground=[("selected", fg)])
        self.style.layout(STYLE, [("Treeview.treearea", {"sticky": "nswe"})])  # no extra border
        for tag, tone, _ in set(STATUS_STYLE.values()):
            row_fg, row_bg = t.STATUS_TONES[tone]
            self.tree.tag_configure(tag, background=t.resolve(row_bg), foreground=fg)
        self.tree.tag_configure("other", background=bg, foreground=fg)

    # ---- data
    def show(self, result: PlanResult | None, empty_text: str = "") -> None:
        self.result = result
        selected = self.tree.selection()
        keep = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        self._rows_by_iid.clear()

        # Errors above the table
        for child in self.errors.winfo_children():
            child.destroy()
        if result and result.errors:
            for i, message in enumerate(result.errors):
                ctk.CTkLabel(self.errors, text="✕  " + message, font=t.font("small"), text_color=t.DANGER_FG,
                             anchor="w", justify="left").grid(row=i, column=0, sticky="w", padx=t.MD,
                                                             pady=(t.SM if i == 0 else 0, t.SM if i == len(result.errors) - 1 else 0))
            self.errors.grid()
        else:
            self.errors.grid_remove()

        if not result or not result.rows:
            self.summary.configure(text="")
            self.empty.configure(text=empty_text or "Choose a period range to see the planned schedules.")
            self.empty.place(relx=0.5, rely=0.55, anchor="center")
            self.detail.configure(text="")
            return
        self.empty.place_forget()

        for row in result.rows:
            tag, values = self._row_view(row)
            iid = str(row.index)
            self._rows_by_iid[iid] = row
            self.tree.insert("", "end", iid=iid, tags=(tag,), values=values)
        self.summary.configure(text=summary_text(result))
        if keep and keep in self._rows_by_iid:
            self.tree.selection_set(keep)
        else:
            self._show_selected()

    @staticmethod
    def _row_view(row: PlannedSchedule) -> tuple[str, tuple]:
        tag, _tone, symbol = STATUS_STYLE.get(row.status, ("other", "neutral", ""))
        return tag, (
            row.index,
            row.name or "—",
            month_label(row),
            row.period_member or "—",
            row.year_member or "—",
            row.day_zero.isoformat() if row.day_zero else "—",
            f"{symbol}  {row.status.value}" if symbol else row.status.value,
        )

    def update_row(self, row: PlannedSchedule, reveal: bool = False) -> None:
        """Live update of one row during deployment (the row object is shared with the deployer)."""
        iid = str(row.index)
        if not self.tree.exists(iid):
            return
        tag, values = self._row_view(row)
        self.tree.item(iid, values=values, tags=(tag,))
        if reveal:
            self.tree.see(iid)
        if self.result:
            self.summary.configure(text=summary_text(self.result))
        sel = self.tree.selection()
        if sel and sel[0] == iid:
            self._show_selected()
        if self._tip_iid == iid:
            self._hide_tip()

    def focus_results(self) -> None:
        """Select the first row that needs attention (failed / unknown / pending) and focus the table."""
        if not self.result or not self.result.rows:
            return
        order = (ScheduleStatus.FAILED, ScheduleStatus.UNKNOWN, ScheduleStatus.PENDING, ScheduleStatus.BLOCKED)
        target = next((r for s in order for r in self.result.rows if r.status == s), self.result.rows[0])
        iid = str(target.index)
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.tree.focus_set()

    def _show_selected(self):
        sel = self.tree.selection()
        row = self._rows_by_iid.get(sel[0]) if sel else None
        if not row:
            blocked = self.result.count(ScheduleStatus.BLOCKED) if self.result else 0
            self.detail.configure(
                text="Select a row to see its details." + (" Blocked rows explain the problem when selected."
                                                          if blocked else ""),
                text_color=t.TEXT_MUTED)
            return
        if row.messages:
            tone = _DETAIL_TONE.get(row.status, "danger")
            self.detail.configure(text=f"Row {row.index} · {row.name or month_label(row)} · {row.status.value}: "
                                       + " ".join(row.messages), text_color=t.STATUS_TONES[tone][0])
        elif row.status == ScheduleStatus.READY_UNCHECKED:
            self.detail.configure(text=f"Row {row.index} · {row.name}: ready. Year/Period members have not "
                                       "been checked in Oracle yet; this happens before deployment.", text_color=t.TEXT_MUTED)
        else:
            self.detail.configure(text=f"Row {row.index} · {row.name}: {row.status.value}.",
                                  text_color=t.TEXT_MUTED)

    # ---- hover tooltip with row messages
    def _on_motion(self, event):
        iid = self.tree.identify_row(event.y)
        row = self._rows_by_iid.get(iid)
        if not row or not row.messages:
            self._hide_tip()
            return
        if iid == self._tip_iid and self._tip is not None:
            self._tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 16}")
            return
        self._hide_tip()
        self._tip_iid = iid
        self._tip = tk.Toplevel(self)
        self._tip.wm_overrideredirect(True)
        self._tip.attributes("-topmost", True)
        frame = tk.Frame(self._tip, bg=t.resolve(t.BORDER_STRONG))
        frame.pack()
        tone = _DETAIL_TONE.get(row.status, "danger")
        tk.Label(frame, text="\n".join(row.messages), justify="left", wraplength=420, bg=t.resolve(t.SURFACE),
                 fg=t.resolve(t.STATUS_TONES[tone][0]), font=t.tk_font("small"), padx=8,
                 pady=5).pack(padx=1, pady=1)
        self._tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 16}")

    def _hide_tip(self):
        if self._tip is not None:
            self._tip.destroy()
        self._tip = None
        self._tip_iid = None
