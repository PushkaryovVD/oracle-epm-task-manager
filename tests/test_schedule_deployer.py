"""ScheduleDeployer: sequential deploy + job polling + ledger. Real OracleClient over a fake transport;
fake clock/sleep — no network, no real time."""

import pytest

from app.models import MonthRef, ScheduleStatus
from app.services.deployment_ledger import DeploymentLedger, LedgerStatus
from app.services.member_validator import MemberCheckResult
from app.services.oracle_client import NetworkError, OracleClient
from app.services.period_service import load_period_mappings
from app.services.schedule_deployer import DeployEvent, ScheduleDeployer, StopReason
from app.services.schedule_planner import PlanRequest, plan
from tests.oracle_fakes import BASE, PASSWORD, USER, FakeClock, FakeOracle, resp

TEMPLATE = "Monthly Financial Close"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def name(month: int) -> str:
    return f"Monthly Close - {MONTHS[month - 1]} 2027"


def planned_rows(ledger=None, months=12):
    request = PlanRequest(start=MonthRef(2027, 1), end=MonthRef(2027, months), name_pattern="Monthly Close - {MMMM} {YYYY}",
                          template_name=TEMPLATE, mapping=load_period_mappings()["standard_monthly"],
                          template_valid=True, oracle_check=MemberCheckResult(application="FCCS", verified=True),
                          ledger=ledger)
    return plan(request).rows


class Harness:
    def __init__(self, fake=None, rows=None, org_unit="", ledger=None, **kwargs):
        self.fake = fake or FakeOracle()
        self.clock = FakeClock()
        self.client_sleeps: list[float] = []
        self.client = OracleClient(BASE, USER, PASSWORD, transport=self.fake, retries=3,
                                   sleep=self.client_sleeps.append)
        self.ledger = ledger or DeploymentLedger("fccs-test")
        self.rows = rows if rows is not None else planned_rows(self.ledger)
        self.events: list[tuple] = []
        self.raw_events: list[DeployEvent] = []
        self.deployer = ScheduleDeployer(self.client, self.ledger, TEMPLATE, org_unit, self.rows, self._on_event,
                                         sleep=self.clock.sleep, clock=self.clock, **kwargs)

    def _on_event(self, event: DeployEvent):
        self.raw_events.append(event)
        self.events.append((event.kind, event.row.name if event.row else None,
                            event.row.status if event.row else None, event.position, event.total, event.operation))

    def run(self):
        self.summary = self.deployer.run()
        return self.summary

    def row(self, month):
        return next(r for r in self.rows if r.name == name(month))

    def statuses(self):
        return [r.status for r in self.rows]


def fresh_harness(ledger, fake=None, **kwargs):
    """A second run in a new session: rows re-planned from the same ledger."""
    return Harness(fake=fake, ledger=DeploymentLedger(ledger.environment_id), **kwargs)


# --------------------------------------------------------------------------- happy path
def test_happy_path_twelve_months():
    h = Harness()
    summary = h.run()
    assert h.statuses() == [ScheduleStatus.COMPLETED] * 12
    assert h.fake.posted_names() == [name(m) for m in range(1, 13)]
    # each job polled until 0: POST(-1) + GET(-1) + GET(-1) + GET(0)
    assert len([c for c in h.fake.calls if c.method == "GET"]) == 36
    for m in range(1, 13):
        entry = h.ledger.get(name(m))
        assert entry.status == LedgerStatus.COMPLETED
        assert entry.template_name == TEMPLATE and entry.year == "FY27" and entry.attempts == 1
        assert entry.job_id
        assert "completed" in h.row(m).messages[0]
    assert entry.period == "Dec" and entry.day_zero == "2027-12-31"
    assert (summary.requested, summary.completed, summary.failed, summary.unknown, summary.skipped,
            summary.not_started) == (12, 12, 0, 0, 0, 0)
    assert summary.stop_reason == StopReason.NONE
    assert summary.rows is h.deployer.rows


def test_accepted_job_never_reported_completed_before_success():
    """DESIGN §4.1: an accepted job (status -1) is never reported as Completed."""
    fake = FakeOracle()
    fake.polls[name(1)] = [-1, -1, -1, 0]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    completed_at = []
    original = h._on_event

    def watch(event):
        original(event)
        if event.kind == "row" and event.row.status == ScheduleStatus.COMPLETED:
            completed_at.append(len(fake.calls))

    h.deployer.on_event = watch
    h.run()
    assert completed_at == [5]  # only after the 4th status GET returned 0


