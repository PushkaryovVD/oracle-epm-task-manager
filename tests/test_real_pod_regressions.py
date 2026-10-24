"""Regressions for behaviour observed on the customer's real FCCS test pod (2026-10-23)."""

import pytest

from app.models import ScheduleStatus
from app.services.deployment_ledger import DeploymentLedger, LedgerStatus
from app.services.oracle_client import Job, OracleClient, UnexpectedResponseError
from app.services.schedule_deployer import recheck_unknown
from tests.oracle_fakes import BASE, JOBS_PATH, PASSWORD, USER, FakeOracle, ScriptedTransport, resp
from tests.test_schedule_deployer import Harness, name, planned_rows

LINK_443 = BASE + ":443" + JOBS_PATH + "/14008"


def client_with(*items):
    transport = ScriptedTransport(*items)
    return OracleClient(BASE, USER, PASSWORD, transport=transport, sleep=lambda s: None), transport


def test_status_link_with_explicit_443_is_same_server():
    client, transport = client_with(resp(200, {"jobId": 14008, "status": 0}))
    job = client.get_job(Job(job_id="14008", status=-1, status_url=LINK_443))
    assert job.succeeded
    assert transport.calls[0].url == BASE + JOBS_PATH + "/14008"   # rebuilt on our own base URL


@pytest.mark.parametrize("link", [
    "https://evil.example.com" + JOBS_PATH + "/1",
    BASE.replace("https://", "http://") + JOBS_PATH + "/1",
    BASE + ":8443" + JOBS_PATH + "/1",
    BASE.replace("https://", "https://user:pw@") + JOBS_PATH + "/1",
])
def test_foreign_links_still_rejected(link):
    client, transport = client_with()
    with pytest.raises(UnexpectedResponseError):
        client.get_job(Job(job_id="1", status=-1, status_url=link))
    assert transport.calls == []


def test_job_id_taken_from_status_link_when_missing_in_body():
    body = {"status": -1, "details": "In Process", "links": [{"rel": "Job Status", "href": LINK_443}]}
    client, _ = client_with(resp(201, body))
    job = client.deploy_template("NA Monthly Close", "S", "FY26", "Aug", __import__("datetime").date(2026, 8, 31))
    assert job.job_id == "14008"
    assert job.running


def test_deploy_completes_with_443_link():
    """The exact real-pod shape: 201 + Job Status link with ':443' must be polled, not reported Unknown."""
    fake = FakeOracle()
    original = fake.__call__

    def transport(method, url, headers, body, timeout):
        response = original(method, url, headers, body, timeout)
        if method == "POST":
            response.body = response.body.replace(BASE.encode(), (BASE + ":443").encode())
            response.status = 201
        return response

    h = Harness(fake=fake, rows=planned_rows(months=2))
    h.client._transport = transport
    h.run()
    assert h.statuses() == [ScheduleStatus.COMPLETED, ScheduleStatus.COMPLETED]
    entry = h.ledger.get(name(1))
    assert entry.status == LedgerStatus.COMPLETED and entry.job_id and entry.status_url.endswith(entry.job_id)


# --------------------------------------------------------------------------- recheck_unknown
def _unknown(ledger, schedule, **info):
    ledger.record(schedule, LedgerStatus.UNKNOWN, year="FY26", period="Aug", **info)


def test_recheck_resolves_completed_failed_running_and_manual():
    ledger = DeploymentLedger("pod")
    _unknown(ledger, "A", job_id="1", status_url=BASE + JOBS_PATH + "/1")
    _unknown(ledger, "B", job_id="2", status_url=LINK_443.replace("14008", "2"))
    _unknown(ledger, "C", job_id="3")
    _unknown(ledger, "D")
    client, transport = client_with(
        resp(200, {"jobId": 1, "status": 0, "details": "Completed"}),
        resp(200, {"jobId": 2, "status": 1, "details": "Period is locked"}),
        resp(200, {"jobId": 3, "status": -1}),
    )
    results = {r.schedule_name: r for r in recheck_unknown(client, ledger)}
    assert ledger.get("A").status == LedgerStatus.COMPLETED
    assert ledger.get("B").status == LedgerStatus.FAILED and "locked" in ledger.get("B").details
    assert ledger.get("C").status == LedgerStatus.UNKNOWN and "still running" in results["C"].message
    assert ledger.get("D").status == LedgerStatus.UNKNOWN and "No Oracle job" in results["D"].message
    assert [c.method for c in transport.calls] == ["GET", "GET", "GET"]   # read-only
    assert transport.calls[2].url.endswith(JOBS_PATH + "/3")             # id-only entry uses the jobs path


def test_recheck_exists_message_records_exists():
    ledger = DeploymentLedger("pod")
    _unknown(ledger, "A", job_id="1")
    client, _ = client_with(resp(200, {"jobId": 1, "status": 1, "details": "Schedule A already exists"}))
    recheck_unknown(client, ledger)
    assert ledger.get("A").status == LedgerStatus.EXISTS


def test_recheck_oracle_error_keeps_unknown():
    ledger = DeploymentLedger("pod")
    _unknown(ledger, "A", job_id="1")
    client, _ = client_with(resp(500, {"detail": "boom"}), resp(500, {}), resp(500, {}), resp(500, {}))
    (result,) = recheck_unknown(client, ledger)
    assert ledger.get("A").status == LedgerStatus.UNKNOWN and "not available" in result.message


def test_real_duplicate_name_answer_is_skipped_exists():
    """Real pod: POST for an existing name -> HTTP 400 'You must specify a unique schedule name.'"""
    fake = FakeOracle()
    fake.post_outcomes[name(1)] = [resp(400, {"detail": "You must specify a unique schedule name."})]
    h = Harness(fake=fake, rows=planned_rows(months=2))
    h.run()
    assert h.statuses() == [ScheduleStatus.SKIPPED_EXISTS, ScheduleStatus.COMPLETED]
    assert h.ledger.get(name(1)).status == LedgerStatus.EXISTS
