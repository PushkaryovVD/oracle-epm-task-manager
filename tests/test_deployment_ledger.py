"""DeploymentLedger: per-environment JSON record of deployment attempts."""

import json
from datetime import datetime, timedelta

import pytest

from app.services import deployment_ledger as ledger_module
from app.services.deployment_ledger import BLOCKING_STATUSES, DeploymentLedger, LedgerEntry, LedgerStatus


@pytest.fixture
def ticking_time(monkeypatch):
    """Deterministic, strictly increasing datetime.now() inside the ledger module."""
    start = datetime(2027, 1, 31, 9, 0, 0)
    state = {"n": 0}

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            state["n"] += 1
            return start + timedelta(minutes=state["n"])

    monkeypatch.setattr(ledger_module, "datetime", FakeDateTime)
    return state


def ledger_dir(home):
    return home / "ledger"


def test_path_in_epmtd_home(isolated_home):
    ledger = DeploymentLedger("fccs-test")
    assert ledger.path == isolated_home / "ledger" / "fccs-test.json"
    assert not ledger.path.exists()  # nothing written until the first record
    assert ledger.entries() == [] and ledger.load_error is None


def test_explicit_path(tmp_path):
    path = tmp_path / "custom" / "l.json"
    ledger = DeploymentLedger("x", path=path)
    ledger.record("A", LedgerStatus.COMPLETED)
    assert path.exists()


def test_record_and_get_case_insensitive_and_trimmed():
    ledger = DeploymentLedger("env")
    entry = ledger.record("Monthly Close - January 2027", LedgerStatus.COMPLETED, job_id="42",
                          template_name="Monthly Financial Close", year="FY27", period="Jan",
                          day_zero="2027-01-31", org_unit="Corporate", details="ok")
    for probe in ("Monthly Close - January 2027", "MONTHLY CLOSE - JANUARY 2027", "  monthly close - january 2027 "):
        assert ledger.get(probe) is entry
    assert ledger.get("Monthly Close - February 2027") is None
    assert entry.status == LedgerStatus.COMPLETED == "completed"
    assert (entry.job_id, entry.year, entry.period, entry.org_unit) == ("42", "FY27", "Jan", "Corporate")
    assert entry.attempts == 1
    assert entry.updated_at  # ISO timestamp
    datetime.fromisoformat(entry.updated_at)


def test_casefold_matches_german_sharp_s():
    ledger = DeploymentLedger("env")
    ledger.record("Abschluss Straße", LedgerStatus.COMPLETED)
    assert ledger.get("ABSCHLUSS STRASSE") is not None


def test_attempts_increment_and_last_status_wins():
    ledger = DeploymentLedger("env")
    ledger.record("Close Jan", LedgerStatus.FAILED, details="boom")
    ledger.record("CLOSE JAN", LedgerStatus.FAILED, details="boom again")
    third = ledger.record("Close Jan", LedgerStatus.COMPLETED, job_id="7")
    assert third.attempts == 3
    assert third.status == LedgerStatus.COMPLETED
    assert third.details == ""  # a new record replaces the old details
    assert len(ledger.entries()) == 1


@pytest.mark.parametrize("status", list(LedgerStatus))
def test_all_statuses_roundtrip(status):
    DeploymentLedger("env").record("S", status)
    reloaded = DeploymentLedger("env").get("S")
    assert reloaded.status == status
    assert LedgerStatus(reloaded.status) is status


def test_blocking_statuses():
    assert set(BLOCKING_STATUSES) == {LedgerStatus.COMPLETED, LedgerStatus.EXISTS, LedgerStatus.UNKNOWN}
    assert LedgerStatus.FAILED not in BLOCKING_STATUSES


def test_persistence_roundtrip(isolated_home):
    first = DeploymentLedger("fccs-test")
    first.record("Clôture – janvier 2027", LedgerStatus.COMPLETED, job_id="1", details="Готово")
    first.record("Close Feb", LedgerStatus.UNKNOWN, job_id="2")
    path = ledger_dir(isolated_home) / "fccs-test.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["environment_id"] == "fccs-test"
    assert {e["schedule_name"] for e in data["entries"]} == {"Clôture – janvier 2027", "Close Feb"}

    second = DeploymentLedger("fccs-test")
    assert second.load_error is None
    jan = second.get("CLÔTURE – JANVIER 2027")
    assert jan == first.get("Clôture – janvier 2027")
    assert jan.details == "Готово" and jan.attempts == 1
    # attempts continue across sessions
    assert second.record("Close Feb", LedgerStatus.FAILED).attempts == 2


def test_environments_are_isolated():
    DeploymentLedger("prod").record("Close Jan", LedgerStatus.COMPLETED)
    assert DeploymentLedger("test").get("Close Jan") is None


def test_unknown_fields_in_file_are_ignored(isolated_home):
    path = ledger_dir(isolated_home) / "env.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 2, "entries": [
        {"schedule_name": "A", "status": "completed", "future_field": 1}]}), encoding="utf-8")
    ledger = DeploymentLedger("env")
    assert ledger.load_error is None
    assert ledger.get("a").status == "completed"


