"""Design tokens for the desktop UI: colours, typography, spacing, radii.

Every colour is a (light, dark) tuple, the format CustomTkinter resolves automatically when the
appearance mode changes. Plain Tk / ttk widgets get the resolved value through `resolve()`.
Text/background pairs are chosen for WCAG AA contrast (>= 4.5:1 for body text) in both modes.
"""

from __future__ import annotations

import customtkinter as ctk

# --------------------------------------------------------------------------- colours
# Surfaces
BG = ("#F3F5F8", "#13161B")              # window background
SURFACE = ("#FFFFFF", "#1B1F26")         # cards
SURFACE_ALT = ("#E9EDF2", "#20252D")     # sidebar, header, subtle fills
SURFACE_SUNKEN = ("#F6F8FA", "#171A20")  # inputs inside cards, table body
BORDER = ("#D5DBE3", "#2F3640")
BORDER_STRONG = ("#AEB7C3", "#46505C")

# Text
TEXT = ("#18212D", "#E7EAF0")
TEXT_MUTED = ("#566173", "#A0AABA")      # 6.3:1 on white, 7.4:1 on dark surface
TEXT_ON_ACCENT = ("#FFFFFF", "#FFFFFF")
TEXT_DISABLED = ("#8A93A0", "#6B7480")

# Accent / actions
PRIMARY = ("#1F5FAD", "#2F6DBA")         # white text >= 5:1 in both modes
PRIMARY_HOVER = ("#184C8B", "#285E9F")
PRIMARY_DISABLED = ("#C3CCD8", "#2C333D")
FOCUS_RING = ("#1F5FAD", "#6EA8F0")

DANGER = ("#B42318", "#C2362B")          # destructive / production buttons
DANGER_HOVER = ("#931C13", "#A32D24")

# Status (foreground on matching *_BG)
SUCCESS_FG = ("#17693A", "#5BD08F")
SUCCESS_BG = ("#E2F3E9", "#16301F")
WARNING_FG = ("#7A4F00", "#F2BE55")
WARNING_BG = ("#FFF3D6", "#352A12")
DANGER_FG = ("#A3221A", "#FF8A80")
DANGER_BG = ("#FCE6E4", "#3A1D1C")
NEUTRAL_FG = ("#4E5867", "#B3BCC8")
NEUTRAL_BG = ("#E9EDF2", "#272D36")
INFO_FG = ("#1A548F", "#8DBDF5")
INFO_BG = ("#E3EEFA", "#18283B")
UNKNOWN_FG = ("#9A3412", "#FDBA74")     # amber-red: outcome unknown, needs a check in Oracle
UNKNOWN_BG = ("#FDE4D2", "#3B2317")

# Table
ROW_SELECTED = ("#CFE0F5", "#25446A")
ROW_HOVER_TINT = ("#F0F4F9", "#222831")

STATUS_TONES = {
    # tone -> (fg, bg)
    "success": (SUCCESS_FG, SUCCESS_BG),
    "warning": (WARNING_FG, WARNING_BG),
    "danger": (DANGER_FG, DANGER_BG),
    "neutral": (NEUTRAL_FG, NEUTRAL_BG),
    "info": (INFO_FG, INFO_BG),
    "unknown": (UNKNOWN_FG, UNKNOWN_BG),
    "danger_solid": (TEXT_ON_ACCENT, DANGER),   # PRODUCTION badge: visible on any surface
}

# --------------------------------------------------------------------------- spacing & shape
XS, SM, MD, LG, XL = 4, 8, 12, 16, 24
CARD_PAD = LG
RADIUS_CARD = 10
RADIUS_CONTROL = 6
RADIUS_BADGE = 9
CONTROL_HEIGHT = 32
BUTTON_HEIGHT = 34
SIDEBAR_WIDTH = 272

# --------------------------------------------------------------------------- typography
FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

_TYPE_SCALE = {
    # name: (size px, weight)
    "title": (19, "bold"),
    "h2": (15, "bold"),
    "h3": (13, "bold"),
    "body": (13, "normal"),
    "body_bold": (13, "bold"),
    "small": (12, "normal"),
    "small_bold": (12, "bold"),
    "caption": (11, "normal"),
    "caption_bold": (11, "bold"),
    "overline": (11, "bold"),
    "mono": (12, "normal"),
}

_font_cache: dict[str, ctk.CTkFont] = {}


def font(name: str = "body") -> ctk.CTkFont:
    """Shared CTkFont for a type-scale step (created lazily: a Tk root must exist)."""
    if name not in _font_cache:
        size, weight = _TYPE_SCALE[name]
        family = MONO_FAMILY if name == "mono" else FONT_FAMILY
        _font_cache[name] = ctk.CTkFont(family=family, size=size, weight=weight)
    return _font_cache[name]


def tk_font(name: str = "body") -> tuple:
    """Font tuple for plain Tk / ttk widgets (point sizes, scaled by Tk)."""
    size, weight = _TYPE_SCALE[name]
    points = max(8, round(size * 0.75))
    family = MONO_FAMILY if name == "mono" else FONT_FAMILY
    return (family, points, weight) if weight == "bold" else (family, points)


def is_dark() -> bool:
    return ctk.get_appearance_mode() == "Dark"


def resolve(color: tuple[str, str] | str) -> str:
    """Pick the light or dark value for the current appearance mode."""
    if isinstance(color, (tuple, list)):
        return color[1] if is_dark() else color[0]
    return color
