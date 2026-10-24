"""Local record of every deployment attempt, per environment (%APPDATA%\\EPMTaskDeployer\\ledger\\<env>.json).

Oracle has no documented API that lists schedules, so this ledger is what guarantees that a retry never
re-creates a schedule this application already created.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from app.utils.paths import app_data_dir

log = logging.getLogger(__name__)


class LedgerStatus(StrEnum):
    COMPLETED = "completed"   # Oracle job finished successfully
    EXISTS = "exists"         # Oracle reported the schedule already exists, or the user confirmed it does
    FAILED = "failed"         # Oracle job failed or the request was refused -> may be retried
    UNKNOWN = "unknown"       # outcome not known (timeout, lost connection) -> user must check Oracle first


BLOCKING_STATUSES = (LedgerStatus.COMPLETED, LedgerStatus.EXISTS, LedgerStatus.UNKNOWN)


@dataclass
class LedgerEntry:
    schedule_name: str
    status: str
    template_name: str = ""
    year: str = ""
    period: str = ""
    day_zero: str = ""
    org_unit: str = ""
    job_id: str = ""
    status_url: str = ""      # Oracle "Job Status" link, lets the app re-check an unknown outcome later
    details: str = ""
    updated_at: str = ""
    attempts: int = 0


class DeploymentLedger:
    def __init__(self, environment_id: str, path: Path | None = None):
        self.environment_id = environment_id
        self.path = path or app_data_dir() / "ledger" / f"{environment_id}.json"
        self._entries: dict[str, LedgerEntry] = {}
        self.load_error: str | None = None
        self._load()

    @staticmethod
    def _key(name: str) -> str:
        return name.strip().casefold()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            known = {f.name for f in fields(LedgerEntry)}
            for raw in data.get("entries", []):
                entry = LedgerEntry(**{k: v for k, v in raw.items() if k in known})
                self._entries[self._key(entry.schedule_name)] = entry
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            backup = self.path.with_name(f"{self.path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
            os.replace(self.path, backup)
            self._entries.clear()
            log.error("Deployment ledger %s unreadable (%s); moved to %s", self.path, exc, backup)
            self.load_error = (f"The local deployment history could not be read and was moved to {backup.name}. "
                               "Schedules deployed earlier are no longer protected against re-deployment; "
                               "check Oracle before deploying.")

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "environment_id": self.environment_id,
                   "entries": [asdict(e) for e in self._entries.values()]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def get(self, schedule_name: str) -> LedgerEntry | None:
        return self._entries.get(self._key(schedule_name))

    def entries(self) -> list[LedgerEntry]:
        return sorted(self._entries.values(), key=lambda e: e.updated_at, reverse=True)

    def with_status(self, *statuses: LedgerStatus) -> list[LedgerEntry]:
        return [e for e in self._entries.values() if e.status in statuses]

    def record(self, schedule_name: str, status: LedgerStatus, **info) -> LedgerEntry:
        previous = self.get(schedule_name)
        entry = LedgerEntry(schedule_name=schedule_name, status=str(status),
                            attempts=(previous.attempts if previous else 0) + 1, **info)
        entry.updated_at = datetime.now().isoformat(timespec="seconds")
        self._entries[self._key(schedule_name)] = entry
        self._write()
        return entry

    def resolve_unknown(self, schedule_name: str, exists_in_oracle: bool) -> None:
        """User checked Oracle for a schedule whose outcome was unknown."""
        entry = self.get(schedule_name)
        if entry is None or entry.status != LedgerStatus.UNKNOWN:
            return
        entry.status = str(LedgerStatus.EXISTS if exists_in_oracle else LedgerStatus.FAILED)
        entry.details = ("Confirmed by user: exists in Oracle." if exists_in_oracle
                         else "Confirmed by user: not in Oracle; may be deployed again.")
        entry.updated_at = datetime.now().isoformat(timespec="seconds")
        self._write()
