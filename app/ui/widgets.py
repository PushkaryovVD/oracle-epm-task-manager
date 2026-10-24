"""Small reusable UI building blocks: cards, badges, buttons, tooltips and a background-task runner."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from typing import Any, Callable

import customtkinter as ctk

from app.ui import theme as t

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- buttons
def primary_button(master, text: str, command=None, width: int = 140, danger: bool = False, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=t.BUTTON_HEIGHT,
        corner_radius=t.RADIUS_CONTROL, font=t.font("body_bold"),
        fg_color=t.DANGER if danger else t.PRIMARY,
        hover_color=t.DANGER_HOVER if danger else t.PRIMARY_HOVER,
        text_color=t.TEXT_ON_ACCENT, text_color_disabled=t.TEXT_DISABLED, **kw,
    )


def secondary_button(master, text: str, command=None, width: int = 120, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, command=command, width=width, height=t.BUTTON_HEIGHT,
        corner_radius=t.RADIUS_CONTROL, font=t.font("body"), border_width=1,
        fg_color=t.SURFACE, hover_color=t.SURFACE_ALT, border_color=t.BORDER_STRONG,
        text_color=t.TEXT, text_color_disabled=t.TEXT_DISABLED, **kw,
    )


def link_button(master, text: str, command=None, **kw) -> ctk.CTkButton:
    """Low-emphasis text button (e.g. 'Details', 'Placeholders')."""
    return ctk.CTkButton(
        master, text=text, command=command, width=10, height=26, corner_radius=t.RADIUS_CONTROL,
        font=t.font("small_bold"), fg_color="transparent", hover_color=t.SURFACE_ALT,
        text_color=t.INFO_FG, text_color_disabled=t.TEXT_DISABLED, **kw,
    )


def set_enabled(widget, enabled: bool) -> None:
    widget.configure(state="normal" if enabled else "disabled")


def set_primary_enabled(button: ctk.CTkButton, enabled: bool, danger: bool = False) -> None:
    """Primary buttons look clearly inactive when disabled (muted fill, not the accent colour)."""
    if enabled:
        button.configure(state="normal", fg_color=t.DANGER if danger else t.PRIMARY,
                         hover_color=t.DANGER_HOVER if danger else t.PRIMARY_HOVER)
    else:
        button.configure(state="disabled", fg_color=t.PRIMARY_DISABLED)


# --------------------------------------------------------------------------- layout
class Card(ctk.CTkFrame):
    """A titled section with a step number, e.g. '1  Excel template'."""

    def __init__(self, master, title: str, step: str | None = None, **kw):
        super().__init__(master, fg_color=t.SURFACE, corner_radius=t.RADIUS_CARD,
                         border_width=1, border_color=t.BORDER, **kw)
        self.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=t.CARD_PAD, pady=(t.MD, t.SM))
        header.grid_columnconfigure(2, weight=1)
        col = 0
        if step:
            ctk.CTkLabel(header, text=step, width=24, height=24, corner_radius=12,
                         fg_color=t.INFO_BG, text_color=t.INFO_FG,
                         font=t.font("small_bold")).grid(row=0, column=col, padx=(0, t.SM))
            col += 1
        ctk.CTkLabel(header, text=title, font=t.font("h2"), text_color=t.TEXT,
                     anchor="w").grid(row=0, column=col, sticky="w")
        # Right-aligned slot for header actions
        self.header_actions = ctk.CTkFrame(header, fg_color="transparent", width=1, height=1)
        self.header_actions.grid(row=0, column=3, sticky="e")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=t.CARD_PAD, pady=(0, t.MD))
        self.grid_rowconfigure(1, weight=1)


def field_label(master, text: str, **kw) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=t.font("small_bold"), text_color=t.TEXT_MUTED,
                        anchor="w", height=20, **kw)


def hint_label(master, text: str = "", tone: str | None = None, wraplength: int = 0, **kw) -> ctk.CTkLabel:
    color = t.STATUS_TONES[tone][0] if tone else t.TEXT_MUTED
    return ctk.CTkLabel(master, text=text, font=t.font("caption"), text_color=color, anchor="w",
                        justify="left", height=18, wraplength=wraplength, **kw)


def entry(master, width: int = 200, **kw) -> ctk.CTkEntry:
    return ctk.CTkEntry(master, width=width, height=t.CONTROL_HEIGHT, corner_radius=t.RADIUS_CONTROL,
                        border_width=1, border_color=t.BORDER_STRONG, fg_color=t.SURFACE_SUNKEN,
                        text_color=t.TEXT, font=t.font("body"), **kw)


def option_menu(master, values: list[str], width: int = 160, command=None, **kw) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        master, values=values or [""], width=width, height=t.CONTROL_HEIGHT, corner_radius=t.RADIUS_CONTROL,
        fg_color=t.SURFACE_SUNKEN, button_color=t.SURFACE_ALT, button_hover_color=t.BORDER,
        text_color=t.TEXT, text_color_disabled=t.TEXT_DISABLED, dropdown_fg_color=t.SURFACE,
        dropdown_hover_color=t.SURFACE_ALT, dropdown_text_color=t.TEXT, font=t.font("body"),
        dropdown_font=t.font("body"), dynamic_resizing=False, command=command, **kw,
    )


def checkbox(master, text: str, variable=None, command=None, **kw) -> ctk.CTkCheckBox:
    options = dict(font=t.font("body"), text_color=t.TEXT, fg_color=t.PRIMARY, hover_color=t.PRIMARY_HOVER,
                   border_color=t.BORDER_STRONG, checkbox_width=20, checkbox_height=20, border_width=2,
                   corner_radius=4)
    options.update(kw)
    return ctk.CTkCheckBox(master, text=text, variable=variable, command=command, **options)


class Badge(ctk.CTkLabel):
    """Pill label: tone in success | warning | danger | neutral | info."""

    def __init__(self, master, text: str = "", tone: str = "neutral", **kw):
        super().__init__(master, text=text, height=20, corner_radius=t.RADIUS_BADGE,
                         font=t.font("caption_bold"), padx=8, **kw)
        self.set(text, tone)

    def set(self, text: str, tone: str = "neutral") -> None:
        fg, bg = t.STATUS_TONES[tone]
        self.configure(text=text, text_color=fg, fg_color=bg)


# --------------------------------------------------------------------------- tooltip
class Tooltip:
    """Hover tooltip for any widget (works on disabled CTk buttons too). Text may be a callable."""

    DELAY_MS = 450

    def __init__(self, widget, text: str | Callable[[], str]):
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self._after: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        text = self.text() if callable(self.text) else self.text
        if not text or self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.attributes("-topmost", True)
        frame = tk.Frame(self._tip, bg=t.resolve(t.BORDER_STRONG), bd=0)
        frame.pack()
        tk.Label(frame, text=text, justify="left", wraplength=360, bg=t.resolve(t.SURFACE),
                 fg=t.resolve(t.TEXT), font=t.tk_font("small"), padx=8, pady=5).pack(padx=1, pady=1)
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# --------------------------------------------------------------------------- background work
class BackgroundTask:
    """Run `func` in a worker thread; deliver the result on the Tk thread via queue + after(100).

    Tk widgets are touched only from the main thread (in on_done / on_error).
    """

    POLL_MS = 100

    def __init__(self, widget, func: Callable[[], Any], on_done: Callable[[Any], None],
                 on_error: Callable[[BaseException], None]):
        self._widget = widget
        self._func = func
        self._on_done = on_done
        self._on_error = on_error
        self._queue: queue.Queue = queue.Queue()
        self.cancelled = False

    def start(self) -> "BackgroundTask":
        threading.Thread(target=self._run, daemon=True).start()
        self._widget.after(self.POLL_MS, self._poll)
        return self

    def cancel(self) -> None:
        """Ignore the result when it arrives (the thread itself cannot be interrupted)."""
        self.cancelled = True

    def _run(self):
        try:
            self._queue.put(("ok", self._func()))
        except BaseException as exc:  # reported to the UI, never swallowed
            log.exception("Background task failed")
            self._queue.put(("error", exc))

    def _poll(self):
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            try:
                self._widget.after(self.POLL_MS, self._poll)
            except tk.TclError:
                pass  # widget destroyed
            return
        if self.cancelled:
            return
        (self._on_done if kind == "ok" else self._on_error)(payload)
