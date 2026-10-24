"""Oracle environment profiles (non-secret) stored in %APPDATA%\\EPMTaskDeployer\\environments.json."""

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.models import Environment
from app.utils.config import service_types
from app.utils.paths import app_data_dir

log = logging.getLogger(__name__)

ORACLE_HOST_SUFFIX = ".oraclecloud.com"


def normalize_base_url(raw: str, allow_custom_host: bool = False) -> str:
    """Return 'https://host' for an Oracle EPM URL pasted from the browser. Raises ValueError with a user message."""
    text = raw.strip()
    if not text:
        raise ValueError("Enter the Oracle environment URL.")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() != "https":
        raise ValueError("The environment URL must start with https://")
    try:
        port_number = parts.port
    except ValueError:
        raise ValueError("The environment URL contains an invalid port number.") from None
    host = (parts.hostname or "").lower()
    if not host or "." not in host:
        raise ValueError("The environment URL does not contain a valid host name.")
    if parts.username or parts.password:
        raise ValueError("Do not put the user name or password into the URL.")
    if not allow_custom_host and not host.endswith(ORACLE_HOST_SUFFIX):
        raise ValueError(f"The host must end with '{ORACLE_HOST_SUFFIX}'. Copy the URL from your Oracle EPM login page.")
    port = f":{port_number}" if port_number and port_number != 443 else ""
    return f"https://{host}{port}"


def guess_is_production(base_url: str) -> bool:
    """Oracle names test environments '<service>-test-<tenant>...'. The user always confirms the result."""
    url = base_url if "://" in base_url else "https://" + base_url
    first_label = (urlsplit(url).hostname or "").split(".")[0]
    return not re.search(r"(^|-)test(-|$)", first_label)


def make_environment_id(display_name: str, existing_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or "environment"
    candidate, n = base, 2
    while candidate in existing_ids:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


class EnvironmentStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "environments.json"
        self._items: dict[str, Environment] = {}
        self.last_used_id: str | None = None
        # User-facing message when environments.json could not be read completely (shown by the UI).
        self.load_error: str | None = None
        self.load()

    def load(self) -> None:
        self._items.clear()
        self.load_error = None
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("unexpected file structure")
        except (OSError, ValueError) as exc:
            backup = self.path.with_name(f"{self.path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
            os.replace(self.path, backup)
            log.warning("environments.json could not be read (%s); moved to %s", exc, backup)
            self.load_error = (f"The saved environment list could not be read and was moved to {backup.name}. "
                               "Please add your environments again.")
            return
        known = {f.name for f in fields(Environment)}
        skipped = 0
        for raw in data.get("environments", []):
            try:
                env = Environment(**{k: v for k, v in raw.items() if k in known})
            except (TypeError, AttributeError):
                skipped += 1
                continue
            self._items[env.id] = env
        if skipped:
            log.warning("%d incomplete environment entr(y/ies) ignored in %s", skipped, self.path)
            self.load_error = f"{skipped} saved environment(s) were incomplete and have been ignored."
        self.last_used_id = data.get("last_used_id")

    def _write(self) -> None:
        payload = {
            "version": 1,
            "last_used_id": self.last_used_id,
            "environments": [asdict(e) for e in self._items.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)

    def list(self) -> list[Environment]:
        return sorted(self._items.values(), key=lambda e: e.display_name.lower())

    def get(self, environment_id: str) -> Environment | None:
        return self._items.get(environment_id)

    def ids(self) -> set[str]:
        return set(self._items)

    def save(self, env: Environment) -> None:
        valid_types = {t["id"] for t in service_types()}
        if env.service_type not in valid_types:
            raise ValueError(f"Unknown service type: {env.service_type}")
        if not env.display_name.strip():
            raise ValueError("Enter a name for the environment.")
        if not env.username.strip():
            raise ValueError("Enter the Oracle user name.")
        for other in self._items.values():
            if other.id != env.id and other.display_name.strip().lower() == env.display_name.strip().lower():
                raise ValueError(f"An environment named '{env.display_name}' already exists.")
        self._items[env.id] = env
        self._write()

    def delete(self, environment_id: str) -> None:
        self._items.pop(environment_id, None)
        if self.last_used_id == environment_id:
            self.last_used_id = None
        self._write()

    def set_last_used(self, environment_id: str) -> None:
        self.last_used_id = environment_id
        self._write()