def test_job_completed_immediately_in_post_response():
    fake = FakeOracle()
    fake.post_status[name(1)] = 0
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.COMPLETED
    assert [c.method for c in fake.calls] == ["POST"]


def test_org_unit_passed_through_and_payload():
    h = Harness(org_unit="Corporate", rows=planned_rows()[:2])
    h.run()
    params = h.fake.posts[1].payload["parameters"]
    assert params == {"templateName": TEMPLATE, "scheduleName": name(2), "year": "FY27", "period": "Feb",
                      "dayZeroDate": "2027-02-28", "orgUnit": "Corporate"}
    assert h.ledger.get(name(2)).org_unit == "Corporate"


def test_no_org_unit_not_sent():
    h = Harness(rows=planned_rows()[:1])
    h.run()
    assert "orgUnit" not in h.fake.posts[0].payload["parameters"]


# --------------------------------------------------------------------------- failures
def test_job_status_1_failed_with_oracle_message():
    fake = FakeOracle()
    fake.polls[name(3)] = [-1, {"status": 1, "details": "Invalid day zero date", "descriptive": "Error"}]
    h = Harness(fake=fake)
    summary = h.run()
    row = h.row(3)
    assert row.status == ScheduleStatus.FAILED
    assert "Invalid day zero date" in row.messages[0]
    entry = h.ledger.get(name(3))
    assert entry.status == LedgerStatus.FAILED and "Invalid day zero date" in entry.details and entry.job_id
    assert summary.failed == 1 and summary.completed == 11  # other months continue


def test_job_failed_without_details_has_generic_message():
    fake = FakeOracle()
    fake.polls[name(1)] = [1]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.FAILED
    assert "failed" in h.row(1).messages[0]


def test_already_exists_in_400_detail():
    fake = FakeOracle()
    fake.post_outcomes[name(2)] = [resp(400, {"detail": f"Schedule {name(2)} already exists."})]
    h = Harness(fake=fake, rows=planned_rows()[:3])
    summary = h.run()
    assert h.row(2).status == ScheduleStatus.SKIPPED_EXISTS
    assert "already exists" in h.row(2).messages[0]
    assert h.ledger.get(name(2)).status == LedgerStatus.EXISTS
    assert summary.skipped == 1 and summary.completed == 2


def test_already_exists_in_failed_job_details_case_insensitive():
    fake = FakeOracle()
    fake.polls[name(1)] = [{"status": 1, "details": "A schedule with this name ALREADY EXISTS"}]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.SKIPPED_EXISTS
    entry = h.ledger.get(name(1))
    assert entry.status == LedgerStatus.EXISTS and entry.job_id


def test_other_400_is_failed():
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(400, {"detail": "Template Monthly Financial Close not found"})]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.FAILED
    assert h.row(1).messages == ["Template Monthly Financial Close not found"]
    assert h.ledger.get(name(1)).status == LedgerStatus.FAILED


def test_401_on_post_stops_run_rows_pending_nothing_recorded():
    fake = FakeOracle()
    fake.post_outcomes[name(4)] = [resp(401)]
    h = Harness(fake=fake)
    summary = h.run()
    assert h.statuses()[:3] == [ScheduleStatus.COMPLETED] * 3
    assert h.statuses()[3:] == [ScheduleStatus.PENDING] * 9
    assert "credentials" in h.row(4).messages[0]
    assert summary.stop_reason == StopReason.AUTHENTICATION
    assert "user name or password" in summary.stop_message
    assert summary.not_started == 9 and summary.completed == 3
    assert all(h.ledger.get(name(m)) is None for m in range(4, 13))
    assert h.fake.posted_names() == [name(m) for m in range(1, 5)]


def test_401_during_polling_row_unknown_and_run_stops():
    fake = FakeOracle()
    fake.polls[name(2)] = [-1, resp(401)]
    h = Harness(fake=fake)
    summary = h.run()
    assert h.row(2).status == ScheduleStatus.UNKNOWN
    entry = h.ledger.get(name(2))
    assert entry.status == LedgerStatus.UNKNOWN and entry.job_id
    assert summary.stop_reason == StopReason.AUTHENTICATION
    assert h.statuses()[2:] == [ScheduleStatus.PENDING] * 10
    assert summary.unknown == 1 and summary.not_started == 10
    assert len(fake.posts) == 2


