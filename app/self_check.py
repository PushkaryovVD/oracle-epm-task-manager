"""Non-GUI self-check: `EPMTaskDeployer.exe --self-check` (or `python -m app.main --self-check`).

Opens no window and never reads or writes credentials. Results go to logs/self_check.txt
(%APPDATA%\\EPMTaskDeployer\\logs, or EPMTD_HOME\\logs) and to stdout when a console exists.
Exit code: 0 when all required checks pass (warnings allowed), 1 otherwise.
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path

REPORT_NAME = "self_check.txt"
WINDOWS_KEYRING_CLASS = "WinVaultKeyring"


class _Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures = 0
        self.warnings = 0

    def ok(self, msg: str) -> None:
        self.lines.append(f"[OK]   {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        self.lines.append(f"[WARN] {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        self.lines.append(f"[FAIL] {msg}")

    def info(self, msg: str) -> None:
        self.lines.append(f"[INFO] {msg}")


def _run(report: _Report, name: str, check) -> None:
    try:
        check(report)
    except Exception as exc:  # a broken check must be reported, not crash the self-check
        report.fail(f"{name}: {exc.__class__.__name__}: {exc}")


def _check_environment(report: _Report) -> None:
    from app import __file__ as app_init
    from app.utils.paths import app_data_dir, bundle_root

    frozen = bool(getattr(sys, "frozen", False))
    report.info(f"Mode: {'frozen executable' if frozen else 'source'}; Python {platform.python_version()}; "
                f"{platform.system()} {platform.release()}")
    report.info(f"Bundle root: {bundle_root()}")
    report.info(f"Data folder: {app_data_dir()}")
    report.info(f"Package: {Path(app_init).parent}")


def _check_config(report: _Report) -> None:
    from app.services.period_service import load_period_mapping
    from app.utils.config import column_catalogue, load_json, naming_rules, period_mapping_files, service_types
    from app.utils.paths import bundled_config_dir

    report.info(f"Config folder: {bundled_config_dir()}")

    rules = naming_rules()
    report.ok(f"naming_rules.json loaded ({len(rules)} top-level keys)")

    columns = column_catalogue()
    report.ok(f"tm_columns.json loaded ({len(columns)} top-level keys)")

    types = service_types()
    if not types:
        report.fail("service_types.json contains no service types")
    else:
        report.ok(f"service_types.json loaded ({', '.join(t['id'] for t in types)})")

    files = period_mapping_files()
    if not files:
        report.fail("period_mappings/ contains no mapping files")
    for path in files:
        mapping = load_period_mapping(path)
        report.ok(f"period mapping '{mapping.id}' loaded ({path.name})")

    messages = load_json("oracle_messages.json")
    missing = [k for k in ("schedule_exists_patterns", "member_not_found_patterns", "template_not_found_patterns")
               if not messages.get(k)]
    if missing:
        report.fail(f"oracle_messages.json lacks keys: {', '.join(missing)}")
    else:
        report.ok("oracle_messages.json loaded (pattern lists present)")


def _check_keyring(report: _Report) -> None:
    import keyring

    backend = keyring.get_keyring()
    name = f"{type(backend).__module__}.{type(backend).__name__}"
    if type(backend).__name__ != WINDOWS_KEYRING_CLASS:
        report.fail(f"keyring backend is {name}, expected the Windows Credential Manager backend "
                    f"({WINDOWS_KEYRING_CLASS}). Passwords cannot be stored safely.")
        return
    report.ok(f"keyring backend: {name} (Windows Credential Manager; no credential written)")


def _check_customtkinter(report: _Report) -> None:
    import tkinter

    import customtkinter

    base = Path(customtkinter.__file__).resolve().parent
    themes = base / "assets" / "themes"
    fonts = base / "assets" / "fonts"
    report.ok(f"customtkinter {customtkinter.__version__} imported from {base}")
    report.ok(f"tkinter {tkinter.TkVersion} imported")
    if not (themes / "blue.json").is_file():
        report.fail(f"customtkinter theme files not found in {themes}")
    else:
        report.ok(f"customtkinter themes found ({len(list(themes.glob('*.json')))} files)")
    if not fonts.is_dir():
        report.fail(f"customtkinter fonts folder not found: {fonts}")
    else:
        report.ok("customtkinter fonts folder found")


def _check_bundled_files(report: _Report) -> None:
    from app.utils.paths import app_icon_path, user_guide_path

    guide = user_guide_path()
    if guide.is_file():
        report.ok(f"user guide found: {guide}")
    else:
        report.warn(f"user guide not found ({guide}); Help links will not open a guide")
    icon = app_icon_path()
    if icon.is_file():
        report.ok("application icon found")
    else:
        report.warn(f"application icon not found ({icon})")


def _check_logs(report: _Report) -> None:
    from app.utils.paths import logs_dir

    probe = logs_dir() / ".write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    report.ok(f"logs folder writable: {logs_dir()}")


def run_self_check() -> int:
    report = _Report()
    report.lines.append(f"EPM Task Deployer self-check, {datetime.now():%Y-%m-%d %H:%M:%S}")
    checks = [
        ("environment", _check_environment),
        ("configuration", _check_config),
        ("keyring", _check_keyring),
        ("customtkinter", _check_customtkinter),
        ("bundled files", _check_bundled_files),
        ("logs folder", _check_logs),
    ]
    for name, check in checks:
        _run(report, name, check)

    verdict = "PASSED" if report.failures == 0 else "FAILED"
    report.lines.append(f"Result: {verdict} ({report.failures} failure(s), {report.warnings} warning(s))")
    text = "\n".join(report.lines) + "\n"

    from app.utils.paths import logs_dir

    target = logs_dir() / REPORT_NAME
    target.write_text(text, encoding="utf-8")
    if sys.stdout is not None:  # None in a windowed (no-console) executable
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
    return 0 if report.failures == 0 else 1
