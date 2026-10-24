"""OracleClient: HTTP contract, status mapping, retries, secret hygiene. Fake transport only (no network)."""

import base64
import json
import logging
from datetime import date

import pytest

from app.services.oracle_client import (
    AuthenticationError, BadRequestError, Job, NetworkError, NotFoundError, OracleClient, OracleError,
    PermissionDeniedError, ServiceUnavailableError, UnexpectedResponseError,
)
from tests.oracle_fakes import BASE, JOBS_PATH, PASSWORD, USER, ScriptedTransport, job_body, resp


def make_client(*items, retries=3, base_url=BASE, username=USER, password=PASSWORD):
    transport = ScriptedTransport(*items)
    sleeps: list[float] = []
    client = OracleClient(base_url, username, password, transport=transport, retries=retries,
                          sleep=sleeps.append, timeout=12.5)
    return client, transport, sleeps


def expected_auth(user=USER, password=PASSWORD):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


def deploy(client, **overrides):
    args = dict(template_name="Monthly Financial Close", schedule_name="Monthly Close - January 2027",
                year="FY27", period="Jan", day_zero=date(2027, 1, 31), org_unit="")
    args.update(overrides)
    return client.deploy_template(**args)


# --------------------------------------------------------------------------- headers / URL
class TestHeadersAndUrl:
    def test_basic_header_is_base64_of_user_colon_password(self):
        client, t, _ = make_client(resp(200, {"items": []}), username="john", password="pass")
        client.get_rest_versions()
        assert t.calls[0].headers["Authorization"] == "Basic am9objpwYXNz"  # base64("john:pass")

    def test_basic_header_non_ascii_password_utf8(self):
        client, t, _ = make_client(resp(200, {"items": []}))
        client.get_rest_versions()
        header = t.calls[0].headers["Authorization"]
        assert header == expected_auth()
        assert base64.b64decode(header[6:]).decode("utf-8") == f"{USER}:{PASSWORD}"

    def test_get_sends_accept_json_and_no_body(self):
        client, t, _ = make_client(resp(200, {"items": []}))
        client.get_rest_versions()
        call = t.calls[0]
        assert call.method == "GET"
        assert call.headers["Accept"] == "application/json"
        assert "Content-Type" not in call.headers
        assert call.body is None
        assert call.timeout == 12.5

    def test_post_sends_content_type_json(self):
        client, t, _ = make_client(resp(200, job_body("1")))
        deploy(client)
        call = t.calls[0]
        assert call.headers["Content-Type"] == "application/json"
        assert call.headers["Accept"] == "application/json"
        assert call.headers["Authorization"] == expected_auth()

    @pytest.mark.parametrize("given", [
        BASE, BASE + "/", BASE + "/epmcloud", BASE + "/HyperionPlanning/rest/v3?x=1#frag",
    ])
    def test_base_url_normalised_to_scheme_and_host(self, given):
        client, t, _ = make_client(resp(200, {"items": []}), base_url=given)
        assert client.base_url == BASE
        client.get_rest_versions()
        assert t.calls[0].url == BASE + "/HyperionPlanning/rest/"