def test_atomic_write_leaves_no_tmp(isolated_home):
    ledger = DeploymentLedger("env")
    for i in range(5):
        ledger.record(f"S{i}", LedgerStatus.COMPLETED)
    files = sorted(p.name for p in ledger_dir(isolated_home).iterdir())
    assert files == ["env.json"]


def test_failed_write_keeps_previous_file_intact(isolated_home, monkeypatch):
    ledger = DeploymentLedger("env")
    ledger.record("Good", LedgerStatus.COMPLETED)
    before = ledger.path.read_bytes()

    def broken_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ledger_module.json, "dump", broken_dump)
    with pytest.raises(OSError):
        ledger.record("Other", LedgerStatus.COMPLETED)
    assert ledger.path.read_bytes() == before


def test_failed_write_does_not_leave_tmp(isolated_home, monkeypatch):
    ledger = DeploymentLedger("env")
    ledger.record("Good", LedgerStatus.COMPLETED)
    monkeypatch.setattr(ledger_module.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        ledger.record("Other", LedgerStatus.COMPLETED)
    assert sorted(p.name for p in ledger_dir(isolated_home).iterdir()) == ["env.json"]


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",                                                     # AttributeError: list has no .get
    json.dumps({"entries": [{"status": "completed"}]}),      # TypeError: schedule_name missing
    json.dumps({"entries": ["text"]}),
    "",
])
def test_corrupt_file_moved_aside(isolated_home, content):
    path = ledger_dir(isolated_home) / "env.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    ledger = DeploymentLedger("env")
    assert ledger.entries() == []
    assert ledger.load_error and "could not be read" in ledger.load_error
    assert not path.exists()
    backups = list(path.parent.glob("env.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content
    assert backups[0].name in ledger.load_error

    # The ledger keeps working afterwards and writes a fresh file.
    ledger.record("New", LedgerStatus.COMPLETED)
    assert DeploymentLedger("env").get("new") is not None


def test_corrupt_file_with_valid_prefix_drops_partial_entries(isolated_home):
    path = ledger_dir(isolated_home) / "env.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"entries": [{"schedule_name": "A", "status": "completed"}, {"bad": 1}]}),
                    encoding="utf-8")
    ledger = DeploymentLedger("env")
    assert ledger.load_error is not None
    assert ledger.get("A") is None  # no half-loaded state


@pytest.mark.parametrize("exists,status,details", [
    (True, LedgerStatus.EXISTS, "exists in Oracle"),
    (False, LedgerStatus.FAILED, "may be deployed again"),
])
def test_resolve_unknown(exists, status, details):
    ledger = DeploymentLedger("env")
    ledger.record("Close Jan", LedgerStatus.UNKNOWN, job_id="9")
    ledger.resolve_unknown("CLOSE JAN", exists_in_oracle=exists)
    entry = ledger.get("Close Jan")
    assert entry.status == status
    assert details in entry.details
    assert entry.job_id == "9"
    assert DeploymentLedger("env").get("close jan").status == status  # persisted


@pytest.mark.parametrize("status", [LedgerStatus.COMPLETED, LedgerStatus.EXISTS, LedgerStatus.FAILED])
def test_resolve_unknown_noop_for_other_statuses(status, ticking_time):
    ledger = DeploymentLedger("env")
    before = ledger.record("Close Jan", status, details="orig")
    snapshot = LedgerEntry(**vars(before))
    ledger.resolve_unknown("Close Jan", exists_in_oracle=True)
    ledger.resolve_unknown("Close Jan", exists_in_oracle=False)
    assert ledger.get("Close Jan") == snapshot


def test_resolve_unknown_missing_name_is_noop(isolated_home):
    ledger = DeploymentLedger("env")
    ledger.resolve_unknown("Nope", exists_in_oracle=True)
    assert ledger.get("Nope") is None
    assert not ledger.path.exists()


def test_with_status():
    ledger = DeploymentLedger("env")
    ledger.record("A", LedgerStatus.COMPLETED)
    ledger.record("B", LedgerStatus.FAILED)
    ledger.record("C", LedgerStatus.UNKNOWN)
    ledger.record("D", LedgerStatus.COMPLETED)
    assert {e.schedule_name for e in ledger.with_status(LedgerStatus.COMPLETED)} == {"A", "D"}
    assert {e.schedule_name for e in ledger.with_status(LedgerStatus.FAILED, LedgerStatus.UNKNOWN)} == {"B", "C"}
    assert ledger.with_status() == []


def test_entries_newest_first(ticking_time):
    ledger = DeploymentLedger("env")
    ledger.record("A", LedgerStatus.COMPLETED)
    ledger.record("B", LedgerStatus.FAILED)
    ledger.record("C", LedgerStatus.UNKNOWN)
    ledger.record("A", LedgerStatus.COMPLETED)  # updated again -> newest
    assert [e.schedule_name for e in ledger.entries()] == ["A", "C", "B"]
    ledger.resolve_unknown("C", exists_in_oracle=True)  # resolving also refreshes updated_at
    assert [e.schedule_name for e in ledger.entries()] == ["C", "A", "B"]
