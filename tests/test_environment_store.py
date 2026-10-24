import json

import pytest

from app.models import Environment
from app.services.credential_store import CredentialStore
from app.services.environment_store import (
    EnvironmentStore, guess_is_production, make_environment_id, normalize_base_url,
)

PROD = "https://svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com"
TEST = "https://svc-test-tenant.epm.us-phoenix-1.ocs.oraclecloud.com"


# ---- normalize_base_url -----------------------------------------------------

@pytest.mark.parametrize("raw", [
    "svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com",
    "https://svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com",
    "https://svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com/",
    "https://svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com/epmcloud/faces/AtkHomePageWelcome?x=1#frag",
    "  HTTPS://SVC-Tenant.EPM.us-phoenix-1.ocs.OracleCloud.com/HyperionPlanning  ",
    "https://svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com:443/epmcloud",
])
def test_normalize_variants(raw):
    assert normalize_base_url(raw) == PROD


def test_normalize_keeps_non_default_port():
    assert normalize_base_url("https://a.oraclecloud.com:8443/x") == "https://a.oraclecloud.com:8443"


@pytest.mark.parametrize("raw", ["", "   "])
def test_normalize_empty(raw):
    with pytest.raises(ValueError, match="Enter the Oracle environment URL"):
        normalize_base_url(raw)


@pytest.mark.parametrize("raw", ["http://svc-tenant.oraclecloud.com", "ftp://svc-tenant.oraclecloud.com"])
def test_normalize_rejects_non_https(raw):
    with pytest.raises(ValueError, match="https://"):
        normalize_base_url(raw)


@pytest.mark.parametrize("raw", [
    "https://evil.example.com",
    "https://oraclecloud.com.evil.com",
    "https://evil-oraclecloud.com",
])
def test_normalize_rejects_non_oracle_host(raw):
    with pytest.raises(ValueError, match="oraclecloud.com"):
        normalize_base_url(raw)


def test_normalize_custom_host_allowed():
    assert normalize_base_url("https://EPM.Corp.Example/x/y", allow_custom_host=True) == "https://epm.corp.example"


@pytest.mark.parametrize("raw", [
    "https://user:pass@svc-tenant.oraclecloud.com",
    "https://user@svc-tenant.oraclecloud.com",
    "user:secret@svc-tenant.oraclecloud.com",
])
def test_normalize_rejects_credentials_in_url(raw):
    with pytest.raises(ValueError, match="user name or password"):
        normalize_base_url(raw)


def test_normalize_credentials_error_does_not_echo_secret():
    with pytest.raises(ValueError) as exc:
        normalize_base_url("https://user:S3cr3tPw@svc-tenant.oraclecloud.com")
    assert "S3cr3tPw" not in str(exc.value)


@pytest.mark.parametrize("raw", ["https://", "https://localhost", "https:///path"])
def test_normalize_rejects_missing_host(raw):
    with pytest.raises(ValueError):
        normalize_base_url(raw, allow_custom_host=True)


# ---- guess_is_production ----------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (TEST, False),
    (PROD, True),
    ("https://fccs-test-acme.epm.ocs.oraclecloud.com", False),
    ("https://svc-tenant-test.epm.ocs.oraclecloud.com", False),
    ("https://svc-contest.epm.ocs.oraclecloud.com", True),     # 'test' inside a word is not a marker
    ("https://svc-testing.epm.ocs.oraclecloud.com", True),
    ("https://svc-tenant.test.ocs.oraclecloud.com", True),      # only the first label counts
])
def test_guess_is_production(url, expected):
    assert guess_is_production(url) is expected


# ---- make_environment_id ----------------------------------------------------

def test_make_environment_id_slug():
    assert make_environment_id("FCCS Prod (EU)", set()) == "fccs-prod-eu"


def test_make_environment_id_unique():
    existing = {"fccs-prod", "fccs-prod-2"}
    assert make_environment_id("FCCS Prod", existing) == "fccs-prod-3"
    assert make_environment_id("FCCS Prod", {"fccs-prod"}) == "fccs-prod-2"


@pytest.mark.parametrize("name", ["", "!!!", "   "])
def test_make_environment_id_fallback(name):
    assert make_environment_id(name, set()) == "environment"
    assert make_environment_id(name, {"environment"}) == "environment-2"


# ---- EnvironmentStore -------------------------------------------------------

def env(**overrides) -> Environment:
    data = dict(id="fccs-prod", display_name="FCCS Prod", service_type="FCCS", base_url=PROD, username="svc_user")
    data.update(overrides)
    return Environment(**data)


def test_default_path_uses_epmtd_home(isolated_home):
    store = EnvironmentStore()
    assert store.path == isolated_home / "environments.json"
    assert store.list() == []


def test_save_load_roundtrip(isolated_home):
    store = EnvironmentStore()
    original = env(is_production=True, default_org_unit="Corp", period_mapping="fiscal_april",
                   day_zero_strategy="fixed_day", day_zero_fixed_day=3)
    store.save(original)
    store.save(env(id="fccs-test", display_name="FCCS Test", base_url=TEST))
    store.set_last_used("fccs-test")

    reloaded = EnvironmentStore()
    assert reloaded.get("fccs-prod") == original
    assert [e.id for e in reloaded.list()] == ["fccs-prod", "fccs-test"]
    assert reloaded.ids() == {"fccs-prod", "fccs-test"}
    assert reloaded.last_used_id == "fccs-test"
    assert not list(isolated_home.glob("*.tmp")), "atomic write must not leave temp files"


