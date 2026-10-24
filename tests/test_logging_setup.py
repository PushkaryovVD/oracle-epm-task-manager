import logging

import pytest

from app.utils import logging_setup
from app.utils.logging_setup import SecretMaskingFilter, configure_logging, mask_secrets

SECRET = "Xy9-SECRET-vAlue"


@pytest.mark.parametrize("text", [
    f"password={SECRET}",
    f"PASSWORD = {SECRET}",
    f"login user=bob password={SECRET} host=x",
    f'{{"username": "bob", "password": "{SECRET}"}}',
    f"{{'password': '{SECRET}'}}",
    f"pwd:{SECRET}",
    f"passwd={SECRET}",
    f"client_secret={SECRET}",
    f"Authorization: Basic {SECRET}",
    f"authorization=Basic {SECRET}",
    f"Authorization: Bearer {SECRET}",
    f"Authorization:{SECRET}",
    f"refresh_token={SECRET}",
    f'"refresh_token": "{SECRET}"',
    f"access_token={SECRET}&expires_in=3600",
    f"token: {SECRET}",
])
def test_secret_masked(text):
    out = mask_secrets(text)
    assert SECRET not in out
    assert "********" in out


def test_non_secret_text_unchanged():
    text = "Deploying 'Monthly Close - January 2027' to https://svc-tenant.oraclecloud.com (user bob)"
    assert mask_secrets(text) == text


def test_surrounding_context_kept():
    out = mask_secrets(f"user=bob password={SECRET}, host=x")
    assert out == "user=bob password=********, host=x"


def test_multiple_secrets_in_one_line():
    out = mask_secrets(f"password={SECRET} refresh_token={SECRET}2 Authorization: Bearer {SECRET}3")
    assert SECRET not in out


# --- Regressions for previously found leaks ----------------------------------

@pytest.mark.parametrize("text", [
    f"headers={{'Authorization': 'Basic {SECRET}'}}",
    f'{{"Authorization": "Bearer {SECRET}"}}',
])
def test_authorization_header_in_dict_repr_masked(text):
    assert SECRET not in mask_secrets(text)


@pytest.mark.parametrize("secret", ["correct horse battery", "abc,def"])
def test_quoted_password_with_space_or_comma_fully_masked(secret):
    out = mask_secrets(f'{{"password": "{secret}"}}')
    for part in secret.replace(",", " ").split():
        assert part not in out, out


def test_bare_bearer_token_masked():
    assert SECRET not in mask_secrets(f"retrying request with Bearer {SECRET}")


def test_masking_formatter_masks_traceback():
    try:
        raise RuntimeError(f"login failed password={SECRET}")
    except RuntimeError:
        import sys
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    out = logging_setup.MaskingFormatter("%(message)s").format(record)
    assert "RuntimeError" in out
    assert SECRET not in out


# --- SecretMaskingFilter -----------------------------------------------------

def make_record(msg, args):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def test_filter_masks_formatted_args():
    record = make_record("login %s password=%s", ("bob", SECRET))
    assert SecretMaskingFilter().filter(record) is True
    assert record.getMessage() == "login bob password=********"
    assert SECRET not in record.getMessage()
    assert record.args == ()


def test_filter_masks_dict_args():
    record = make_record("payload %(body)s", ({"body": f"refresh_token={SECRET}"},))
    SecretMaskingFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_filter_handles_non_string_message():
    record = make_record({"password": SECRET}, None)
    SecretMaskingFilter().filter(record)
    assert SECRET not in record.getMessage()


def test_configure_logging_writes_masked_file(isolated_home):
    root = logging.getLogger()
    before = list(root.handlers)
    old_level = root.level
    try:
        configure_logging(logging.DEBUG)
        new = [h for h in root.handlers if h not in before]
        assert len(new) == 1
        logging.getLogger("x").info("auth password=%s Authorization: Basic %s", SECRET, SECRET)
        new[0].flush()
        content = (isolated_home / "logs" / "app.log").read_text(encoding="utf-8")
        assert "auth password=********" in content
        assert SECRET not in content
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        root.setLevel(old_level)


def test_mask_constant():
    assert logging_setup._MASK == "********"