def test_403_on_post_failed_and_stops_with_permission():
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(403, {"detail": "Not authorized"})]
    h = Harness(fake=fake)
    summary = h.run()
    assert h.row(1).status == ScheduleStatus.FAILED
    assert "not allowed" in h.row(1).messages[0]
    assert h.ledger.get(name(1)).status == LedgerStatus.FAILED
    assert summary.stop_reason == StopReason.PERMISSION
    assert h.statuses()[1:] == [ScheduleStatus.PENDING] * 11
    assert len(fake.posts) == 1


def test_network_error_maybe_reached_is_unknown_and_never_resent():
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [NetworkError("Oracle did not answer in time.", may_have_reached_server=True)]
    h = Harness(fake=fake, rows=planned_rows()[:2])
    summary = h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert "may have reached Oracle" in h.row(1).messages[0]
    assert "Check the schedule in Oracle" in h.row(1).messages[0]
    assert h.ledger.get(name(1)).status == LedgerStatus.UNKNOWN
    assert fake.posted_names().count(name(1)) == 1
    assert h.row(2).status == ScheduleStatus.COMPLETED  # run continues
    assert summary.unknown == 1
    assert h.client_sleeps == []


def test_network_error_not_reached_is_failed_after_retries():
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [NetworkError("dns", may_have_reached_server=False) for _ in range(4)]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.FAILED
    assert h.ledger.get(name(1)).status == LedgerStatus.FAILED
    assert fake.posted_names().count(name(1)) == 4  # safe: never reached Oracle


@pytest.mark.parametrize("status", [500, 502, 504])
def test_5xx_on_post_unknown_single_post(status):
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(status, {"detail": "Internal error"})]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert "Internal error" in h.row(1).messages[0]
    assert h.ledger.get(name(1)).status == LedgerStatus.UNKNOWN
    assert len(fake.posts) == 1


@pytest.mark.parametrize("status", [503, 429])
def test_503_429_after_retries_failed(status):
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(status) for _ in range(4)]
    h = Harness(fake=fake, rows=planned_rows()[:2])
    h.run()
    assert h.row(1).status == ScheduleStatus.FAILED
    assert h.ledger.get(name(1)).status == LedgerStatus.FAILED
    assert fake.posted_names().count(name(1)) == 4
    assert h.row(2).status == ScheduleStatus.COMPLETED


def test_503_then_success_completes():
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(503), None]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.COMPLETED
    assert h.ledger.get(name(1)).attempts == 1


def test_job_timeout_unknown_with_job_id():
    fake = FakeOracle()
    fake.polls[name(1)] = [-1] * 1000
    h = Harness(fake=fake, rows=planned_rows()[:2], job_timeout_seconds=60)
    h.run()
    row = h.row(1)
    entry = h.ledger.get(name(1))
    assert row.status == ScheduleStatus.UNKNOWN
    assert entry.status == LedgerStatus.UNKNOWN and entry.job_id
    assert entry.job_id in row.messages[0] and "did not finish" in row.messages[0]
    assert "1 minutes" in row.messages[0]
    # stopped right after the deadline: the last sleep crossed 60 s of fake time
    month1_sleeps = h.clock.sleeps[:-3]  # month 2 polls three times
    assert sum(month1_sleeps[:-1]) < 60 <= sum(month1_sleeps)
    assert h.row(2).status == ScheduleStatus.COMPLETED


def test_job_status_poll_error_unknown():
    fake = FakeOracle()
    fake.polls[name(1)] = [resp(500)]
    h = Harness(fake=fake, rows=planned_rows()[:2])
    h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert "Could not read the status" in h.row(1).messages[0]
    assert h.row(2).status == ScheduleStatus.COMPLETED


def test_unknown_job_status_unknown():
    fake = FakeOracle()
    fake.polls[name(1)] = [{"status": "Processing"}]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert "unknown status" in h.row(1).messages[0]
    assert h.ledger.get(name(1)).status == LedgerStatus.UNKNOWN


