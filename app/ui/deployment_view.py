"""Step 4: deployment controls, live progress and run counters."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from app.ui import theme as t
from app.ui.widgets import Tooltip, hint_label, primary_button, secondary_button, set_primary_enabled


class DeploymentView(ctk.CTkFrame):
    def __init__(self, master, on_deploy: Callable[[], None], on_export: Callable[[], None],
                 on_retry: Callable[[], None], on_cancel: Callable[[], None],
                 on_resolve: Callable[[], None], on_history: Callable[[], None]):
        super().__init__(master, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD, border_width=1,
                         border_color=t.BORDER)
        self.grid_columnconfigure(1, weight=1)
        self._reasons: list[str] = []
        self._retry_tip = ""
        self.running = False

        ctk.CTkLabel(self, text="4", width=24, height=24, corner_radius=12, fg_color=t.INFO_BG,
                     text_color=t.INFO_FG, font=t.font("small_bold")).grid(
            row=0, column=0, sticky="n", padx=(t.CARD_PAD, t.SM), pady=(t.MD, 0))

        # ---- headline row: title + counters
        info = ctk.CTkFrame(self, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", pady=(t.MD, 0), padx=(0, t.CARD_PAD))
        info.grid_columnconfigure(0, weight=1)
        self.headline = ctk.CTkLabel(info, text="Deploy", font=t.font("h2"), text_color=t.TEXT, anchor="w")
        self.headline.grid(row=0, column=0, sticky="w")
        counters = ctk.CTkFrame(info, fg_color="transparent")
        counters.grid(row=0, column=1, sticky="e")
        self.counters: dict[str, ctk.CTkLabel] = {}
        for key, symbol, tone, tip in (("completed", "✔", "success", "Completed"),
                                       ("skipped", "»", "neutral", "Skipped (already exist)"),
                                       ("failed", "✖", "danger", "Failed"),
                                       ("unknown", "?", "unknown", "Unknown – check Oracle")):
            label = ctk.CTkLabel(counters, text=f"{symbol} 0", font=t.font("body_bold"),
                                 text_color=t.STATUS_TONES[tone][0], width=44, anchor="e")
            label.pack(side="left", padx=(t.SM, 0))
            Tooltip(label, tip)
            self.counters[key] = label
        self.reason = hint_label(info, "", wraplength=520)
        self.reason.grid(row=1, column=0, columnspan=2, sticky="w")

        # ---- progress row
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=1, column=1, sticky="ew", pady=(t.XS, 0), padx=(0, t.CARD_PAD))
        prog.grid_columnconfigure(0, weight=1)
        # Idle: progress colour equals the track so no stray dot is drawn at 0 %
        self.progress = ctk.CTkProgressBar(prog, height=6, corner_radius=3, fg_color=t.SURFACE_ALT,
                                           progress_color=t.SURFACE_ALT)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.set(0)
        self.progress_label = ctk.CTkLabel(prog, text="No deployment in progress", font=t.font("small"),
                                           text_color=t.TEXT_MUTED, anchor="w", justify="left", height=18)
        self.progress_label.grid(row=1, column=0, sticky="ew", pady=(t.XS, 0))
        self.bind("<Configure>", self._on_resize, add="+")

        # ---- buttons
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=t.CARD_PAD, pady=t.MD)
        buttons.grid_columnconfigure(3, weight=1)
        self.history_btn = secondary_button(buttons, "Deployment History…", on_history, width=160)
        self.history_btn.grid(row=0, column=0, padx=(0, t.SM))
        Tooltip(self.history_btn, "Schedules this computer deployed to the selected environment")
        self.export_btn = secondary_button(buttons, "Export Report…", on_export, width=130)
        self.export_btn.grid(row=0, column=1, padx=(0, t.SM))
        Tooltip(self.export_btn, "Save the preview or the deployment results, validation issues and history "
                                 "to Excel")
        self.resolve_btn = ctk.CTkButton(
            buttons, text="Resolve Unknown…", command=on_resolve, width=150, height=t.BUTTON_HEIGHT,
            corner_radius=t.RADIUS_CONTROL, border_width=1, font=t.font("body_bold"), fg_color=t.UNKNOWN_BG,
            hover_color=t.WARNING_BG, border_color=t.UNKNOWN_FG, text_color=t.UNKNOWN_FG,
            text_color_disabled=t.TEXT_DISABLED)
        self.resolve_btn.grid(row=0, column=2, padx=(0, t.SM))
        Tooltip(self.resolve_btn, "Some deployments have an unknown result. Check them in Oracle and record "
                                  "what you found.")
        self.resolve_btn.grid_remove()

        self.retry_btn = secondary_button(buttons, "Retry Failed", on_retry, width=110, state="disabled")
        self.retry_btn.grid(row=0, column=4, padx=(0, t.SM))
        Tooltip(self.retry_btn, lambda: self._retry_tip)
        self.cancel_btn = secondary_button(buttons, "Cancel Remaining", on_cancel, width=140, state="disabled")
        self.cancel_btn.grid(row=0, column=5, padx=(0, t.SM))
        Tooltip(self.cancel_btn, "Stop after the current month. A running Oracle job cannot be cancelled.")
        self.deploy_btn = primary_button(buttons, "Deploy Schedules", on_deploy, width=160, state="disabled")
        self.deploy_btn.grid(row=0, column=6)
        set_primary_enabled(self.deploy_btn, False)
        Tooltip(self.deploy_btn, lambda: "\n".join("• " + r for r in self._reasons) if self._reasons else
                "Review the deployment details before anything is changed")

    def _on_resize(self, event):
        width = max(240, event.width - 120)
        self.reason.configure(wraplength=width)
        self.progress_label.configure(wraplength=width)

    # ---- readiness (idle)
    def set_readiness(self, reasons: list[str], ready_text: str, production: bool, can_export: bool,
                      busy_text: str = "") -> None:
        """reasons: what is still missing (empty = ready). busy_text: a check is running."""
        self._reasons = reasons
        ready = not reasons and not busy_text and not self.running
        set_primary_enabled(self.deploy_btn, ready, danger=production)
        if self.running:
            pass
        elif busy_text:
            self.reason.configure(text=busy_text, text_color=t.INFO_FG)
        elif not reasons:
            self.reason.configure(text=ready_text, text_color=t.DANGER_FG if production else t.SUCCESS_FG)
        else:
            self.reason.configure(text="To enable deployment: " + reasons[0]
                                  + (f" (+{len(reasons) - 1} more)" if len(reasons) > 1 else ""),
                                  text_color=t.TEXT_MUTED)
        self.export_btn.configure(state="normal" if can_export and not self.running else "disabled")
        self.history_btn.configure(state="disabled" if self.running else "normal")

    def set_exporting(self, busy: bool) -> None:
        self.export_btn.configure(state="disabled" if busy else "normal",
                                  text="Exporting…" if busy else "Export Report…")

    def set_retry(self, count: int, tip: str = "") -> None:
        enabled = count > 0 and not self.running
        self.retry_btn.configure(state="normal" if enabled else "disabled",
                                 text=f"Retry Failed ({count})" if count else "Retry Failed",
                                 width=130 if count else 110)
        self._retry_tip = tip or ("Deploy again the schedules that failed or were not started in the last run. "
                                  "Unknown results are never retried automatically." if count else
                                  "Available after a run with failed or not-started schedules")

    def set_unknown(self, count: int) -> None:
        if count:
            self.resolve_btn.configure(text=f"Resolve Unknown ({count})…",
                                       state="disabled" if self.running else "normal")
            self.resolve_btn.grid()
        else:
            self.resolve_btn.grid_remove()

    def set_counters(self, completed: int, skipped: int, failed: int, unknown: int) -> None:
        for key, value, symbol in (("completed", completed, "✔"), ("skipped", skipped, "»"),
                                   ("failed", failed, "✖"), ("unknown", unknown, "?")):
            self.counters[key].configure(text=f"{symbol} {value}")

    # ---- running
    def set_running(self, running: bool, production: bool = False) -> None:
        self.running = running
        self.headline.configure(text="Deploying…" if running else "Deploy")
        self.cancel_btn.configure(state="normal" if running else "disabled", text="Cancel Remaining")
        for b in (self.export_btn, self.history_btn, self.retry_btn, self.resolve_btn):
            b.configure(state="disabled" if running else "normal")
        if running:
            set_primary_enabled(self.deploy_btn, False)
            self.deploy_btn.configure(text="Deploying…")
            self.progress.configure(progress_color=t.DANGER if production else t.PRIMARY)
            self.reason.configure(text="Do not close the application until the deployment has finished.",
                                  text_color=t.TEXT_MUTED)
        else:
            self.deploy_btn.configure(text="Deploy Schedules")

    def set_cancelling(self) -> None:
        self.cancel_btn.configure(state="disabled", text="Stopping…")

    def set_progress(self, fraction: float, text: str, tone: str | None = None) -> None:
        self.progress.set(max(0.0, min(1.0, fraction)))
        self.progress_label.configure(text=text, text_color=t.STATUS_TONES[tone][0] if tone else t.TEXT_MUTED)

    def set_progress_idle_colour(self, tone: str | None) -> None:
        """After a run: keep the bar visible in the outcome colour (None = hide)."""
        if tone is None:
            self.progress.configure(progress_color=t.SURFACE_ALT)
            self.progress.set(0)
        else:
            self.progress.configure(progress_color=t.STATUS_TONES[tone][0])
