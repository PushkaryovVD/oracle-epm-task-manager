"""UI-side helpers around the Oracle client: client factory type, connection-report text and a thin wrapper
that remembers whether Oracle rejected the credentials during a member check.

No Tk code here: everything in this module may run in a worker thread.
"""

from __future__ import annotations

from typing import Callable

from app.services.oracle_client import AuthenticationError, ConnectionReport, OracleClient

# (base_url, username, password) -> client. Tests and tools inject a factory that uses a fake transport.
ClientFactory = Callable[[str, str, str], OracleClient]


def default_client_factory(base_url: str, username: str, password: str) -> OracleClient:
    return OracleClient(base_url, username, password)


def connection_text(report: ConnectionReport) -> str:
    versions = ", ".join(report.rest_versions) if report.rest_versions else "version not reported"
    text = f"Connected. REST API {versions}."
    if report.applications is not None:
        apps = ", ".join(report.applications) if report.applications else "none visible"
        text += f" Applications: {apps}"
    return text


class AuthWatchClient:
    """Delegates to an OracleClient and records an AuthenticationError before re-raising it.

    member_validator.check_members turns every OracleError into a text message; the UI still needs to know
    whether the credentials were rejected so it can offer "Update Password".
    """

    def __init__(self, client: OracleClient):
        self._client = client
        self.authentication_failed = False

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except AuthenticationError:
                self.authentication_failed = True
                raise
        return call