# --------------------------------------------------------------------------- read API
class TestReadApi:
    def test_get_rest_versions(self):
        body = {"items": [{"version": "v3", "lifecycle": "active"}, {"version": "11.1.2.3.600"}, {"other": 1}, "x"]}
        client, t, _ = make_client(resp(200, body))
        assert client.get_rest_versions() == ["v3", "11.1.2.3.600"]
        assert t.calls[0].url == f"{BASE}/HyperionPlanning/rest/"

    def test_list_applications(self):
        body = {"items": [{"name": "FCCS"}, {"name": "Plan2"}, {"type": "x"}]}
        client, t, _ = make_client(resp(200, body))
        assert client.list_applications() == ["FCCS", "Plan2"]
        assert t.calls[0].url == f"{BASE}/HyperionPlanning/rest/v3/applications"

    def test_list_applications_tolerates_unexpected_json(self):
        client, _, _ = make_client(resp(200, [1, 2]))
        assert client.list_applications() == []

    def test_test_connection_ok(self):
        client, t, _ = make_client(resp(200, {"items": [{"version": "v3"}]}), resp(200, {"items": [{"name": "FCCS"}]}))
        report = client.test_connection()
        assert report.rest_versions == ["v3"]
        assert report.applications == ["FCCS"]
        assert report.applications_note == ""
        assert all(c.method == "GET" for c in t.calls)

    def test_test_connection_403_on_applications_gives_none_and_note(self):
        client, t, _ = make_client(resp(200, {"items": [{"version": "v3"}]}), resp(403, {"detail": "denied"}))
        report = client.test_connection()
        assert report.rest_versions == ["v3"]
        assert report.applications is None
        assert "may not list applications" in report.applications_note
        assert len(t.calls) == 2  # 403 is not retried

    def test_test_connection_401_raises(self):
        client, _, _ = make_client(resp(401))
        with pytest.raises(AuthenticationError):
            client.test_connection()

    def test_get_member_200_returns_dict(self):
        member = {"name": "FY27", "parentName": "Years"}
        client, t, _ = make_client(resp(200, member))
        assert client.get_member("FCCS", "Years", "FY27") == member
        assert t.calls[0].url == f"{BASE}/HyperionPlanning/rest/v3/applications/FCCS/dimensions/Years/members/FY27"

    def test_get_member_404_returns_none_without_retry(self):
        client, t, sleeps = make_client(resp(404, {"detail": "Member not found"}))
        assert client.get_member("FCCS", "Years", "FY99") is None
        assert len(t.calls) == 1 and sleeps == []

    def test_get_member_non_dict_json_returns_empty_dict(self):
        client, _, _ = make_client(resp(200, ["x"]))
        assert client.get_member("FCCS", "Years", "FY27") == {}

    def test_get_member_url_encodes_every_segment(self):
        client, t, _ = make_client(resp(200, {"name": "x"}))
        client.get_member("My App", "Entity Dim", "North America")
        assert t.calls[0].url.endswith("/applications/My%20App/dimensions/Entity%20Dim/members/North%20America")
        client2, t2, _ = make_client(resp(200, {"name": "x"}))
        client2.get_member("FCCS", "Years", "FY27/28")
        assert t2.calls[0].url.endswith("/dimensions/Years/members/FY27%2F28")
        client3, t3, _ = make_client(resp(200, {"name": "x"}))
        client3.get_member("FCCS", "Period", "Q1?#&%")
        assert t3.calls[0].url.endswith("/members/Q1%3F%23%26%25")

    def test_get_member_other_errors_propagate(self):
        client, _, _ = make_client(resp(403))
        with pytest.raises(PermissionDeniedError):
            client.get_member("FCCS", "Years", "FY27")


# --------------------------------------------------------------------------- deploy
class TestDeploy:
    def test_exact_payload_without_org_unit(self):
        client, t, _ = make_client(resp(200, job_body("42")))
        deploy(client)
        call = t.calls[0]
        assert call.method == "POST"
        assert call.url == BASE + JOBS_PATH
        assert call.payload == {
            "jobType": "TM_DEPLOY_TEMPLATE",
            "parameters": {
                "templateName": "Monthly Financial Close",
                "scheduleName": "Monthly Close - January 2027",
                "year": "FY27",
                "period": "Jan",
                "dayZeroDate": "2027-01-31",
            },
        }
        assert "dateFormat" not in call.payload["parameters"]

    def test_org_unit_sent_only_when_non_empty(self):
        client, t, _ = make_client(resp(200, job_body("42")))
        deploy(client, org_unit="Corporate", day_zero=date(2028, 2, 29), schedule_name="Clôture – février")
        params = t.calls[0].payload["parameters"]
        assert params["orgUnit"] == "Corporate"
        assert params["dayZeroDate"] == "2028-02-29"
        assert params["scheduleName"] == "Clôture – février"
        assert "dateFormat" not in params

    def test_job_parsed_from_response(self):
        client, _, _ = make_client(resp(200, job_body("42", -1, details="Queued", descriptive="In Progress")))
        job = deploy(client)
        assert job.job_id == "42" and job.status == -1 and job.running
        assert not job.succeeded and not job.failed
        assert job.status_url == f"{BASE}{JOBS_PATH}/42"
        assert job.message == "In Progress — Queued"

    @pytest.mark.parametrize("raw,expected", [(-1, -1), (0, 0), (1, 1), ("-1", -1), ("0", 0), ("1", 1),
                                              (None, None), ("Processing", None), ([], None)])
    def test_job_status_int_str_none(self, raw, expected):
        body = {"jobId": 7, "status": raw}
        client, _, _ = make_client(resp(200, body))
        job = deploy(client)
        assert job.status == expected
        assert job.job_id == "7"

    def test_job_status_link_rel_case_insensitive_and_preferred_over_location(self):
        body = {"jobId": "9", "status": -1, "links": [
            {"rel": "self", "href": f"{BASE}/self"},
            {"rel": "JOB STATUS", "href": f"{BASE}{JOBS_PATH}/9"},
        ]}
        client, _, _ = make_client(resp(200, body, headers={"Location": f"{BASE}/other"}))
        assert deploy(client).status_url == f"{BASE}{JOBS_PATH}/9"

    def test_location_header_fallback(self):
        client, _, _ = make_client(resp(201, {"jobId": "9", "status": -1},
                                        headers={"location": f"{BASE}{JOBS_PATH}/9"}))
        assert deploy(client).status_url == f"{BASE}{JOBS_PATH}/9"

    def test_no_link_no_location(self):
        client, _, _ = make_client(resp(200, {"status": -1}))
        job = deploy(client)
        assert job.status_url is None and job.job_id == ""

    def test_details_masked_and_clipped(self):
        details = "Failed: password=hunter2 " + "x" * 1000
        client, _, _ = make_client(resp(200, {"jobId": "1", "status": 1, "details": details,
                                              "descriptiveStatus": "Error"}))
        job = deploy(client)
        assert "hunter2" not in job.details
        assert len(job.details) == 601 and job.details.endswith("…")
        assert job.failed and job.message.startswith("Error — Failed:")

    def test_message_deduplicates_identical_parts(self):
        assert Job("1", 1, details="Error", descriptive_status="Error").message == "Error"
        assert Job("1", 0).message == ""

    @pytest.mark.parametrize("body", [b"", b"not json", b"[1,2]"])
    def test_unreadable_job_response(self, body):
        client, _, _ = make_client(resp(200, body))
        with pytest.raises(UnexpectedResponseError):
            deploy(client)


