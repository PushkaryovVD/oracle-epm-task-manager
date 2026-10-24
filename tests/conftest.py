"""Shared fixtures: isolated app-data folder, in-memory keyring backends, Excel builders."""

from pathlib import Path

import openpyxl
import pytest
from keyring.errors import KeyringError, PasswordDeleteError


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own EPMTD_HOME so nothing touches %APPDATA%."""
    home = tmp_path / "epmtd_home"
    monkeypatch.setenv("EPMTD_HOME", str(home))
    return home


class MemoryKeyring:
    """Minimal in-memory keyring backend (never touches Windows Credential Manager)."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.calls: list[tuple] = []

    def get_password(self, service, username):
        self.calls.append(("get", service, username))
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.calls.append(("set", service, username))
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.calls.append(("delete", service, username))
        if (service, username) not in self.store:
            raise PasswordDeleteError("Password not found")
        del self.store[(service, username)]


class BrokenKeyring:
    """Backend whose every operation fails like a locked / unavailable vault."""

    def get_password(self, service, username):
        raise KeyringError("vault unavailable")

    def set_password(self, service, username, password):
        raise KeyringError("vault unavailable")

    def delete_password(self, service, username):
        raise KeyringError("vault unavailable")


@pytest.fixture
def memory_keyring():
    return MemoryKeyring()


@pytest.fixture
def broken_keyring():
    return BrokenKeyring()


STANDARD_HEADERS = ["Task ID", "Task Name", "Task Type", "Owner", "Assignee", "Approver1",
                    "Predecessor", "Start Day", "End Day"]


def standard_rows():
    return [
        ["T001", "Close subledgers", "Basic", "controller", "ap.lead", "controller", None, -2, -1],
        ["T002", "Load trial balance", "Data Load", "controller", "fin.analyst", "controller", "T001", 0, 0],
        ["T003", "Run consolidation", "Consolidation", "controller", "fin.analyst", "cfo", "T002", 1, 1],
    ]


@pytest.fixture
def make_workbook(tmp_path):
    """Build an .xlsx in tmp_path.

    make_workbook(name, headers, rows, title_rows=(), sheet_title="Tasks", customize=None)
    customize(wb, ws) may modify the workbook before it is saved.
    """

    def _make(name="Monthly_Close_Template.xlsx", headers=None, rows=None, title_rows=(),
              sheet_title="Tasks", customize=None) -> Path:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_title
        for title in title_rows:
            ws.append(list(title))
        if headers is not None:
            ws.append(list(headers))
        for row in rows or []:
            ws.append(list(row))
        if customize:
            customize(wb, ws)
        path = tmp_path / name
        wb.save(path)
        return path

    return _make
