"""Regenerate the screenshots of the user guide (docs/user-guide/images/*.png).

    .venv\\Scripts\\python.exe tools\\make_doc_screenshots.py

Development-only, Windows-only. The script starts the real MainWindow against the FAKE Oracle
(tools/fake_oracle.py), with an in-memory password store and a temporary EPMTD_HOME that is deleted at the end:
nothing reaches %APPDATA%\\EPMTaskDeployer, Windows Credential Manager or a real Oracle environment.
Only invented example data is used (environment "FCCS – Test", user finance.user@example.com, the anonymised
fixture tests/fixtures/NA_Monthly_Close_sample.csv presented as "NA Monthly Close.csv").

The scenario clicks through the app by itself (do not use the mouse or keyboard while it runs, ~1-2 minutes;
the window stays on top). Screenshots are taken from the screen with the Windows GDI API and written as PNG with
the standard library only (no Pillow needed). The window is sized 1320x860; dialogs are cropped to the dialog.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import traceback
import zlib
from ctypes import wintypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "user-guide" / "images"
FIXTURE = ROOT / "tests" / "fixtures" / "NA_Monthly_Close_sample.csv"
WINDOW_W, WINDOW_H = 1320, 860

# Invented example data only
TEST_ENV = dict(name="FCCS – Test", url="https://fccs-test-example.epm.us-ashburn-1.ocs.oraclecloud.com/epmcloud")
PROD_ENV = dict(name="FCCS – Production", url="https://fccs-example.epm.us-ashburn-1.ocs.oraclecloud.com")
USER = "finance.user@example.com"
PATTERN = "NA and WW {MMMM} {YYYY} Close"
FAKE_PASSWORD = "fake-password-for-screenshots"  # accepted by the fake Oracle only
DISPLAY_APPDATA = Path(r"C:\Users\finance.user\AppData\Roaming\EPMTaskDeployer")  # shown, never written
DISPLAY_DOCUMENTS = Path(r"C:\Users\finance.user\Documents")
UNIQUE_NAME_TEXT = "You must specify a unique schedule name."  # Oracle's real answer (DESIGN §10a, Q1)


# =========================================================================== screen capture (GDI) + PNG
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _window_rect(toplevel) -> tuple[int, int, int, int]:
    """Visible bounds of a Tk toplevel including the title bar (without the invisible resize border)."""
    hwnd = int(toplevel.wm_frame(), 16)
    rect = _RECT()
    dwm_extended_frame_bounds = 9
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), dwm_extended_frame_bounds,
                                                  ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _widget_rect(widget, pad: int = 0) -> tuple[int, int, int, int]:
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    return x - pad, y - pad, x + widget.winfo_width() + pad, y + widget.winfo_height() + pad


def _grab(left: int, top: int, right: int, bottom: int) -> tuple[int, int, bytes]:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    width, height = right - left, bottom - top
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(mem, bmp)
    try:
        srccopy, captureblt = 0x00CC0020, 0x40000000
        gdi32.BitBlt(mem, 0, 0, width, height, screen, left, top, srccopy | captureblt)
        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth, header.biHeight = width, -height  # top-down rows
        header.biPlanes, header.biBitCount, header.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(header), 0)
        return width, height, buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def _write_png(path: Path, width: int, height: int, bgra: bytes) -> None:
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(png)


# =========================================================================== fake Oracle with real texts
def make_fake():
    from tools.fake_oracle import FakeOracle

    class DocFakeOracle(FakeOracle):
        """FakeOracle answering with the texts observed on the customer's test pod (DESIGN §10a):
        HTTP 400 for a missing member and "You must specify a unique schedule name." for a duplicate.
        Behaviour "stuck": the job is accepted, then every status request fails (-> Unknown with a job id)."""

        def transport(self, method, url, headers, body, timeout):
            path = unquote(urlsplit(url).path)
            if self._authorized(headers):
                if method == "GET" and "/dimensions/" in path and "/members/" in path:
                    member = path.rsplit("/members/", 1)[1]
                    if member in self.missing_members:
                        time.sleep(self.latency)
                        return self._json(400, {"detail": f"The member {member} does not exist for the specified "
                                                          "cube or you do not have access to it."})
                if method == "POST" and path.endswith("/cmapi/v1/jobs"):
                    schedule = json.loads(body or b"{}").get("parameters", {}).get("scheduleName", "")
                    if self._behaviour(schedule) == "exists":
                        time.sleep(self.latency)
                        return self._json(400, {"detail": UNIQUE_NAME_TEXT})
                if method == "GET" and "/cmapi/v1/jobs/" in path:
                    job = self._jobs.get(int(path.rsplit("/", 1)[1]))
                    if job is not None and job.behaviour == "stuck":
                        time.sleep(self.latency)
                        return self._json(504, {"detail": "Gateway Timeout"})
            return super().transport(method, url, headers, body, timeout)

    return DocFakeOracle(
        password=FAKE_PASSWORD, applications=["FCCS1"], missing_members={"FY28"},
        behaviours={"February": "exists", "March": "slow", "April": "fail", "May": "stuck"},
        slow_polls=6, slow_delay=0.7, latency=0.08,
    )


# =========================================================================== scenario runner
class Wait:
    def __init__(self, predicate, timeout: float = 40.0, settle: float = 0.6, what: str = ""):
        self.predicate, self.timeout, self.settle, self.what = predicate, timeout, settle, what


def until(predicate, what: str, timeout: float = 40.0, settle: float = 0.6) -> Wait:
    return Wait(predicate, timeout, settle, what)


def pause(seconds: float) -> Wait:
    end = time.monotonic() + seconds
    return Wait(lambda: time.monotonic() >= end, seconds + 5, 0.0, f"pause {seconds}s")


class Director:
    """Drives the window through a generator of steps; every blocking UI call is started with after()."""

    def __init__(self, app, fake, home: Path):
        self.app, self.fake, self.home = app, fake, home
        self.dialogs: list = []
        self.saved: list[str] = []
        self.failed: BaseException | None = None
        self._gen = self.scenario()

    # ---- plumbing
    def start(self):
        self.app.after(900, self._next)

    def _next(self):
        try:
            wait = next(self._gen)
        except StopIteration:
            self.app.after(300, self.app.destroy)
            return
        except BaseException as exc:  # noqa: BLE001 - reported below
            self.abort(exc)
            return
        self._poll(wait, time.monotonic() + wait.timeout)

    def _poll(self, wait: Wait, deadline: float):
        try:
            ok = bool(wait.predicate())
        except Exception:  # widgets may not exist yet
            ok = False
        if ok:
            self.app.after(int(wait.settle * 1000), self._next)
        elif time.monotonic() > deadline:
            self.abort(TimeoutError(f"Timed out waiting for: {wait.what}"))
        else:
            self.app.after(100, self._poll, wait, deadline)

    def abort(self, exc: BaseException):
        self.failed = exc
        traceback.print_exception(exc)
        _remove_home(self.home)
        sys.stdout.flush()
        os._exit(1)  # dialogs may be nested in wait_window(): leave hard, the temp home is already removed

    def later(self, func, *args):
        self.app.after(20, lambda: func(*args))

    def dialog(self, cls):
        for dlg in reversed(self.dialogs):
            try:
                if isinstance(dlg, cls) and dlg.winfo_exists() and dlg.winfo_viewable():
                    return dlg
            except Exception:
                continue
        return None

    def wait_dialog(self, cls, settle: float = 0.9) -> Wait:
        return until(lambda: self.dialog(cls) is not None, f"dialog {cls.__name__}", settle=settle)

    def gone(self, dlg) -> Wait:
        return until(lambda: not dlg.winfo_exists(), "dialog closed", settle=0.4)

    # ---- capture
    def shot(self, name: str, toplevel=None, rect=None, above=()):
        target = toplevel or self.app
        target.lift()
        for window in above:  # e.g. a tooltip that must stay in front of the window
            window.lift()
        target.update_idletasks()
        target.update()
        box = rect or _window_rect(target)
        width, height, data = _grab(*box)
        _write_png(OUT_DIR / f"{name}.png", width, height, data)
        self.saved.append(f"{name}.png ({width}x{height})")
        print(f"  saved {name}.png  {width}x{height}")

    # ---- the scenario
    def scenario(self):  # noqa: C901 - one linear story, easier to read in one place
        from app.ui.deployment_dialogs import (
            CompletionSummaryDialog, DeploymentHistoryDialog, ResolveUnknownDialog, RetryConfirmDialog,
        )
        from app.ui.dialogs import ConfirmDeploymentDialog, IssuesDialog
        from app.ui.environment_view import EnvironmentDialog, ManageEnvironmentsDialog, PasswordDialog
        from app.models import Environment, ScheduleStatus

        app = self.app

        # 01 first start: Add Oracle Environment (filled in, password masked)
        yield self.wait_dialog(EnvironmentDialog, settle=1.2)
        dlg = self.dialog(EnvironmentDialog)
        dlg.name.insert(0, TEST_ENV["name"])
        dlg.url.insert(0, TEST_ENV["url"])
        dlg._validate_url()
        dlg.username.insert(0, USER)
        dlg.password.entry.insert(0, FAKE_PASSWORD)
        app.focus_force()
        dlg.focus_force()
        dlg.password.entry.focus_set()
        yield pause(0.6)
        self.shot("01_add_environment", dlg)

        # 02 Test Connection
        dlg._test_connection()
        yield until(lambda: dlg._test_task is None and dlg._tested_ok, "test connection")
        self.shot("02_test_connection", dlg)
        dlg._save()
        yield self.gone(dlg)

        # A second, production environment (added directly: same dialog as above)
        prod = Environment(id="fccs-production", display_name=PROD_ENV["name"], service_type="FCCS",
                           base_url=PROD_ENV["url"], username=USER, is_production=True)
        app.env_store.save(prod)
        app.cred_store.save(prod.id, prod.username, FAKE_PASSWORD)
        test_id = app.sidebar.current.id
        app.sidebar.reload(select_id=test_id)
        yield pause(0.8)

        # 03 Manage environments
        self.later(app.sidebar.manage)
        yield self.wait_dialog(ManageEnvironmentsDialog)
        dlg = self.dialog(ManageEnvironmentsDialog)
        self.shot("03_manage_environments", dlg)
        dlg.cancel()
        yield self.gone(dlg)

        # 04 main window before a template is selected
        yield pause(0.6)
        self.shot("04_main_window_empty")

        # 05 template selected and validated
        self.later(app.template.browse)
        yield until(lambda: app.template.inspection is not None and not app.template.validating, "validation",
                    settle=1.0)
        self.shot("05_template_validated", rect=_widget_rect(app.template, pad=8))

        # 06 validation details
        self.later(app.template.show_details)
        yield self.wait_dialog(IssuesDialog)
        dlg = self.dialog(IssuesDialog)
        self.shot("06_validation_details", dlg)
        dlg.cancel()
        yield self.gone(dlg)

        # 07 schedule settings + placeholder help (tooltip of "{ } Insert placeholder")
        sched = app.schedule
        sched.from_month.set("January")
        sched.from_year.set("2027")
        sched.to_month.set("June")
        sched.to_year.set("2027")
        sched.pattern.delete(0, "end")
        sched.pattern.insert(0, PATTERN)
        app.request_replan()
        yield pause(0.8)
        canvas = getattr(sched.help_btn, "_canvas", sched.help_btn)
        canvas.event_generate("<Enter>")
        yield until(lambda: _tooltip_of(sched.help_btn) is not None, "placeholder tooltip", settle=0.5)
        tip = _tooltip_of(sched.help_btn)
        # Keep the tooltip inside the window: right-aligned under the button
        btn = sched.help_btn
        tip.wm_geometry(f"+{btn.winfo_rootx() + btn.winfo_width() - tip.winfo_width()}"
                        f"+{btn.winfo_rooty() + btn.winfo_height() + 4}")
        yield pause(0.4)
        card = _widget_rect(sched, pad=8)
        tip_box = _widget_rect(tip, pad=8)
        self.shot("07_schedule_settings", rect=(card[0], card[1], max(card[2], tip_box[2]),
                                                 max(card[3], tip_box[3])), above=[tip])
        canvas.event_generate("<Leave>")
        yield pause(0.4)

        # 08 preview before the Oracle check
        app.preview.tree.selection_set("1")
        yield pause(0.6)
        self.shot("08_preview_not_checked")

        # 09 after Check in Oracle
        self.later(app.check_in_oracle)
        yield until(lambda: app._check_task is None and app.oracle_check is not None, "check in Oracle",
                    settle=0.8)
        app.preview.tree.selection_set("1")
        yield pause(0.5)
        self.shot("09_checked_ready")

        # 10 a blocked row: FY28 does not exist in the fake application
        sched.from_month.set("November")
        sched.to_month.set("February")
        sched.to_year.set("2028")
        app.replan()
        self.later(app.check_in_oracle)
        yield until(lambda: app._check_task is None and app.oracle_check is not None
                    and app.result.count(ScheduleStatus.BLOCKED) > 0, "blocked rows", settle=0.8)
        blocked = next(r for r in app.result.rows if r.status == ScheduleStatus.BLOCKED)
        app.preview.tree.selection_set(str(blocked.index))
        yield pause(0.6)
        self.shot("10_blocked_row", rect=_widget_rect(app.preview, pad=8))

        # back to January–June 2027
        sched.from_month.set("January")
        sched.to_month.set("June")
        sched.to_year.set("2027")
        app.replan()
        yield pause(0.5)

        # 11 confirmation (test environment); Deploy runs the Oracle check first
        self.later(app.deploy)
        yield self.wait_dialog(ConfirmDeploymentDialog)
        dlg = self.dialog(ConfirmDeploymentDialog)
        self.shot("11_confirm_test", dlg)
        dlg.cancel()
        yield self.gone(dlg)

        # 12 confirmation for a production environment, with the tick
        app.sidebar._on_pick(PROD_ENV["name"])
        yield pause(0.8)
        self.later(app.deploy)
        yield self.wait_dialog(ConfirmDeploymentDialog)
        dlg = self.dialog(ConfirmDeploymentDialog)
        dlg._ack.set(True)
        dlg._sync()
        yield pause(0.4)
        self.shot("12_confirm_production", dlg)
        dlg.cancel()
        yield self.gone(dlg)
        app.sidebar._on_pick(TEST_ENV["name"])
        yield pause(0.8)

        # 13 deployment in progress (March is a slow job in the fake)
        self.later(app.deploy)
        yield self.wait_dialog(ConfirmDeploymentDialog)
        dlg = self.dialog(ConfirmDeploymentDialog)
        dlg.close(True)
        yield until(lambda: app.run is not None and any(
            "March" in r.name and r.status == ScheduleStatus.DEPLOYING for r in app.run.all_rows),
            "March deploying", settle=1.6)
        self.shot("13_deploying")

        # 14 completion summary (after Export Report from the summary)
        yield self.wait_dialog(CompletionSummaryDialog, settle=1.0)
        dlg = self.dialog(CompletionSummaryDialog)
        self.later(dlg._export)
        yield until(lambda: dlg.report_label.cget("text").endswith(".xlsx"), "report exported", settle=0.6)
        self.shot("14_completion_summary", dlg)
        dlg.close("view")
        yield self.gone(dlg)

        # 15 results in the table: select the "Skipped - Already Exists" row
        app.preview.focus_results()
        skipped = next(r for r in app.result.rows if r.status == ScheduleStatus.SKIPPED_EXISTS)
        app.preview.tree.selection_set(str(skipped.index))
        app.preview.tree.see(str(skipped.index))
        yield pause(0.8)
        self.shot("15_results")

        # 16 Retry Failed confirmation
        self.later(app.retry_failed)
        yield self.wait_dialog(RetryConfirmDialog)
        dlg = self.dialog(RetryConfirmDialog)
        self.shot("16_retry_failed", dlg)
        dlg.cancel()
        yield self.gone(dlg)

        # 17 Resolve Unknown (May: job accepted, status could not be read)
        self.later(app.resolve_unknown)
        yield self.wait_dialog(ResolveUnknownDialog)
        dlg = self.dialog(ResolveUnknownDialog)
        self.shot("17_resolve_unknown", dlg)
        dlg.cancel()
        yield self.gone(dlg)

        # 18 Deployment History
        self.later(app.show_history)
        yield self.wait_dialog(DeploymentHistoryDialog)
        dlg = self.dialog(DeploymentHistoryDialog)
        self.shot("18_deployment_history", dlg)
        dlg.cancel()
        yield self.gone(dlg)

        # 20 Update Password
        self.later(app.sidebar.update_password)
        yield self.wait_dialog(PasswordDialog)
        dlg = self.dialog(PasswordDialog)
        dlg.password.entry.insert(0, FAKE_PASSWORD)
        yield pause(0.3)
        self.shot("20_update_password", dlg)
        dlg.password.clear()
        dlg.cancel()
        yield self.gone(dlg)

        # 21 dark theme overview
        app.mode_switch.set("Dark")
        app._set_mode("Dark")
        app.preview.tree.selection_set("1")
        app.preview.tree.see("1")
        yield pause(1.5)
        self.shot("21_dark_theme")


def _tooltip_of(widget):
    """The Tooltip toplevel created for a CTk widget (child of the widget or of its inner canvas/label)."""
    import tkinter as tk

    stack = [widget]
    while stack:
        w = stack.pop()
        for child in w.winfo_children():
            if isinstance(child, tk.Toplevel) and child.winfo_viewable():
                return child
            stack.append(child)
    return None


# =========================================================================== main
def main() -> int:
    if sys.platform != "win32":
        print("This script captures the screen with the Windows API and runs on Windows only.")
        return 2
    home = Path(tempfile.mkdtemp(prefix="epmtd-doc-shots-"))
    os.environ["EPMTD_HOME"] = str(home)  # before any app import: environments, logs, ledger go here
    try:
        return _run(home)
    finally:
        _remove_home(home)


def _remove_home(home: Path) -> None:
    """Close the log file (Windows keeps open files locked), then delete the temporary home."""
    import logging

    logging.shutdown()
    for handler in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(handler)
    shutil.rmtree(home, ignore_errors=True)
    print(f"Removed temporary home {home}" if not home.exists() else f"WARNING: could not remove {home}")


def _run(home: Path) -> int:
    import customtkinter as ctk
    import keyring
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.data: dict[tuple[str, str], str] = {}

        def set_password(self, service, username, password):
            self.data[(service, username)] = password

        def get_password(self, service, username):
            return self.data.get((service, username))

        def delete_password(self, service, username):
            if self.data.pop((service, username), None) is None:
                raise PasswordDeleteError(service)

    memory = MemoryKeyring()
    keyring.set_keyring(memory)  # belt and braces: even a default CredentialStore() stays in memory

    from app.services.credential_store import CredentialStore
    from app.ui import dialogs as dialogs_module
    from app.ui import main_window as main_window_module
    from app.ui.main_window import MainWindow
    from app.utils.logging_setup import configure_logging

    configure_logging()
    (home / "ui_settings.json").write_text(json.dumps({"appearance_mode": "Light"}), encoding="utf-8")
    files = home / "files"
    files.mkdir()
    template = files / "NA Monthly Close.csv"
    shutil.copyfile(FIXTURE, template)
    reports = home / "reports"
    reports.mkdir()

    # No native file dialogs: they would block the scripted run
    ctk.filedialog.askopenfilename = lambda **_kw: str(template)
    ctk.filedialog.asksaveasfilename = lambda **kw: str(reports / kw.get("initialfile", "report.xlsx"))

    # Display-only: the completion summary shows the log and report paths. Show the paths a typical user would
    # see instead of this computer's temporary folder (the files themselves are still written to the temp home).
    main_window_module.logs_dir = lambda: DISPLAY_APPDATA / "logs"
    real_write_report = main_window_module.write_report_xlsx

    def write_report(path, data):
        real_write_report(path, data)
        return DISPLAY_DOCUMENTS / Path(path).name
    main_window_module.write_report_xlsx = write_report

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.png"):
        old.unlink()

    fake = make_fake()
    app = MainWindow(cred_store=CredentialStore(memory), client_factory=fake.client_factory(),
                     deploy_options={"poll_initial": 0.3, "poll_max": 0.6})
    director = Director(app, fake, home)

    # Record every dialog that is shown, newest last
    original_place = dialogs_module.BaseDialog._place_and_show

    def place_and_show(dlg):
        director.dialogs.append(dlg)
        original_place(dlg)
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
    dialogs_module.BaseDialog._place_and_show = place_and_show
    app.report_callback_exception = lambda exc_type, exc, tb: director.abort(exc)

    x = max(0, (app.winfo_screenwidth() - WINDOW_W) // 2)
    app.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+40")
    app.attributes("-topmost", True)
    print(f"Capturing screenshots into {OUT_DIR} (temporary home {home}) …")
    director.start()
    app.mainloop()
    print(f"Done: {len(director.saved)} screenshot(s).")
    if memory.data:
        print(f"In-memory password entries used: {len(memory.data)} (discarded on exit)")
    return 0 if director.failed is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