# --------------------------------------------------------------------------- get_job
class TestGetJob:
    def test_uses_status_link(self):
        link = f"{BASE}/HyperionPlanning/rest/cmapi/v1/jobs/42?x=1"
        client, t, _ = make_client(resp(200, {"jobId": "42", "status": 0}))
        refreshed = client.get_job(Job("42", -1, status_url=link))
        assert t.calls[0].method == "GET" and t.calls[0].url == link
        assert refreshed.succeeded
        assert refreshed.status_url == link  # kept when the poll response has no link

    def test_falls_back_to_jobs_path_and_keeps_job_id(self):
        client, t, _ = make_client(resp(200, {"status": -1}))
        refreshed = client.get_job(Job("A 1", -1))
        assert t.calls[0].url == f"{BASE}{JOBS_PATH}/A%201"
        assert refreshed.job_id == "A 1"

    def test_relative_link_resolved_against_base(self):
        client, t, _ = make_client(resp(200, {"jobId": "5", "status": 0}))
        client.get_job(Job("5", -1, status_url=f"{JOBS_PATH}/5"))
        assert t.calls[0].url == f"{BASE}{JOBS_PATH}/5"

    def test_host_comparison_case_insensitive(self):
        client, t, _ = make_client(resp(200, {"jobId": "5", "status": 0}))
        upper = BASE.replace("fccs-test", "FCCS-TEST").replace("https", "HTTPS")
        client.get_job(Job("5", -1, status_url=f"{upper}{JOBS_PATH}/5"))
        assert len(t.calls) == 1

    @pytest.mark.parametrize("link", [
        "https://evil.example.com/HyperionPlanning/rest/cmapi/v1/jobs/42",
        BASE.replace("https://", "http://") + JOBS_PATH + "/42",            # scheme downgrade
        BASE + ":8443" + JOBS_PATH + "/42",                                 # other port
        "https://fccs-test.epm.us-phoenix-1.ocs.oraclecloud.com.evil.io/jobs/42",
        "https://user@evil.example.com/jobs/42",
    ])
    def test_foreign_link_rejected_and_not_sent(self, link):
        client, t, _ = make_client()
        with pytest.raises(UnexpectedResponseError) as err:
            client.get_job(Job("42", -1, status_url=link))
        assert t.calls == []
        assert "not sent" in str(err.value)