def test_list_sorted_case_insensitively():
    store = EnvironmentStore()
    store.save(env(id="b", display_name="beta"))
    store.save(env(id="a", display_name="Alpha"))
    store.save(env(id="c", display_name="Gamma"))
    assert [e.display_name for e in store.list()] == ["Alpha", "beta", "Gamma"]


def test_update_existing_keeps_single_entry():
    store = EnvironmentStore()
    store.save(env())
    store.save(env(username="other_user"))
    assert len(store.list()) == 1
    assert EnvironmentStore().get("fccs-prod").username == "other_user"


@pytest.mark.parametrize("name", ["FCCS Prod", "fccs prod", "  FCCS PROD  "])
def test_duplicate_display_name_rejected(name):
    store = EnvironmentStore()
    store.save(env())
    with pytest.raises(ValueError, match="already exists"):
        store.save(env(id="other", display_name=name))
    assert store.ids() == {"fccs-prod"}


@pytest.mark.parametrize("service_type", ["fccs", "EPBCS", ""])
def test_unknown_service_type_rejected(service_type):
    store = EnvironmentStore()
    with pytest.raises(ValueError, match="Unknown service type"):
        store.save(env(service_type=service_type))
    assert not store.path.exists()


def test_all_configured_service_types_accepted():
    store = EnvironmentStore()
    for i, st in enumerate(["FCCS", "TRCS", "PLANNING", "FREEFORM", "EPCM", "SWP", "SALES_PLANNING", "ARCS"]):
        store.save(env(id=f"e{i}", display_name=f"Env {i}", service_type=st))
    assert len(store.list()) == 8


@pytest.mark.parametrize("field, value, msg", [
    ("display_name", "  ", "name for the environment"),
    ("username", "", "user name"),
])
def test_blank_fields_rejected(field, value, msg):
    with pytest.raises(ValueError, match=msg):
        EnvironmentStore().save(env(**{field: value}))


def test_delete_clears_last_used():
    store = EnvironmentStore()
    store.save(env())
    store.save(env(id="t", display_name="Test"))
    store.set_last_used("fccs-prod")
    store.delete("fccs-prod")
    assert store.get("fccs-prod") is None
    assert store.last_used_id is None
    reloaded = EnvironmentStore()
    assert reloaded.last_used_id is None
    assert reloaded.ids() == {"t"}


def test_delete_other_keeps_last_used():
    store = EnvironmentStore()
    store.save(env())
    store.save(env(id="t", display_name="Test"))
    store.set_last_used("fccs-prod")
    store.delete("t")
    assert EnvironmentStore().last_used_id == "fccs-prod"


def test_delete_unknown_is_silent():
    store = EnvironmentStore()
    store.delete("nope")
    assert store.list() == []


def test_json_contains_no_password(isolated_home, memory_keyring):
    secret = "Sup3r-Secret-Pa55!"
    store = EnvironmentStore()
    e = env()
    store.save(e)
    CredentialStore(memory_keyring).save(e.id, e.username, secret)
    store.set_last_used(e.id)

    raw = (isolated_home / "environments.json").read_text(encoding="utf-8")
    assert secret not in raw
    data = json.loads(raw)
    for item in data["environments"]:
        assert not {k for k in item if any(w in k.lower() for w in ("password", "secret", "token"))}
    # nothing else in the app-data folder holds the secret either
    for path in isolated_home.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_unknown_json_keys_ignored(isolated_home):
    payload = {
        "version": 2,
        "future_setting": True,
        "last_used_id": "fccs-prod",
        "environments": [dict(
            id="fccs-prod", display_name="FCCS Prod", service_type="FCCS", base_url=PROD, username="u",
            password="should-be-ignored", color="red",
        )],
    }
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "environments.json").write_text(json.dumps(payload), encoding="utf-8")
    store = EnvironmentStore()
    e = store.get("fccs-prod")
    assert e is not None and e.username == "u"
    assert not hasattr(e, "password")
    assert store.last_used_id == "fccs-prod"
    # re-saving drops the stray password key from disk
    store.save(e)
    assert "should-be-ignored" not in (isolated_home / "environments.json").read_text(encoding="utf-8")


def test_environment_host_property():
    assert env().host == "svc-tenant.epm.us-phoenix-1.ocs.oraclecloud.com"


# --- Robustness: damaged environments.json must not crash the app ------------

def test_corrupt_json_is_moved_aside_and_reported(tmp_path):
    path = tmp_path / "environments.json"
    path.write_text("{not json", encoding="utf-8")
    store = EnvironmentStore(path)
    assert store.list() == []
    assert store.load_error and "could not be read" in store.load_error
    assert not path.exists()
    assert len(list(tmp_path.glob("environments.corrupt-*.json"))) == 1


def test_incomplete_entries_are_skipped(tmp_path):
    path = tmp_path / "environments.json"
    path.write_text(json.dumps({"environments": [
        {"id": "ok", "display_name": "OK", "service_type": "FCCS",
         "base_url": "https://a.oraclecloud.com", "username": "u"},
        {"id": "broken"},
    ]}), encoding="utf-8")
    store = EnvironmentStore(path)
    assert [e.id for e in store.list()] == ["ok"]
    assert "1 saved environment" in store.load_error


def test_guess_is_production_without_scheme():
    assert guess_is_production("svc-test-x.epm.oraclecloud.com") is False
    assert guess_is_production("svc-x.epm.oraclecloud.com") is True


@pytest.mark.parametrize("url", ["https://a.oraclecloud.com:abc", "https://a.oraclecloud.com:99999"])
def test_invalid_port_has_friendly_message(url):
    with pytest.raises(ValueError, match="invalid port"):
        normalize_base_url(url)