def test_no_job_id_and_no_link_unknown():
    fake = FakeOracle()
    fake.post_body[name(1)] = {"status": -1}
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert "no job to follow" in h.row(1).messages[0]
    assert [c.method for c in fake.calls] == ["POST"]


def test_foreign_status_link_unknown_and_not_followed():
    fake = FakeOracle()
    fake.post_body[name(1)] = {"jobId": "1", "status": -1,
                               "links": [{"rel": "Job Status", "href": "https://evil.example.com/jobs/1"}]}
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert [c.method for c in fake.calls] == ["POST"]


@pytest.mark.parametrize("body", [b"", b"OK", b"[]"])
def test_unreadable_2xx_deploy_response_is_unknown(body):
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(200, body)]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert h.row(1).status == ScheduleStatus.UNKNOWN
    assert h.ledger.get(name(1)).status == LedgerStatus.UNKNOWN


def test_template_not_found_stops_run():
    fake = FakeOracle()
    for m in range(1, 13):
        fake.post_outcomes[name(m)] = [resp(400, {"detail": "Template Monthly Financial Close not found"})]
    h = Harness(fake=fake)
    h.run()
    assert len(fake.posts) == 1
    assert h.statuses()[1:] == [ScheduleStatus.PENDING] * 11


# --------------------------------------------------------------------------- cancel / skip / retry
def test_cancel_during_run_finishes_current_month():
    fake = FakeOracle()
    h = Harness(fake=fake)
    fake.on_post = lambda n: h.deployer.cancel() if n == name(3) else None
    summary = h.run()
    assert h.statuses()[:3] == [ScheduleStatus.COMPLETED] * 3  # current month polled to the end
    assert h.statuses()[3:] == [ScheduleStatus.PENDING] * 9
    assert summary.stop_reason == StopReason.CANCELLED
    assert summary.not_started == 9 and summary.completed == 3
    assert len(fake.posts) == 3


def test_cancel_before_run_deploys_nothing():
    h = Harness()
    h.deployer.cancel()
    summary = h.run()
    assert h.fake.calls == []
    assert summary.stop_reason == StopReason.CANCELLED and summary.not_started == 12


@pytest.mark.parametrize("ledger_status,row_status", [
    (LedgerStatus.COMPLETED, ScheduleStatus.SKIPPED),
    (LedgerStatus.EXISTS, ScheduleStatus.SKIPPED),
    (LedgerStatus.UNKNOWN, ScheduleStatus.UNKNOWN),
])
def test_rows_blocked_in_ledger_are_not_posted(ledger_status, row_status):
    """Rows marked READY (e.g. stale preview) are still protected by the ledger at deploy time."""
    ledger = DeploymentLedger("fccs-test")
    rows = planned_rows(ledger)  # planned before the ledger knew about month 2
    ledger.record(name(2), ledger_status, job_id="77")
    h = Harness(rows=rows, ledger=ledger)
    h.run()
    assert name(2) not in h.fake.posted_names()
    assert len(h.fake.posts) == 11
    assert h.row(2).status == row_status
    entry = ledger.get(name(2))
    assert entry.status == ledger_status and entry.attempts == 1  # untouched


