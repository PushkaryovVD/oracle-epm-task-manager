"""Development-only fake Oracle EPM REST server, as a Transport function for OracleClient.

Never shipped with the application. Use it to try the UI without a real Oracle pod:

    .venv\\Scripts\\python.exe tools\\fake_oracle.py            # starts the app against the fake

Data and the password are kept in a separate EPMTD_HOME (set it before starting) and an in-memory credential
store, so nothing reaches %APPDATA% or Windows Credential Manager unless you choose so.

Behaviour per schedule is chosen by a fragment of the schedule name (case-insensitive), e.g.
    FakeOracle(behaviours={"March": "fail", "April": "exists", "June": "unknown", "May": "slow"})
Behaviours:
    ok        job accepted with status -1, then 0 after `polls` status requests (default)
    fail      job accepted with status -1, then 1 (Oracle "details" message)
    exists    HTTP 400 "Schedule ... already exists"
    unknown   HTTP 500 on the deploy request (Oracle may have started the job -> Unknown)
    slow      job stays -1 for `slow_polls` status requests; every request takes `slow_delay` seconds
    template  job fails with "Template ... not found"
    auth      HTTP 401 on the deploy request
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

if __name__ == "__main__":  # started as a script: make the project importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.oracle_client import HttpResponse, OracleClient

API = "/HyperionPlanning/rest"


@dataclass
class _Job:
    job_id: int
    schedule: str
    behaviour: str
    polls_left: int


@dataclass
class FakeOracle:
    password: str = "secret"
    applications: list[str] = field(default_factory=lambda: ["FCCS1"])
    missing_members: set[str] = field(default_factory=set)     # Get Member answers 404 for these
    behaviours: dict[str, str] = field(default_factory=dict)  # schedule-name fragment -> behaviour
    polls: int = 1
    slow_polls: int = 4
    slow_delay: float = 0.8
    latency: float = 0.05
    forbid_applications: bool = False                           # 403 on Get Applications
    requests: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self):
        self._jobs: dict[int, _Job] = {}
        self._next_id = 14001
        self._lock = threading.Lock()

    # ---- helpers
    @staticmethod
    def _json(status: int, data: object) -> HttpResponse:
        return HttpResponse(status, {"Content-Type": "application/json"}, json.dumps(data).encode("utf-8"))

    def _authorized(self, headers: dict[str, str]) -> bool:
        value = headers.get("Authorization", "")
        if not value.startswith("Basic "):
            return False
        _user, _, password = base64.b64decode(value[6:]).decode("utf-8").partition(":")
        return password == self.password

    def _behaviour(self, schedule: str) -> str:
        for fragment, behaviour in self.behaviours.items():
            if fragment.casefold() in schedule.casefold():
                return behaviour
        return "ok"

    def _job_body(self, base: str, job: _Job, status: int, details: str = "") -> dict:
        return {
            "jobId": job.job_id, "status": status, "details": details,
            "descriptiveStatus": {-1: "In Progress", 0: "Completed", 1: "Error"}.get(status, ""),
            "links": [{"rel": "Job Status", "href": f"{base}{API}/cmapi/v1/jobs/{job.job_id}"}],
        }

    # ---- transport
    def transport(self, method: str, url: str, headers: dict[str, str], body: bytes | None,
                  timeout: float) -> HttpResponse:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        path = unquote(parts.path)
        with self._lock:
            self.requests.append((method, path))
        time.sleep(self.latency)
        if not self._authorized(headers):
            return self._json(401, {"detail": "Unauthorized"})

        if method == "GET" and path.rstrip("/") == API:
            return self._json(200, {"items": [{"version": "v3", "lifecycle": "active"}]})
        if method == "GET" and path == f"{API}/v3/applications":
            if self.forbid_applications:
                return self._json(403, {"detail": "Forbidden"})
            return self._json(200, {"items": [{"name": a, "type": "FCCS"} for a in self.applications]})
        if method == "GET" and "/dimensions/" in path and "/members/" in path:
            member = path.rsplit("/members/", 1)[1]
            if member in self.missing_members:
                return self._json(404, {"detail": f"Member {member} not found"})
            return self._json(200, {"name": member})
        if method == "POST" and path == f"{API}/cmapi/v1/jobs":
            payload = json.loads(body or b"{}")
            params = payload.get("parameters", {})
            schedule = params.get("scheduleName", "")
            behaviour = self._behaviour(schedule)
            if behaviour == "exists":
                return self._json(400, {"detail": f"Schedule {schedule} already exists."})
            if behaviour == "unknown":
                return self._json(500, {"detail": "Internal server error"})
            if behaviour == "auth":
                return self._json(401, {"detail": "Unauthorized"})
            with self._lock:
                job = _Job(self._next_id, schedule, behaviour,
                           self.slow_polls if behaviour == "slow" else self.polls)
                self._next_id += 1
                self._jobs[job.job_id] = job
            return self._json(200, self._job_body(base, job, -1))
        if method == "GET" and path.startswith(f"{API}/cmapi/v1/jobs/"):
            job = self._jobs.get(int(path.rsplit("/", 1)[1]))
            if job is None:
                return self._json(404, {"detail": "Job not found"})
            if job.behaviour == "slow":
                time.sleep(self.slow_delay)
            if job.polls_left > 0:
                job.polls_left -= 1
                return self._json(200, self._job_body(base, job, -1))
            if job.behaviour == "fail":
                return self._json(200, self._job_body(base, job, 1, "Period is locked for task scheduling."))
            if job.behaviour == "template":
                return self._json(200, self._job_body(base, job, 1, "Template not found."))
            return self._json(200, self._job_body(base, job, 0, f"Schedule {job.schedule} deployed."))
        return self._json(404, {"detail": f"No fake route for {method} {path}"})

    def client_factory(self):
        """A MainWindow client_factory that sends every request to this fake (no retry sleeps)."""
        def factory(base_url: str, username: str, password: str) -> OracleClient:
            return OracleClient(base_url, username, password, transport=self.transport, sleep=lambda _s: None)
        return factory


def main() -> None:
    """Start the application against the fake Oracle (password: 'secret')."""
    import os
    import tempfile

    os.environ.setdefault("EPMTD_HOME", str(Path(tempfile.gettempdir()) / "EPMTaskDeployer-fake-oracle"))
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    from app.services.credential_store import CredentialStore
    from app.ui.main_window import MainWindow
    from app.utils.logging_setup import configure_logging

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

    configure_logging()
    fake = FakeOracle(missing_members={"FY30"}, behaviours={"March": "fail", "April": "exists", "June": "unknown",
                                                            "May": "slow"})
    MainWindow(cred_store=CredentialStore(MemoryKeyring()), client_factory=fake.client_factory(),
               deploy_options={"poll_initial": 0.5, "poll_max": 1.0}).mainloop()


if __name__ == "__main__":
    main()
