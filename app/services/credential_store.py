"""Secrets (Oracle passwords / OAuth refresh tokens) in Windows Credential Manager via keyring.

Nothing secret is ever written to disk by the application itself.
"""

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

SERVICE_PREFIX = "EPMTaskDeployer"


class CredentialStoreError(Exception):
    """Raised with a user-facing message when Windows Credential Manager cannot be used."""


class CredentialStore:
    def __init__(self, backend=None):
        # backend: any keyring backend object (tests inject an in-memory one).
        self._backend = backend or keyring.get_keyring()

    @staticmethod
    def target(environment_id: str) -> str:
        return f"{SERVICE_PREFIX}:{environment_id}"

    def save(self, environment_id: str, username: str, secret: str) -> None:
        """Store the secret, replacing whatever was stored for this environment (under any user name)."""
        if not secret:
            raise CredentialStoreError("Password must not be empty.")
        target = self.target(environment_id)
        try:
            # keyring's Windows backend copies an existing secret to '<user>@<target>' on every save;
            # clearing first keeps exactly one entry per environment in Credential Manager.
            self._clear(target, username)
            self._backend.set_password(target, username, secret)
        except KeyringError as exc:
            raise CredentialStoreError(f"Could not save the password in Windows Credential Manager: {exc}") from exc
        log.info("Credentials saved for environment '%s' (user %s)", environment_id, username)

    def _clear(self, target: str, username: str) -> None:
        try:
            self._backend.delete_password(target, username)
        except PasswordDeleteError:
            pass
        get_credential = getattr(self._backend, "get_credential", None)
        if get_credential is None:
            return
        for _ in range(10):  # entries left under other user names (e.g. after a user name change)
            existing = get_credential(target, None)
            if existing is None:
                return
            self._backend.delete_password(target, existing.username)

    def get(self, environment_id: str, username: str) -> str | None:
        try:
            return self._backend.get_password(self.target(environment_id), username)
        except KeyringError as exc:
            raise CredentialStoreError(f"Could not read the password from Windows Credential Manager: {exc}") from exc

    def has(self, environment_id: str, username: str) -> bool:
        return bool(self.get(environment_id, username))

    def delete(self, environment_id: str, username: str) -> None:
        try:
            self._backend.delete_password(self.target(environment_id), username)
        except PasswordDeleteError:
            pass  # nothing stored
        except KeyringError as exc:
            raise CredentialStoreError(f"Could not remove the password from Windows Credential Manager: {exc}") from exc
        log.info("Credentials removed for environment '%s'", environment_id)
