"""Oracle EPM Cloud REST client (basic authentication).

Only documented endpoints are used (see docs/DESIGN.md §2):
- GET  /HyperionPlanning/rest/                                          REST API versions (connection check)
- GET  /HyperionPlanning/rest/{v}/applications                          Get Applications
- GET  /HyperionPlanning/rest/{v}/applications/{app}/dimensions/{dim}/members/{member}   Get Member
- POST /HyperionPlanning/rest/cmapi/{v}/jobs  jobType=TM_DEPLOY_TEMPLATE                Deploy Task Manager Templates
- GET  <"Job Status" link returned by the deploy call>                                    job status

The HTTP layer is a plain function (Transport) so tests can replace it. The default transport uses urllib:
it honours the Windows proxy settings and the Windows certificate store (corporate TLS inspection).
"""

import base64
import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Callable
from urllib.parse import quote, urlsplit

from app.utils.config import load_json
from app.utils.logging_setup import mask_secrets

log = logging.getLogger(__name__)

PLANNING_API_VERSION = "v3"
TM_API_VERSION = "v1"
_MAX_DETAIL = 600


# --------------------------------------------------------------------------- errors
class OracleError(Exception):
    """Base class. str(error) is a user-facing English message."""

    def __init__(self, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail


class AuthenticationError(OracleError):
    """401 — wrong or expired credentials. Deployment must stop."""


class PermissionDeniedError(OracleError):
    """403 — the user is not allowed to call this API."""


class NotFoundError(OracleError):
    """404 — resource URI does not exist."""


class BadRequestError(OracleError):
    """400 and other 4xx — Oracle rejected the request."""


class ServiceUnavailableError(OracleError):
    """5xx or 429 after retries."""


class UnexpectedResponseError(OracleError):
    """Redirect to a sign-in page, HTML instead of JSON, link to a foreign host..."""


class NetworkError(OracleError):
    """No HTTP response. may_have_reached_server tells whether a POST could have been processed."""

    def __init__(self, message: str, may_have_reached_server: bool):
        super().__init__(message)
        self.may_have_reached_server = may_have_reached_server


# --------------------------------------------------------------------------- transport
@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def json(self) -> object:
        try:
            return json.loads(self.body.decode("utf-8-sig") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


Transport = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: they would forward the Authorization header and usually mean an SSO login page."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler(),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    _NoRedirect(),
)


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=timeout) as resp:
            return HttpResponse(resp.status, dict(resp.headers.items()), resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() or b"")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise NetworkError("The Oracle server certificate is not trusted on this computer. "
                               "Contact your IT department.", may_have_reached_server=False) from exc
        if isinstance(reason, (socket.gaierror, ConnectionRefusedError, ssl.SSLError)):
            raise NetworkError(f"Cannot reach the Oracle environment ({reason}). Check the URL and your network "
                               "or VPN connection.", may_have_reached_server=False) from exc
        raise NetworkError(f"The connection to Oracle failed ({reason}).", may_have_reached_server=True) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise NetworkError("Oracle did not answer in time.", may_have_reached_server=True) from exc
    except ConnectionError as exc:
        raise NetworkError(f"The connection to Oracle was interrupted ({exc}).", may_have_reached_server=True) from exc


# --------------------------------------------------------------------------- results
@dataclass
class Job:
    job_id: str
    status: int | None          # -1 in progress, 0 success, 1 failure (Oracle job status codes)
    details: str = ""
    descriptive_status: str = ""
    status_url: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def running(self) -> bool:
        return self.status == -1

    @property
    def succeeded(self) -> bool:
        return self.status == 0

    @property
    def failed(self) -> bool:
        return self.status not in (-1, 0)

    @property
    def message(self) -> str:
        parts = [p for p in (self.descriptive_status, self.details) if p]
        return " — ".join(dict.fromkeys(parts))


@dataclass
class ConnectionReport:
    rest_versions: list[str]
    applications: list[str] | None   # None when the user may not list applications
    applications_note: str = ""


