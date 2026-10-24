"""Test doubles for the Oracle REST layer. No network, no real time."""

import json
from dataclasses import dataclass

from app.services.oracle_client import HttpResponse

BASE = "https://fccs-test.epm.us-phoenix-1.ocs.oraclecloud.com"
JOBS_PATH = "/HyperionPlanning/rest/cmapi/v1/jobs"
USER = "svc_tm_deployer"
PASSWORD = "S3cr3t!Pässwörd-ä"


def resp(status=200, body=None, headers=None, content_type="application/json") -> HttpResponse:
    if isinstance(body, (dict, list)):
        raw = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    elif body is None:
        raw = b""
    else:
        raw = body
    hdrs = {"Content-Type": content_type} if content_type else {}
    hdrs.update(headers or {})
    return HttpResponse(status, hdrs, raw)


def job_body(job_id, status=-1, details="", descriptive="", link=True):
    body = {"jobId": job_id, "status": status, "details": details, "descriptiveStatus": descriptive}
    if link:
        body["links"] = [{"rel": "Job Status", "href": f"{BASE}{JOBS_PATH}/{job_id}"}]
    return body


@dataclass
class Call:
    method: str
    url: str
    headers: dict
    body: bytes | None
    timeout: float

    @property
    def payload(self):
        return json.loads(self.body) if self.body else None


class ScriptedTransport:
    """Returns (or raises) the queued items in order and records every call."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls: list[Call] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(Call(method, url, dict(headers), body, timeout))
        if not self.items:
            raise AssertionError(f"unexpected request {method} {url}")
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeOracle:
    """Minimal stateful Task Manager deploy endpoint.

    post_outcomes[name]: list consumed per POST of that schedule (HttpResponse / Exception / None = accept).
    post_status[name]:   status in the accepted POST response (default -1).
    polls[name]:         list consumed per job-status GET (int status, dict of job_body kwargs, HttpResponse
                         or Exception). Default: [-1, -1, 0].
    """

    def __init__(self):
        self.post_outcomes: dict[str, list] = {}
        self.post_status: dict[str, object] = {}
        self.post_body: dict[str, dict] = {}
        self.polls: dict[str, list] = {}
        self.default_polls = [-1, -1, 0]
        self.calls: list[Call] = []
        self.on_post = None
        self._jobs: dict[str, list] = {}
        self._next = 100

    def __call__(self, method, url, headers, body, timeout):
        call = Call(method, url, dict(headers), body, timeout)
        self.calls.append(call)
        if method == "POST":
            assert url == BASE + JOBS_PATH, url
            name = call.payload["parameters"]["scheduleName"]
            if self.on_post:
                self.on_post(name)
            queue = self.post_outcomes.get(name)
            if queue:
                item = queue.pop(0)
                if isinstance(item, BaseException):
                    raise item
                if item is not None:
                    return item
            self._next += 1
            job_id = str(self._next)
            self._jobs[job_id] = list(self.polls.get(name, self.default_polls))
            if name in self.post_body:
                return resp(200, self.post_body[name])
            return resp(200, job_body(job_id, self.post_status.get(name, -1)))
        assert method == "GET", method
        job_id = url.rsplit("/", 1)[-1]
        item = self._jobs[job_id].pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, HttpResponse):
            return item
        if isinstance(item, dict):
            return resp(200, job_body(job_id, **item))
        return resp(200, job_body(job_id, item))

    # ---- inspection
    @property
    def posts(self) -> list[Call]:
        return [c for c in self.calls if c.method == "POST"]

    def posted_names(self) -> list[str]:
        return [c.payload["parameters"]["scheduleName"] for c in self.posts]


class FakeClock:
    """Monotonic clock that only advances when sleep() is called."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
