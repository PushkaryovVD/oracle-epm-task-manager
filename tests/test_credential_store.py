import keyring
import pytest

from app.services.credential_store import CredentialStore, CredentialStoreError
from tests.conftest import MemoryKeyring


@pytest.fixture(autouse=True)
def forbid_real_keyring(monkeypatch):
    """Fail loudly if any code path falls back to the real Windows Credential Manager."""
    def _boom():
        raise AssertionError("real keyring must not be used in tests")
    monkeypatch.setattr(keyring, "get_keyring", _boom)


def test_target_name():
    assert CredentialStore.target("fccs-prod") == "EPMTaskDeployer:fccs-prod"


def test_save_get_has_delete(memory_keyring):
    store = CredentialStore(memory_keyring)
    assert store.get("fccs-prod", "svc") is None
    assert store.has("fccs-prod", "svc") is False

    store.save("fccs-prod", "svc", "s3cret")
    assert memory_keyring.store == {("EPMTaskDeployer:fccs-prod", "svc"): "s3cret"}
    assert store.get("fccs-prod", "svc") == "s3cret"
    assert store.has("fccs-prod", "svc") is True
    assert store.has("fccs-prod", "other-user") is False
    assert store.has("other-env", "svc") is False

    store.delete("fccs-prod", "svc")
    assert memory_keyring.store == {}
    assert store.has("fccs-prod", "svc") is False


def test_save_overwrites(memory_keyring):
    store = CredentialStore(memory_keyring)
    store.save("e", "u", "old")
    store.save("e", "u", "new")
    assert store.get("e", "u") == "new"


def test_empty_secret_rejected(memory_keyring):
    store = CredentialStore(memory_keyring)
    with pytest.raises(CredentialStoreError, match="must not be empty"):
        store.save("e", "u", "")
    assert memory_keyring.calls == []


def test_delete_when_missing_is_silent(memory_keyring):
    CredentialStore(memory_keyring).delete("e", "u")
    assert memory_keyring.calls == [("delete", "EPMTaskDeployer:e", "u")]


@pytest.mark.parametrize("op, args", [
    ("save", ("e", "u", "pw")),
    ("get", ("e", "u")),
    ("has", ("e", "u")),
    ("delete", ("e", "u")),
])
def test_keyring_error_wrapped(broken_keyring, op, args):
    store = CredentialStore(broken_keyring)
    with pytest.raises(CredentialStoreError, match="Windows Credential Manager") as exc:
        getattr(store, op)(*args)
    assert isinstance(exc.value.__cause__, keyring.errors.KeyringError)


def test_save_error_message_does_not_contain_secret(broken_keyring):
    with pytest.raises(CredentialStoreError) as exc:
        CredentialStore(broken_keyring).save("e", "u", "TopSecret123")
    assert "TopSecret123" not in str(exc.value)


def test_secret_never_logged(memory_keyring, caplog):
    caplog.set_level("DEBUG")
    store = CredentialStore(memory_keyring)
    store.save("e", "u", "TopSecret123")
    store.get("e", "u")
    store.delete("e", "u")
    assert "TopSecret123" not in caplog.text
    assert "e" in caplog.text


def test_save_replaces_previous_entries_under_any_username():
    """keyring's Windows backend would keep '<old user>@target' copies; save must leave exactly one entry."""
    from keyring.credentials import SimpleCredential

    class VaultLike(MemoryKeyring):
        def get_credential(self, service, username):
            for (svc, user), pw in self.store.items():
                if svc == service and (username is None or user == username):
                    return SimpleCredential(user, pw)
            return None

    backend = VaultLike()
    store = CredentialStore(backend)
    store.save("fccs", "old.user", "pw1")
    store.save("fccs", "new.user", "pw2")
    assert store.get("fccs", "new.user") == "pw2"
    assert store.get("fccs", "old.user") is None
    assert len(backend.store) == 1

