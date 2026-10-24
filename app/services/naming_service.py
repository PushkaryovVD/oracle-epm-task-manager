"""Schedule name generation. Month names are fixed English strings, independent of the Windows locale."""

import re

from app.models import MonthRef
from app.utils.config import naming_rules

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
MONTH_ABBREVIATIONS = tuple(name[:3] for name in MONTH_NAMES)

PLACEHOLDERS = {
    "MMMM": "Full month name (January)",
    "MMM": "Short month name (Jan)",
    "MM": "Two-digit month (01)",
    "YYYY": "Four-digit year (2027)",
    "YY": "Two-digit year (27)",
    "TemplateName": "Oracle template name",
}

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


def unknown_placeholders(pattern: str) -> list[str]:
    return [name for name in _PLACEHOLDER_RE.findall(pattern) if name not in PLACEHOLDERS]


def render(pattern: str, month: MonthRef, template_name: str = "") -> str:
    """Render a schedule name. Raises ValueError on unknown placeholders."""
    unknown = unknown_placeholders(pattern)
    if unknown:
        raise ValueError("Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in unknown))
    values = {
        "MMMM": MONTH_NAMES[month.month - 1],
        "MMM": MONTH_ABBREVIATIONS[month.month - 1],
        "MM": f"{month.month:02d}",
        "YYYY": f"{month.year:04d}",
        "YY": f"{month.year % 100:02d}",
        "TemplateName": template_name.strip(),
    }
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], pattern).strip()


def validate_name(name: str, rules: dict | None = None, include_provisional: bool = True) -> list[str]:
    """Return user-facing problems with a generated schedule name (empty list = valid).

    Length and character limits come from naming_rules.json and are provisional until confirmed on a
    test pod; include_provisional=False returns only the problems that are always invalid.
    """
    rules = rules or naming_rules()
    problems = []
    if not name.strip():
        problems.append("Schedule name is empty.")
        return problems
    if include_provisional:
        problems.extend(provisional_name_problems(name, rules))
    if any(ord(ch) < 32 for ch in name):
        problems.append("Schedule name contains control characters.")
    return problems


def provisional_name_problems(name: str, rules: dict | None = None) -> list[str]:
    rules = rules or naming_rules()
    problems = []
    if len(name) > rules["max_length"]:
        problems.append(f"Schedule name is longer than {rules['max_length']} characters.")
    bad = sorted({ch for ch in name if ch in rules["forbidden_chars"]})
    if bad:
        problems.append("Schedule name contains forbidden characters: " + " ".join(bad))
    return problems
