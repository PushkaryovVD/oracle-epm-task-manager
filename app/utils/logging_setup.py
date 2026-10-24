"""Application logging with secret masking. Passwords and tokens must never reach the log."""

import logging
import re
from logging.handlers import RotatingFileHandler

from app.utils.paths import logs_dir

_MASK = "********"
# key=value / key: value / "key": "value" (dict and JSON forms); quoted values are masked whole.
_KEY_VALUE = re.compile(
    r"""(?ix)
    (["']?\b\w*(?:authorization|password|passwd|pwd|secret|token)["']?\s*[:=]\s*)
    (?:(?:basic|bearer)\s+[^\s,'"}&]+ | "[^"]*" | '[^']*' | [^\s,'"}&]+)
    """
)
_BARE_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")


def mask_secrets(text: str) -> str:
    text = _KEY_VALUE.sub(lambda m: m.group(1) + _MASK, text)
    return _BARE_BEARER.sub(lambda m: m.group(1) + " " + _MASK, text)


class MaskingFormatter(logging.Formatter):
    """Masks the fully formatted record, including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_secrets(super().format(record))


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = RotatingFileHandler(logs_dir() / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretMaskingFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
