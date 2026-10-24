"""Loading of the JSON configuration files shipped in config/."""

import json
from functools import lru_cache
from pathlib import Path

from app.utils.paths import bundled_config_dir


def load_json(relative_path: str) -> dict:
    with open(bundled_config_dir() / relative_path, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def naming_rules() -> dict:
    return load_json("naming_rules.json")


@lru_cache(maxsize=1)
def column_catalogue() -> dict:
    return load_json("tm_columns.json")


@lru_cache(maxsize=1)
def service_types() -> list[dict]:
    return load_json("service_types.json")["service_types"]


def service_type(type_id: str) -> dict:
    for item in service_types():
        if item["id"] == type_id:
            return item
    raise KeyError(f"Unknown service type: {type_id}")


def period_mapping_files() -> list[Path]:
    return sorted((bundled_config_dir() / "period_mappings").glob("*.json"))