# --------------------------------------------------------------------------- status mapping
class TestStatusMapping:
    @pytest.mark.parametrize("status,exc", [
        (401, AuthenticationError), (403, PermissionDeniedError), (404, NotFoundError),
        (400, BadRequestError), (409, BadRequestError), (422, BadRequestError),
        (500, ServiceUnavailableError), (301, UnexpectedResponseError), (302, UnexpectedResponseError),
        (307, UnexpectedResponseError),
    ])
    def test_get_status_mapping(self, status, exc):
        client, t, _ = make_client(resp(status, {"detail": "d"}))
        with pytest.raises(exc) as err:
            client.list_applications()
        assert err.value.status == status
        assert isinstance(err.value, OracleError)
        assert len(t.calls) == 1  # none of these is retried

    def test_503_after_retries_is_service_unavailable(self):
        client, t, _ = make_client(*[resp(503)] * 4)
        with pytest.raises(ServiceUnavailableError) as err:
            client.list_applications()
        assert err.value.status == 503
        assert len(t.calls) == 4

    def test_html_200_is_unexpected(self):
        client, _, _ = make_client(resp(200, "<html><body>Sign in</body></html>", content_type="text/html; charset=utf-8"))
        with pytest.raises(UnexpectedResponseError, match="web page"):
            client.list_applications()

    def test_empty_html_200_is_accepted(self):
        client, _, _ = make_client(resp(200, b"", content_type="text/html"))
        assert client.list_applications() == []

    def test_redirect_message_mentions_sso(self):
        client, _, _ = make_client(resp(302, b"", headers={"Location": "https://login.example.com"}))
        with pytest.raises(UnexpectedResponseError, match="single sign-on"):
            client.get_rest_versions()

    def test_bad_request_message_includes_detail(self):
        client, _, _ = make_client(resp(400, {"detail": "Invalid period ZZZ"}))
        with pytest.raises(BadRequestError) as err:
            deploy(client)
        assert err.value.detail == "Invalid period ZZZ"
        assert "Invalid period ZZZ" in str(err.value)
        assert "HTTP 400" in str(err.value)

    @pytest.mark.parametrize("body,detail", [
        ({"detail": "A"}, "A"),
        ({"details": "B"}, "B"),
        ({"message": "C"}, "C"),
        ({"error": "E"}, "E"),
        ({"errorMessage": "F"}, "F"),
        ({"title": "T"}, "T"),
        ({"detail": "first", "details": "second", "message": "third"}, "first"),
        ({"detail": "", "message": "fallback"}, "fallback"),
        ("plain text error", "plain text error"),
        ("<HTML><body>error page</body></html>", ""),
        (b"", ""),
    ])
    def test_error_detail_extraction(self, body, detail):
        client, _, _ = make_client(resp(400, body))
        with pytest.raises(BadRequestError) as err:
            client.list_applications()
        assert err.value.detail == detail


# --------------------------------------------------------------------------- retries
class TestRetries:
    @pytest.mark.parametrize("status", [503, 502, 504, 429])
    def test_get_retried_then_succeeds(self, status):
        client, t, sleeps = make_client(resp(status), resp(status), resp(200, {"items": [{"name": "FCCS"}]}))
        assert client.list_applications() == ["FCCS"]
        assert len(t.calls) == 3
        assert sleeps == [2.0, 4.0]  # exponential backoff

    @pytest.mark.parametrize("status", [503, 502, 504, 429])
    def test_get_gives_up_after_retries(self, status):
        client, t, sleeps = make_client(*[resp(status)] * 3, retries=2)
        with pytest.raises(ServiceUnavailableError):
            client.list_applications()
        assert len(t.calls) == 3 and sleeps == [2.0, 4.0]

    def test_backoff_is_exponential_and_capped_at_30(self):
        client, t, sleeps = make_client(*[resp(503)] * 7, retries=6)
        with pytest.raises(ServiceUnavailableError):
            client.get_rest_versions()
        assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

    @pytest.mark.parametrize("retry_after,expected", [("5", 5.0), (" 7 ", 7.0), ("0", 0.0), ("120", 60.0),
                                                      ("60", 60.0), ("Wed, 21 Oct 2026 07:28:00 GMT", 2.0),
                                                      ("1.5", 2.0), ("-3", 2.0)])
    def test_retry_after_honoured_and_capped(self, retry_after, expected):
        client, _, sleeps = make_client(resp(503, headers={"Retry-After": retry_after}), resp(200, {"items": []}))
        client.get_rest_versions()
        assert sleeps == [expected]

    def test_retry_after_header_name_case_insensitive(self):
        client, _, sleeps = make_client(resp(429, headers={"retry-after": "9"}), resp(200, {"items": []}))
        client.get_rest_versions()
        assert sleeps == [9.0]

    @pytest.mark.parametrize("reached", [True, False])
    def test_get_retried_on_network_error(self, reached):
        client, t, sleeps = make_client(NetworkError("boom", may_have_reached_server=reached),
                                        resp(200, {"items": [{"version": "v3"}]}))
        assert client.get_rest_versions() == ["v3"]
        assert len(t.calls) == 2 and sleeps == [2.0]

    def test_get_network_error_after_retries_reraised(self):
        errors = [NetworkError(f"boom{i}", may_have_reached_server=True) for i in range(4)]
        client, t, _ = make_client(*errors)
        with pytest.raises(NetworkError) as err:
            client.get_rest_versions()
        assert err.value is errors[-1]
        assert len(t.calls) == 4

    def test_retries_zero_means_single_attempt(self):
        client, t, sleeps = make_client(resp(503), retries=0)
        with pytest.raises(ServiceUnavailableError):
            client.get_rest_versions()
        assert len(t.calls) == 1 and sleeps == []

    @pytest.mark.parametrize("status", [429, 503])
    def test_post_retried_on_429_503(self, status):
        client, t, sleeps = make_client(resp(status, headers={"Retry-After": "3"}), resp(200, job_body("1")))
        assert deploy(client).job_id == "1"
        assert [c.method for c in t.calls] == ["POST", "POST"]
        assert t.calls[0].body == t.calls[1].body
        assert sleeps == [3.0]

    def test_post_429_after_retries(self):
        client, t, _ = make_client(*[resp(429)] * 4)
        with pytest.raises(ServiceUnavailableError) as err:
            deploy(client)
        assert err.value.status == 429 and len(t.calls) == 4

    def test_post_retried_on_network_error_not_reached(self):
        client, t, sleeps = make_client(NetworkError("dns", may_have_reached_server=False), resp(200, job_body("1")))
        assert deploy(client).job_id == "1"
        assert len(t.calls) == 2 and sleeps == [2.0]

    def test_post_not_retried_on_network_error_maybe_reached(self):
        client, t, sleeps = make_client(NetworkError("reset", may_have_reached_server=True))
        with pytest.raises(NetworkError) as err:
            deploy(client)
        assert err.value.may_have_reached_server is True
        assert len(t.calls) == 1 and sleeps == []

    def test_post_network_error_reached_after_unreached_retry_stops(self):
        client, t, _ = make_client(NetworkError("dns", may_have_reached_server=False),
                                   NetworkError("timeout", may_have_reached_server=True))
        with pytest.raises(NetworkError):
            deploy(client)
        assert len(t.calls) == 2

    @pytest.mark.parametrize("status", [500, 502, 504])
    def test_post_not_retried_on_500_502_504(self, status):
        client, t, sleeps = make_client(resp(status, {"detail": "boom"}))
        with pytest.raises(ServiceUnavailableError) as err:
            deploy(client)
        assert err.value.status == status and err.value.detail == "boom"
        assert len(t.calls) == 1 and sleeps == []


