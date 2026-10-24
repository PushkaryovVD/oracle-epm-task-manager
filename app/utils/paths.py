"""File-system locations used by the application."""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "EPMTaskDeployer"


def app_data_dir() -> Path:
    """Per-user writable folder (%APPDATA%\\EPMTaskDeployer). EPMTD_HOME overrides it (tests)."""
    override = os.environ.get("EPMTD_HOME")
    if override:
        base = Path(override)
    else:
        base = Path(os.environ.get("APPDATA", Path.home())) / APP_DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def bundle_root() -> Path:
    """Project root when run from source; the PyInstaller bundle folder when frozen."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def bundled_config_dir() -> Path:
    """Read-only config shipped with the app (works from source and from a PyInstaller build)."""
    return bundle_root() / "config"


def user_guide_path() -> Path:
    """Self-contained HTML user guide (built by tools/build_user_guide.py, bundled by build.ps1)."""
    return bundle_root() / "docs" / "user-guide" / "USER_GUIDE.html"


def app_icon_path() -> Path:
    """Window / executable icon (assets/app.ico, bundled by the PyInstaller spec)."""
    return bundle_root() / "assets" / "app.ico"


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
