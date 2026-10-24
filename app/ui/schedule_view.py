"""Step 2: period range, name pattern and schedule options. Defaults come from the selected environment."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import date
from typing import Callable

import customtkinter as ctk

from app.models import Environment, MonthRef
from app.services.naming_service import MONTH_NAMES, PLACEHOLDERS
from app.services.period_service import DAY_ZERO_STRATEGIES, PeriodMapping
from app.ui import theme as t
from app.ui.widgets import Card, Tooltip, entry, field_label, hint_label, link_button, option_menu

DEFAULT_PATTERN = "Monthly Close - {MMMM} {YYYY}"
FIXED_DAYS = [str(d) for d in range(1, 32)]


@dataclass
class ScheduleParams:
    start: MonthRef
    end: MonthRef
    name_pattern: str
    org_unit: str
    mapping_id: str
    day_zero_strategy: str
    day_zero_fixed_day: int


def year_options(today: date | None = None) -> list[str]:
    year = (today or date.today()).year
    return [str(y) for y in range(year - 1, year + 6)]


class ScheduleView(Card):
    def __init__(self, master, mappings: dict[str, PeriodMapping], on_change: Callable[[], None]):
        super().__init__(master, "Schedules", step="2")
        self.mappings = mappings
        self.on_change = on_change
        self._mapping_by_label = {m.display_name: m.id for m in mappings.values()}
        self._strategy_by_label = {v: k for k, v in DAY_ZERO_STRATEGIES.items()}
        years = year_options()
        next_year = str(date.today().year + 1)
        changed = lambda *_a: self.on_change()  # noqa: E731

        b = self.body
        for col in range(4):
            b.grid_columnconfigure(col, weight=1, uniform="sched")

        # Row 0-1: From / To
        field_label(b, "From").grid(row=0, column=0, sticky="w")
        frm = ctk.CTkFrame(b, fg_color="transparent")
        frm.grid(row=1, column=0, sticky="w", padx=(0, t.MD))
        self.from_month = option_menu(frm, list(MONTH_NAMES), width=120, command=changed)
        self.from_month.pack(side="left")
        self.from_year = option_menu(frm, years, width=76, command=changed)
        self.from_year.pack(side="left", padx=(t.XS, 0))

        field_label(b, "To").grid(row=0, column=1, sticky="w")
        to = ctk.CTkFrame(b, fg_color="transparent")
        to.grid(row=1, column=1, sticky="w", padx=(0, t.MD))
        self.to_month = option_menu(to, list(MONTH_NAMES), width=120, command=changed)
        self.to_month.pack(side="left")
        self.to_year = option_menu(to, years, width=76, command=changed)
        self.to_year.pack(side="left", padx=(t.XS, 0))

        self.from_month.set(MONTH_NAMES[0])
        self.to_month.set(MONTH_NAMES[11])
        self.from_year.set(next_year)
        self.to_year.set(next_year)

        # Name pattern spans two columns
        pat_head = ctk.CTkFrame(b, fg_color="transparent")
        pat_head.grid(row=0, column=2, columnspan=2, sticky="ew")
        field_label(pat_head, "Schedule name pattern").pack(side="left")
        self.help_btn = link_button(pat_head, "{ } Insert placeholder", self._show_placeholders)
        self.help_btn.pack(side="right")
        Tooltip(self.help_btn, "\n".join(f"{{{k}}}  –  {v}" for k, v in PLACEHOLDERS.items()))
        self.pattern = entry(b, width=260)
        self.pattern.grid(row=1, column=2, columnspan=2, sticky="ew")
        self.pattern.insert(0, DEFAULT_PATTERN)
        self.pattern.bind("<KeyRelease>", changed)

        # Row 2-3: Org unit / mapping / Day Zero / fixed day
        field_label(b, "Organizational Unit").grid(row=2, column=0, sticky="w", pady=(t.SM, 0))
        self.org_unit = entry(b, width=200, placeholder_text="Optional")
        self.org_unit.grid(row=3, column=0, sticky="ew", padx=(0, t.MD))
        self.org_unit.bind("<KeyRelease>", changed)
        Tooltip(self.org_unit, "Oracle uses the organizational unit for holidays and working days. "
                               "Leave blank to use the standard date mapping.")

        field_label(b, "Period mapping").grid(row=2, column=1, sticky="w", pady=(t.SM, 0))
        self.mapping = option_menu(b, list(self._mapping_by_label), width=200, command=changed)
        self.mapping.grid(row=3, column=1, sticky="ew", padx=(0, t.MD))
        Tooltip(self.mapping, lambda: self.mapping.get())

        field_label(b, "Day Zero date").grid(row=2, column=2, sticky="w", pady=(t.SM, 0))
        self.strategy = option_menu(b, list(self._strategy_by_label), width=200,
                                    command=lambda _v: (self._sync_fixed(), self.on_change()))
        self.strategy.grid(row=3, column=2, sticky="ew", padx=(0, t.MD))

        self.fixed_label = field_label(b, "Day of month")
        self.fixed_label.grid(row=2, column=3, sticky="w", pady=(t.SM, 0))
        self.fixed_day = option_menu(b, FIXED_DAYS, width=80, command=changed)
        self.fixed_day.grid(row=3, column=3, sticky="w")
        self.fixed_day.set("5")

        default_mapping = mappings.get("standard_monthly") or next(iter(mappings.values()), None)
        if default_mapping:
            self.mapping.set(default_mapping.display_name)
        self.strategy.set(DAY_ZERO_STRATEGIES["last_calendar_day"])
        self._sync_fixed()

        self._menu: tk.Menu | None = None

    # ---- public
    def apply_environment_defaults(self, env: Environment | None) -> None:
        if env is None:
            return
        self.org_unit.delete(0, "end")
        if env.default_org_unit:
            self.org_unit.insert(0, env.default_org_unit)
        else:
            # CTkEntry shows its placeholder again only after losing focus with an empty value
            self.org_unit._activate_placeholder()
        mapping = self.mappings.get(env.period_mapping)
        if mapping:
            self.mapping.set(mapping.display_name)
        self.strategy.set(DAY_ZERO_STRATEGIES.get(env.day_zero_strategy, DAY_ZERO_STRATEGIES["last_calendar_day"]))
        self.fixed_day.set(str(env.day_zero_fixed_day))
        self._sync_fixed()

    def params(self) -> ScheduleParams:
        return ScheduleParams(
            start=MonthRef(int(self.from_year.get()), MONTH_NAMES.index(self.from_month.get()) + 1),
            end=MonthRef(int(self.to_year.get()), MONTH_NAMES.index(self.to_month.get()) + 1),
            name_pattern=self.pattern.get(),
            org_unit=self.org_unit.get().strip(),
            mapping_id=self._mapping_by_label.get(self.mapping.get(), "standard_monthly"),
            day_zero_strategy=self._strategy_by_label.get(self.strategy.get(), "last_calendar_day"),
            day_zero_fixed_day=int(self.fixed_day.get() or 5),
        )

    def set_locked(self, locked: bool) -> None:
        """Configuration cannot change while Oracle work (check / deployment) is running."""
        state = "disabled" if locked else "normal"
        for widget in (self.from_month, self.from_year, self.to_month, self.to_year, self.pattern, self.org_unit,
                       self.mapping, self.strategy, self.fixed_day, self.help_btn):
            widget.configure(state=state)

    # ---- internal
    def _sync_fixed(self):
        fixed = self._strategy_by_label.get(self.strategy.get()) == "fixed_day"
        for w in (self.fixed_label, self.fixed_day):
            if fixed:
                w.grid()
            else:
                w.grid_remove()

    def _show_placeholders(self):
        if self._menu is None:
            self._menu = tk.Menu(self, tearoff=False)
        menu = self._menu
        menu.delete(0, "end")
        menu.configure(bg=t.resolve(t.SURFACE), fg=t.resolve(t.TEXT), activebackground=t.resolve(t.ROW_SELECTED),
                       activeforeground=t.resolve(t.TEXT), font=t.tk_font("small"))
        for key, description in PLACEHOLDERS.items():
            menu.add_command(label=f"{{{key}}}   {description}", command=lambda k=key: self._insert(k))
        x = self.help_btn.winfo_rootx()
        y = self.help_btn.winfo_rooty() + self.help_btn.winfo_height()
        menu.tk_popup(x, y)

    def _insert(self, key: str):
        self.pattern.insert("insert", "{" + key + "}")
        self.pattern.focus_set()
        self.on_change()