# --------------------------------------------------------------------------- secrets
class TestSecrets:
    def _token(self):
        return expected_auth().split(" ", 1)[1]

    def test_secrets_never_in_logs_or_exceptions(self, caplog):
        caplog.set_level(logging.DEBUG)
        token = self._token()
        echo = {"detail": f"Bad header Authorization: Basic {token} password={PASSWORD}"}
        client, _, _ = make_client(
            resp(503), NetworkError("reset", may_have_reached_server=True), resp(200, {"items": []}),   # GET
            resp(400, echo),                                                                         # POST
            resp(401, echo),
            resp(200, {"jobId": "1", "status": 1, "details": f"password={PASSWORD}"}),
        )
        client.get_rest_versions()
        texts = []
        for _ in range(2):
            with pytest.raises(OracleError) as err:
                deploy(client)
            texts += [str(err.value), err.value.detail, repr(err.value)]
        job = deploy(client)
        texts += [job.details, job.message, repr(job)]
        texts.append(caplog.text)
        texts.extend(r.getMessage() for r in caplog.records)
        joined = "\n".join(texts)
        assert caplog.records, "requests should be logged"
        assert token not in joined
        assert PASSWORD not in joined
        assert "Authorization" not in caplog.text
        assert "Basic " not in caplog.text

    def test_client_repr_does_not_expose_password(self):
        client, _, _ = make_client()
        assert PASSWORD not in repr(client) and self._token() not in repr(client)
        assert PASSWORD not in json.dumps({k: str(v) for k, v in vars(client).items() if k != "_auth_header"})


# --------------------------------------------------------------------------- confirmed on a real FCCS test pod
REAL_POD_MISSING_MEMBER = ("The member P05 does not exist for the specified cube or you do not have access to it.")


def test_get_member_400_member_does_not_exist_is_missing():
    """Real FCCS answers HTTP 400 (not 404) for a missing member (test pod, 2026-10-23)."""
    client, _, _ = make_client(resp(400, {"detail": REAL_POD_MISSING_MEMBER}))
    assert client.get_member("FCCS", "Period", "P05") is None


def test_get_member_other_400_still_raises():
    client, _, _ = make_client(resp(400, {"detail": "Invalid dimension name"}))
    with pytest.raises(BadRequestError):
        client.get_member("FCCS", "Peroid", "Jun")
