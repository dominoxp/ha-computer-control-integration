"""Bildet die statischen hassfest-Regeln lokal nach, damit sie nicht erst in CI auffallen."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from custom_components.hacc import CONFIG_SCHEMA

_INTEGRATION = Path(__file__).parent.parent / "custom_components" / "hacc"


def test_manifest_keys_sorted_domain_name_then_alphabetical() -> None:
    keys = list(json.loads((_INTEGRATION / "manifest.json").read_text(encoding="utf-8")))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_services_use_device_selector_instead_of_target_device_filter() -> None:
    services = yaml.safe_load((_INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
    for name, service in services.items():
        assert "device" not in (service.get("target") or {}), name
        device_field = service["fields"]["device_id"]
        assert device_field["selector"]["device"]["integration"] == "hacc", name


def test_config_schema_is_config_entry_only() -> None:
    assert CONFIG_SCHEMA({}) == {}