# --------------------------------------------------------------------------- client
class OracleClient:
    def __init__(self, base_url: str, username: str, password: str, *, transport: Transport = urllib_transport,
                 timeout: float = 60.0, retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        parts = urlsplit(base_url)
        self.base_url = f"{parts.scheme}://{parts.netloc}"
        self._origin = _origin(parts)
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._auth_header = f"Basic {token}"
        self._transport = transport
        self._timeout = timeout
        self._retries = retries
        self._sleep = sleep

    # ---- low level
    def _url(self, path_or_url: str) -> str:
        if "://" not in path_or_url:
            return self.base_url + path_or_url
        parts = urlsplit(path_or_url)
        # Oracle's job links carry an explicit ':443' (seen on a real pod): same server, different spelling.
        if parts.username is not None or parts.password is not None or _origin(parts) != self._origin:
            raise UnexpectedResponseError(f"Oracle returned a link to another server ({parts.netloc}); "
                                          "the request was not sent.")
        # Rebuild on our own base URL so only the path and query come from Oracle's answer.
        return self.base_url + (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

    def _request(self, method: str, path_or_url: str, payload: dict | None = None, *,
                 idempotent: bool = True) -> HttpResponse:
        url = self._url(path_or_url)
        headers = {"Authorization": self._auth_header, "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._transport(method, url, headers, body, self._timeout)
            except NetworkError as exc:
                # A non-idempotent call that may have reached Oracle must not be repeated (duplicate schedule).
                retryable = idempotent or not exc.may_have_reached_server
                if retryable and attempt <= self._retries:
                    log.warning("%s %s: network error (%s), retry %d", method, url, exc, attempt)
                    self._sleep(self._backoff(attempt, None))
                    continue
                raise
            if response.status in (429, 503) or (idempotent and response.status in (502, 504)):
                # 429/503: Oracle did not process the request, so even a POST can be repeated.
                if attempt <= self._retries:
                    log.warning("%s %s: HTTP %d, retry %d", method, url, response.status, attempt)
                    self._sleep(self._backoff(attempt, response.header("Retry-After")))
                    continue
            log.info("%s %s -> HTTP %d", method, url, response.status)
            return self._check(response)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.strip().isdigit():
            return min(float(retry_after), 60.0)
        return min(2.0 * 2 ** (attempt - 1), 30.0)

    def _check(self, response: HttpResponse) -> HttpResponse:
        status = response.status
        detail = _error_detail(response)
        if 200 <= status < 300:
            content_type = (response.header("Content-Type") or "").lower()
            if response.body and "html" in content_type:
                raise UnexpectedResponseError(
                    "Oracle returned a web page instead of data. The environment URL may be wrong.", status)
            return response
        if 300 <= status < 400:
            raise UnexpectedResponseError(
                "Oracle redirected the request to a sign-in page. Basic authentication may not be allowed for this "
                "user (single sign-on or multi-factor authentication). Use an Oracle user name and password that "
                "can sign in without SSO.", status)
        if status == 401:
            raise AuthenticationError(
                "Oracle rejected the user name or password. Check the credentials (Update Password). Users with "
                "multi-factor authentication cannot use the REST API.", status, detail)
        if status == 403:
            raise PermissionDeniedError(
                "Your Oracle user is not allowed to perform this operation. Oracle documents the Service "
                "Administrator role for this API.", status, detail)
        if status == 404:
            raise NotFoundError("The requested Oracle resource does not exist.", status, detail)
        if status >= 500 or status == 429:
            raise ServiceUnavailableError(
                f"Oracle is temporarily unavailable (HTTP {status}). Try again later.", status, detail)
        raise BadRequestError(f"Oracle rejected the request (HTTP {status})." + (f" {detail}" if detail else ""),
                              status, detail)

    # ---- API
    def get_rest_versions(self) -> list[str]:
        data = self._request("GET", "/HyperionPlanning/rest/").json() or {}
        items = data.get("items", []) if isinstance(data, dict) else []
        return [str(i.get("version")) for i in items if isinstance(i, dict) and i.get("version")]

    def list_applications(self) -> list[str]:
        data = self._request("GET", f"/HyperionPlanning/rest/{PLANNING_API_VERSION}/applications").json() or {}
        items = data.get("items", []) if isinstance(data, dict) else []
        return [str(i["name"]) for i in items if isinstance(i, dict) and i.get("name")]

    def test_connection(self) -> ConnectionReport:
        """Checks network, URL and credentials. Never changes anything in Oracle."""
        versions = self.get_rest_versions()
        try:
            return ConnectionReport(versions, self.list_applications())
        except PermissionDeniedError:
            return ConnectionReport(versions, None, "Signed in, but this user may not list applications "
                                                    "(Service Administrator role). Deployment may be refused.")

    def get_member(self, application: str, dimension: str, member: str) -> dict | None:
        """Member properties, or None when the member does not exist.

        Observed on a real FCCS test pod (2026-10-23): a missing member is answered with HTTP 400
        "The member P05 does not exist for the specified cube or you do not have access to it." — not 404.
        """
        path = (f"/HyperionPlanning/rest/{PLANNING_API_VERSION}/applications/{_seg(application)}"
                f"/dimensions/{_seg(dimension)}/members/{_seg(member)}")
        try:
            data = self._request("GET", path).json()
        except NotFoundError:
            return None
        except BadRequestError as exc:
            if any(p.search(exc.detail or str(exc)) for p in _member_not_found_patterns()):
                return None
            raise
        return data if isinstance(data, dict) else {}

    def deploy_template(self, template_name: str, schedule_name: str, year: str, period: str,
                        day_zero: date, org_unit: str = "") -> Job:
        """Start TM_DEPLOY_TEMPLATE. Returns the accepted job — NOT a completed deployment."""
        parameters = {
            "templateName": template_name,
            "scheduleName": schedule_name,
            "year": year,
            "period": period,
            "dayZeroDate": day_zero.isoformat(),  # Oracle default format YYYY-MM-DD; dateFormat not sent
        }
        if org_unit:
            parameters["orgUnit"] = org_unit
        payload = {"jobType": "TM_DEPLOY_TEMPLATE", "parameters": parameters}
        response = self._request("POST", f"/HyperionPlanning/rest/cmapi/{TM_API_VERSION}/jobs", payload,
                                 idempotent=False)
        return _parse_job(response)

    def get_job(self, job: Job) -> Job:
        url = job.status_url or f"/HyperionPlanning/rest/cmapi/{TM_API_VERSION}/jobs/{_seg(job.job_id)}"
        refreshed = _parse_job(self._request("GET", url))
        refreshed.status_url = refreshed.status_url or job.status_url
        refreshed.job_id = refreshed.job_id or job.job_id
        return refreshed


@lru_cache(maxsize=1)
def _member_not_found_patterns() -> tuple[re.Pattern, ...]:
    try:
        return tuple(re.compile(p, re.IGNORECASE) for p in load_json("oracle_messages.json")["member_not_found_patterns"])
    except (OSError, KeyError, ValueError, re.error):
        log.exception("config/oracle_messages.json: cannot load 'member_not_found_patterns'")
        return ()


def _seg(value: str) -> str:
    return quote(str(value), safe="")


def _origin(parts) -> tuple[str, str, int | None]:
    """(scheme, host, port) with the scheme's default port filled in, so 'host' == 'host:443' for https."""
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = -1  # never equal to a valid origin
    if port is None:
        port = {"https": 443, "http": 80}.get(scheme)
    return scheme, (parts.hostname or "").lower(), port


def _parse_job(response: HttpResponse) -> Job:
    data = response.json()
    if not isinstance(data, dict):
        raise UnexpectedResponseError("Oracle returned an unreadable job response.", response.status)
    status = data.get("status")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    status_url = None
    for link in data.get("links") or []:
        if isinstance(link, dict) and str(link.get("rel", "")).lower() == "job status":
            status_url = link.get("href")
    if status_url is None:
        status_url = response.header("Location")
    job_id = str(data.get("jobId") or data.get("jobID") or data.get("id") or "")
    if not job_id and status_url:
        job_id = urlsplit(status_url).path.rstrip("/").rsplit("/", 1)[-1]
    log.info("Oracle job response (HTTP %d): %s", response.status, _clip(json.dumps(data, default=str)))
    return Job(
        job_id=job_id,
        status=status,
        details=_clip(data.get("details")),
        descriptive_status=_clip(data.get("descriptiveStatus")),
        status_url=status_url,
        raw=data,
    )


def _clip(value: object) -> str:
    if value is None:
        return ""
    text = mask_secrets(str(value)).strip()
    return text if len(text) <= _MAX_DETAIL else text[:_MAX_DETAIL] + "…"


def _error_detail(response: HttpResponse) -> str:
    data = response.json()
    if isinstance(data, dict):
        for key in ("detail", "details", "message", "error", "errorMessage", "title"):
            if data.get(key):
                return _clip(data[key])
    if response.body and b"<html" not in response.body[:200].lower():
        return _clip(response.body.decode("utf-8", "replace"))
    return ""
