"""Help: opens the bundled HTML user guide (docs/user-guide/USER_GUIDE.html) in the default browser."""

from __future__ import annotations

import logging
import os
import webbrowser
from pathlib import Path

from app.ui.dialogs import show_message
from app.utils.paths import user_guide_path

log = logging.getLogger(__name__)

MISSING_TEXT = ("The user guide was not found on this computer.\n\nExpected location:\n{path}\n\n"
                "Ask the person who installed the application for the file USER_GUIDE.html, or for a "
                "complete copy of the application folder.")


def open_user_guide(parent, path: Path | None = None) -> bool:
    """Open the user guide; show a friendly message when it is missing or cannot be opened. True = opened."""
    guide = path or user_guide_path()
    if not guide.is_file():
        log.warning("User guide not found: %s", guide)
        show_message(parent, "User guide not found", MISSING_TEXT.format(path=guide), "warning")
        return False
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(guide))  # noqa: S606 - opens a local HTML file with the default browser
        elif not webbrowser.open(guide.as_uri()):
            raise OSError("no web browser is available")
    except OSError as exc:
        log.warning("User guide could not be opened: %s", exc)
        try:
            if webbrowser.open(guide.as_uri()):
                return True
        except Exception:  # pragma: no cover - last resort
            pass
        show_message(parent, "User guide could not be opened",
                     f"The user guide could not be opened automatically ({exc}).\n\nOpen this file in your web "
                     f"browser:\n{guide}", "warning")
        return False
    log.info("User guide opened")
    return True