def test_acceptance_failed_operations_do_not_duplicate_successful_schedules_on_retry():
    """Acceptance: 'Failed operations do not cause successful schedules to be duplicated on retry'."""
    fake = FakeOracle()
    fake.polls[name(3)] = [{"status": 1, "details": "Oracle job error"}]
    fake.post_outcomes[name(5)] = [resp(503) for _ in range(4)]
    fake.post_outcomes[name(7)] = [NetworkError("reset", may_have_reached_server=True)]
    first = Harness(fake=fake)
    first.run()
    assert [r.status for r in first.rows if r.status != ScheduleStatus.COMPLETED] == [
        ScheduleStatus.FAILED, ScheduleStatus.FAILED, ScheduleStatus.UNKNOWN]
    posts_after_first = len(fake.posts)

    # (a) "Retry failed" with the same row objects: only FAILED rows are deployable.
    del fake.polls[name(3)]  # Oracle-side problem fixed; 503s for month 5 are used up
    retry = Harness(fake=fake, rows=first.rows, ledger=first.ledger)
    assert [r.name for r in retry.deployer.rows] == [name(3), name(5)]
    retry.run()
    assert fake.posted_names()[posts_after_first:] == [name(3), name(5)]
    assert first.row(3).status == ScheduleStatus.COMPLETED and first.row(5).status == ScheduleStatus.COMPLETED
    assert first.ledger.get(name(3)).attempts == 2

    # (b) Re-planned in a new session: nothing that succeeded (or is unknown) is deployable or posted.
    again = fresh_harness(first.ledger, fake=fake)
    statuses = {r.name: r.status for r in again.rows}
    assert statuses[name(7)] == ScheduleStatus.BLOCKED
    assert all(s == ScheduleStatus.SKIPPED_EXISTS for n, s in statuses.items() if n != name(7))
    posts_before = len(fake.posts)
    again.run()
    assert len(fake.posts) == posts_before

    # (c) Even if every row is forced back to READY, the ledger prevents duplicates.
    forced = planned_rows()
    third = Harness(fake=fake, rows=forced, ledger=DeploymentLedger("fccs-test"))
    third.run()
    assert len(fake.posts) == posts_before
    posted = fake.posted_names()
    expected = {name(m): 1 for m in range(1, 13)}
    expected[name(3)] = 2   # job failed + one retry
    expected[name(5)] = 5   # 4 x 503 (never processed by Oracle) + one retry
    assert {n: posted.count(n) for n in set(posted)} == expected
    assert posted.count(name(7)) == 1  # unknown outcome: never re-sent automatically


def test_successful_schedules_posted_exactly_once_across_retries():
    fake = FakeOracle()
    fake.polls[name(3)] = [{"status": 1, "details": "Oracle job error"}]
    first = Harness(fake=fake)
    first.run()
    del fake.polls[name(3)]
    Harness(fake=fake, rows=first.rows, ledger=first.ledger).run()
    Harness(fake=fake, rows=planned_rows(), ledger=DeploymentLedger("fccs-test")).run()
    posted = fake.posted_names()
    assert {n: posted.count(n) for n in set(posted)} == {**{name(m): 1 for m in range(1, 13)}, name(3): 2}


def test_deployer_filters_non_deployable_rows():
    rows = planned_rows()[:6]
    rows[0].status = ScheduleStatus.BLOCKED
    rows[1].status = ScheduleStatus.SKIPPED_EXISTS
    rows[2].status = ScheduleStatus.SKIPPED
    rows[3].status = ScheduleStatus.COMPLETED
    rows[4].status = ScheduleStatus.UNKNOWN
    rows[5].status = ScheduleStatus.READY_UNCHECKED
    h = Harness(rows=rows)
    summary = h.run()
    assert [r.name for r in h.deployer.rows] == [name(6)]
    assert h.fake.posted_names() == [name(6)]
    assert summary.requested == 1
    assert [r.status for r in rows[:5]] == [ScheduleStatus.BLOCKED, ScheduleStatus.SKIPPED_EXISTS,
                                            ScheduleStatus.SKIPPED, ScheduleStatus.COMPLETED, ScheduleStatus.UNKNOWN]


def test_deployable_statuses_include_pending_and_failed():
    rows = planned_rows()[:4]
    rows[0].status = ScheduleStatus.READY
    rows[1].status = ScheduleStatus.READY_UNCHECKED
    rows[2].status = ScheduleStatus.FAILED
    rows[3].status = ScheduleStatus.PENDING
    assert len(Harness(rows=rows).deployer.rows) == 4


def test_warnings_kept_while_pending_and_replaced_by_outcome():
    rows = planned_rows()[:1]
    rows[0].messages = ["Warning: name longer than 80", "old error"]
    seen = []
    h = Harness(rows=rows)
    h.deployer.on_event = lambda e: seen.append((e.kind, list(e.row.messages) if e.row else None))
    h.run()
    assert ("row", ["Warning: name longer than 80"]) in seen
    assert rows[0].messages[0].startswith("Oracle job")


