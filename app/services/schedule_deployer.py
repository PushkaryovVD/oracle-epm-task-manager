"""Sequential deployment of planned schedules, one Oracle job at a time.

Runs in a worker thread. Reports progress through a callback (the UI forwards it to the Tk thread via a queue).
Safety rules (docs/DESIGN.md §9):
- an accepted job is never reported as Completed: the job is polled until Oracle reports success or failure;
- every outcome is written to the ledger before the next month starts;
- a request whose outcome is unknown is never repeated automatically;
- 401 stops the run; remaining months stay Pending.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable

from app.models import PlannedSchedule, ScheduleStatus
from app.services.deployment_ledger import BLOCKING_STATUSES, DeploymentLedger, LedgerStatus
from app.services.oracle_client import (
    AuthenticationError, BadRequestError, Job, NetworkError, OracleClient, OracleError, PermissionDeniedError,
    ServiceUnavailableError, UnexpectedResponseError,
)
from app.utils.config import load_json

log = logging.getLogger(__name__)

DEPLOYABLE = (ScheduleStatus.READY, ScheduleStatus.READY_UNCHECKED, ScheduleStatus.FAILED, ScheduleStatus.PENDING)


class StopReason(StrEnum):
    NONE = ""
    CANCELLED = "Cancelled by user"
    AUTHENTICATION = "Oracle rejected the credentials"
    PERMISSION = "The Oracle user is not allowed to deploy templates"
    TEMPLATE = "The template was not found in Oracle"
    HISTORY = "The deployment history could not be saved"
    ERROR = "Unexpected error"


@dataclass
class DeployEvent:
    kind: str                       # "progress" | "row" | "finished"
    row: PlannedSchedule | None = None
    position: int = 0               # 1-based position among the months of this run
    total: int = 0
    operation: str = ""
    summary: "DeploySummary | None" = None


@dataclass
class DeploySummary:
    requested: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    unknown: int = 0
    not_started: int = 0
    duration_seconds: float = 0.0
    stop_reason: StopReason = StopReason.NONE
    stop_message: str = ""
    rows: list[PlannedSchedule] = field(default_factory=list)


class TemplateNotFoundError(Exception):
    """Oracle says the template does not exist: every following month would fail the same way."""


def _patterns(key: str) -> list[re.Pattern]:
    try:
        return [re.compile(p, re.IGNORECASE) for p in load_json("oracle_messages.json")[key]]
    except (OSError, KeyError, ValueError, re.error):
        log.exception("config/oracle_messages.json: cannot load '%s'", key)
        return []


@dataclass
class RecheckResult:
    schedule_name: str
    status: LedgerStatus          # status in the ledger after the re-check
    message: str


def recheck_unknown(client: OracleClient, ledger: DeploymentLedger) -> list[RecheckResult]:
    """Ask Oracle once for the job status of every UNKNOWN ledger entry that has a job link or id. Read-only."""
    exists_patterns = _patterns("schedule_exists_patterns")
    results = []
    for entry in ledger.with_status(LedgerStatus.UNKNOWN):
        if not entry.status_url and not entry.job_id:
            results.append(RecheckResult(entry.schedule_name, LedgerStatus.UNKNOWN,
                                         "No Oracle job is known for this attempt. Check the schedule in Oracle "
                                         "and choose 'Exists in Oracle' or 'Not in Oracle'."))
            continue
        try:
            job = client.get_job(Job(job_id=entry.job_id, status=-1, status_url=entry.status_url or None))
        except OracleError as exc:
            results.append(RecheckResult(entry.schedule_name, LedgerStatus.UNKNOWN, f"Status not available: {exc}"))
            continue
        info = dict(template_name=entry.template_name, year=entry.year, period=entry.period,
                    day_zero=entry.day_zero, org_unit=entry.org_unit, job_id=job.job_id or entry.job_id,
                    status_url=entry.status_url)
        if job.running:
            results.append(RecheckResult(entry.schedule_name, LedgerStatus.UNKNOWN,
                                         f"Oracle job {info['job_id']} is still running. Re-check later."))
        elif job.succeeded:
            ledger.record(entry.schedule_name, LedgerStatus.COMPLETED, details=job.message or "Completed "
                          "(confirmed by re-check).", **info)
            results.append(RecheckResult(entry.schedule_name, LedgerStatus.COMPLETED,
                                         f"Oracle job {info['job_id']} completed."))
        elif job.status is None:
            results.append(RecheckResult(entry.schedule_name, LedgerStatus.UNKNOWN,
                                         f"Oracle job {info['job_id']} returned an unknown status."))
        else:
            message = job.message or f"Oracle job {info['job_id']} failed."
            status = (LedgerStatus.EXISTS if any(p.search(message) for p in exists_patterns)
                      else LedgerStatus.FAILED)
            ledger.record(entry.schedule_name, status, details=message, **info)
            results.append(RecheckResult(entry.schedule_name, status, message))
    return results


class ScheduleDeployer:
    def __init__(self, client: OracleClient, ledger: DeploymentLedger, template_name: str, org_unit: str,
                 rows: list[PlannedSchedule], on_event: Callable[[DeployEvent], None], *,
                 job_timeout_seconds: float = 1800, poll_initial: float = 2.0, poll_max: float = 15.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.ledger = ledger
        self.template_name = template_name
        self.org_unit = org_unit
        self.rows = [r for r in rows if r.status in DEPLOYABLE]
        self.on_event = on_event
        self.job_timeout = job_timeout_seconds
        self.poll_initial = poll_initial
        self.poll_max = poll_max
        self._sleep = sleep
        self._clock = clock
        self._cancel = threading.Event()
        self._exists_patterns = _patterns("schedule_exists_patterns")
        self._template_patterns = _patterns("template_not_found_patterns")

    def cancel(self) -> None:
        """Stop after the current month. A running Oracle job cannot be cancelled."""
        self._cancel.set()

    # ------------------------------------------------------------------ run
    def run(self) -> DeploySummary:
        started = self._clock()
        summary = DeploySummary(requested=len(self.rows), rows=self.rows)
        for row in self.rows:
            self._set(row, ScheduleStatus.PENDING, None)

        total = len(self.rows)
        for position, row in enumerate(self.rows, start=1):
            if self._cancel.is_set():
                summary.stop_reason = StopReason.CANCELLED
                break
            try:
                self._deploy_one(row, position, total)
            except AuthenticationError as exc:
                summary.stop_reason, summary.stop_message = StopReason.AUTHENTICATION, str(exc)
                break
            except PermissionDeniedError as exc:
                summary.stop_reason, summary.stop_message = StopReason.PERMISSION, str(exc)
                break
            except TemplateNotFoundError as exc:
                summary.stop_reason, summary.stop_message = StopReason.TEMPLATE, str(exc)
                break
            except OSError as exc:  # ledger could not be written: continuing would lose duplicate protection
                log.exception("Deployment ledger write failed")
                summary.stop_reason, summary.stop_message = StopReason.HISTORY, str(exc)
                if row.status in (ScheduleStatus.DEPLOYING, ScheduleStatus.VALIDATING):
                    self._set(row, ScheduleStatus.UNKNOWN, "The result could not be saved to the deployment "
                              "history. Check the schedule in Oracle.")
                break
            except Exception as exc:  # never leave the UI without a "finished" event
                log.exception("Unexpected error during deployment")
                summary.stop_reason, summary.stop_message = StopReason.ERROR, str(exc)
                if row.status in (ScheduleStatus.DEPLOYING, ScheduleStatus.VALIDATING):
                    self._set(row, ScheduleStatus.UNKNOWN, f"Unexpected error: {exc}. Check the schedule in Oracle.")
                break

        for row in self.rows:
            if row.status == ScheduleStatus.COMPLETED:
                summary.completed += 1
            elif row.status in (ScheduleStatus.SKIPPED, ScheduleStatus.SKIPPED_EXISTS):
                summary.skipped += 1
            elif row.status == ScheduleStatus.FAILED:
                summary.failed += 1
            elif row.status == ScheduleStatus.UNKNOWN:
                summary.unknown += 1
            else:
                summary.not_started += 1
        summary.duration_seconds = self._clock() - started
        log.info("Deployment finished: %s", summary)
        self.on_event(DeployEvent("finished", summary=summary, total=total))
        return summary

    # ------------------------------------------------------------------ one month
    def _deploy_one(self, row: PlannedSchedule, position: int, total: int) -> None:
        self._progress(row, position, total, "Checking deployment history")
        self._set(row, ScheduleStatus.VALIDATING, None)
        previous = self.ledger.get(row.name)
        if previous is not None and previous.status in BLOCKING_STATUSES:
            if previous.status == LedgerStatus.UNKNOWN:
                self._set(row, ScheduleStatus.UNKNOWN, "An earlier deployment result is unknown; check Oracle first.")
            else:
                self._set(row, ScheduleStatus.SKIPPED, "Already deployed or existing in Oracle (deployment history).")
            return

        info = dict(template_name=self.template_name, year=row.year_member, period=row.period_member,
                    day_zero=row.day_zero.isoformat() if row.day_zero else "", org_unit=self.org_unit)

        self._progress(row, position, total, "Sending deployment request")
        self._set(row, ScheduleStatus.DEPLOYING, None)
        try:
            job = self.client.deploy_template(self.template_name, row.name, row.year_member, row.period_member,
                                              row.day_zero, self.org_unit)
        except AuthenticationError:
            self._set(row, ScheduleStatus.PENDING, "Not deployed: Oracle rejected the credentials.")
            raise
        except PermissionDeniedError as exc:
            self.ledger.record(row.name, LedgerStatus.FAILED, details=str(exc), **info)
            self._set(row, ScheduleStatus.FAILED, str(exc))
            raise
        except NetworkError as exc:
            if exc.may_have_reached_server:
                self._unknown(row, info, "", f"{exc} The request may have reached Oracle.")
            else:
                self._failed(row, info, "", str(exc))
            return
        except ServiceUnavailableError as exc:
            if exc.status in (429, 503):     # Oracle explicitly did not process the request
                self._failed(row, info, "", f"{exc} {exc.detail}".strip())
            else:                            # 500/502/504: Oracle may have started the job
                self._unknown(row, info, "", f"{exc} {exc.detail}".strip())
            return
        except BadRequestError as exc:
            self._failed_or_exists(row, info, "", f"{exc.detail or exc}")
            return
        except UnexpectedResponseError as exc:
            if exc.status is not None and 200 <= exc.status < 300:
                # Oracle accepted the request but the answer is unreadable: the job may be running.
                self._unknown(row, info, "", f"{exc} Oracle accepted the request.")
            else:
                self._failed(row, info, "", str(exc))
            return
        except OracleError as exc:
            self._failed(row, info, "", str(exc))
            return

        if not job.job_id and job.status_url is None:
            self._unknown(row, info, "", "Oracle accepted the request but returned no job to follow.")
            return
        self._wait_for_job(row, job, info, position, total)

    def _wait_for_job(self, row: PlannedSchedule, job: Job, info: dict, position: int, total: int) -> None:
        deadline = self._clock() + self.job_timeout
        delay = self.poll_initial
        info = {**info, "status_url": job.status_url or ""}
        while job.running:
            if self._clock() >= deadline:
                self._unknown(row, info, job.job_id,
                              f"Oracle job {job.job_id} did not finish within {self.job_timeout / 60:.0f} minutes.")
                return
            self._progress(row, position, total, f"Waiting for Oracle job {job.job_id}")
            self._sleep(delay)
            delay = min(delay * 1.5, self.poll_max)
            try:
                job = self.client.get_job(job)
            except (AuthenticationError, PermissionDeniedError):
                self._unknown(row, info, job.job_id, f"Oracle refused the status request for job {job.job_id} "
                              "(credentials or permissions changed while it was running).")
                raise
            except OracleError as exc:
                self._unknown(row, info, job.job_id, f"Could not read the status of Oracle job {job.job_id}: {exc}")
                return

        if job.succeeded:
            self.ledger.record(row.name, LedgerStatus.COMPLETED, job_id=job.job_id, details=job.message, **info)
            self._set(row, ScheduleStatus.COMPLETED, f"Oracle job {job.job_id} completed." +
                      (f" {job.message}" if job.message else ""))
        elif job.status is None:
            self._unknown(row, info, job.job_id, f"Oracle job {job.job_id} returned an unknown status.")
        else:
            self._failed_or_exists(row, info, job.job_id, job.message or f"Oracle job {job.job_id} failed.")

    # ------------------------------------------------------------------ outcomes
    def _failed_or_exists(self, row: PlannedSchedule, info: dict, job_id: str, message: str) -> None:
        if any(p.search(message) for p in self._exists_patterns):
            self.ledger.record(row.name, LedgerStatus.EXISTS, job_id=job_id, details=message, **info)
            self._set(row, ScheduleStatus.SKIPPED_EXISTS, f"Oracle reports the schedule already exists: {message}")
            return
        self._failed(row, info, job_id, message)
        if any(p.search(message) for p in self._template_patterns):
            raise TemplateNotFoundError(f"Template '{self.template_name}' was not found in Oracle: {message}")

    def _failed(self, row: PlannedSchedule, info: dict, job_id: str, message: str) -> None:
        self.ledger.record(row.name, LedgerStatus.FAILED, job_id=job_id, details=message, **info)
        self._set(row, ScheduleStatus.FAILED, message)

    def _unknown(self, row: PlannedSchedule, info: dict, job_id: str, message: str) -> None:
        self.ledger.record(row.name, LedgerStatus.UNKNOWN, job_id=job_id, details=message, **info)
        self._set(row, ScheduleStatus.UNKNOWN, message + " Check the schedule in Oracle before retrying.")

    def _set(self, row: PlannedSchedule, status: ScheduleStatus, message: str | None) -> None:
        row.status = status
        if message:
            row.messages = [message]
        elif status in (ScheduleStatus.PENDING, ScheduleStatus.VALIDATING, ScheduleStatus.DEPLOYING):
            row.messages = [m for m in row.messages if m.startswith("Warning:")]
        self.on_event(DeployEvent("row", row=row))

    def _progress(self, row: PlannedSchedule, position: int, total: int, operation: str) -> None:
        self.on_event(DeployEvent("progress", row=row, position=position, total=total, operation=operation))