# --------------------------------------------------------------------------- events / timing
def test_progress_events_and_finished_summary():
    fake = FakeOracle()
    fake.polls[name(2)] = [1]
    h = Harness(fake=fake, rows=planned_rows()[:3])
    summary = h.run()
    progress = [e for e in h.events if e[0] == "progress"]
    assert {(e[1], e[3], e[4]) for e in progress} == {(name(1), 1, 3), (name(2), 2, 3), (name(3), 3, 3)}
    ops = [e[5] for e in progress if e[1] == name(1)]
    assert ops[:2] == ["Checking deployment history", "Sending deployment request"]
    assert all(op.startswith("Waiting for Oracle job ") for op in ops[2:]) and len(ops) == 5
    row_states = [e[2] for e in h.events if e[0] == "row" and e[1] == name(1)]
    assert row_states == [ScheduleStatus.PENDING, ScheduleStatus.VALIDATING, ScheduleStatus.DEPLOYING,
                          ScheduleStatus.COMPLETED]
    finished = h.raw_events[-1]
    assert finished.kind == "finished" and finished.total == 3
    assert [e for e in h.raw_events if e.kind == "finished"] == [finished]
    assert finished.summary is summary
    assert (summary.requested, summary.completed, summary.failed) == (3, 2, 1)
    assert summary.duration_seconds == pytest.approx(sum(h.clock.sleeps))
    assert summary.duration_seconds > 0


def test_poll_delay_grows_and_is_capped():
    fake = FakeOracle()
    fake.polls[name(1)] = [-1] * 9 + [0]
    h = Harness(fake=fake, rows=planned_rows()[:1], poll_initial=2.0, poll_max=15.0)
    h.run()
    assert h.clock.sleeps == pytest.approx([2.0, 3.0, 4.5, 6.75, 10.125, 15.0, 15.0, 15.0, 15.0, 15.0])


def test_empty_run_emits_finished():
    h = Harness(rows=[])
    summary = h.run()
    assert summary.requested == 0 and h.fake.calls == []
    assert [e[0] for e in h.events] == ["finished"]


def test_ledger_written_before_next_month_starts():
    fake = FakeOracle()
    h = Harness(fake=fake, rows=planned_rows()[:3])
    seen = {}
    fake.on_post = lambda n: seen.setdefault(n, [e.schedule_name for e in DeploymentLedger("fccs-test").entries()])
    h.run()
    assert set(seen[name(3)]) == {name(1), name(2)}  # persisted on disk, not only in memory


def test_password_not_in_row_messages_or_ledger():
    fake = FakeOracle()
    fake.polls[name(1)] = [{"status": 1, "details": f"login failed password={PASSWORD}"}]
    h = Harness(fake=fake, rows=planned_rows()[:1])
    h.run()
    assert PASSWORD not in " ".join(h.row(1).messages)
    assert PASSWORD not in h.ledger.path.read_text(encoding="utf-8")



# --------------------------------------------------------------------------- regressions (review fixes)
def test_ledger_write_failure_stops_run_and_still_finishes(monkeypatch):
    h = Harness(rows=planned_rows(months=3))
    original = h.ledger.record

    def failing_record(schedule_name, status, **info):
        if schedule_name == name(2):
            raise OSError("disk full")
        return original(schedule_name, status, **info)

    monkeypatch.setattr(h.ledger, "record", failing_record)
    summary = h.run()
    assert summary.stop_reason == StopReason.HISTORY
    assert h.statuses() == [ScheduleStatus.COMPLETED, ScheduleStatus.UNKNOWN, ScheduleStatus.PENDING]
    assert h.raw_events[-1].kind == "finished"
    assert len(h.fake.posts) == 2


def test_permission_denied_while_polling_is_unknown_and_stops():
    fake = FakeOracle()
    fake.polls[name(1)] = [-1, resp(403, {"detail": "forbidden"})]
    h = Harness(fake=fake, rows=planned_rows(months=3))
    summary = h.run()
    assert summary.stop_reason == StopReason.PERMISSION
    assert h.statuses() == [ScheduleStatus.UNKNOWN, ScheduleStatus.PENDING, ScheduleStatus.PENDING]
    assert h.ledger.get(name(1)).status == LedgerStatus.UNKNOWN
    assert len(fake.posts) == 1


def test_template_not_found_in_failed_job_stops_run():
    fake = FakeOracle()
    fake.polls[name(1)] = [-1, {"status": 1, "details": "Template Monthly Financial Close does not exist"}]
    h = Harness(fake=fake, rows=planned_rows(months=3))
    summary = h.run()
    assert summary.stop_reason == StopReason.TEMPLATE
    assert h.statuses() == [ScheduleStatus.FAILED, ScheduleStatus.PENDING, ScheduleStatus.PENDING]
