"""Tests für das Freigabe-Datenmodell (custom_components/hacc/access.py)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hacc import access
from custom_components.hacc.const import DOMAIN, MAX_PENDING_ACCESS_REQUESTS


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return entry


async def test_request_read_creates_a_requested_entry(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.request_read(hass, entry, "sensor.drucker_status")

    grant = access.read_grants(entry)["sensor.drucker_status"]
    assert grant.status is access.AccessStatus.REQUESTED
    assert grant.requested_at
    assert not access.is_read_granted(entry, "sensor.drucker_status")


async def test_request_read_deduplicates_but_refreshes_timestamp(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.request_read(hass, entry, "sensor.drucker_status")
    first = access.read_grants(entry)["sensor.drucker_status"].requested_at

    access.request_read(hass, entry, "sensor.drucker_status")
    second = access.read_grants(entry)["sensor.drucker_status"].requested_at

    assert len(access.read_grants(entry)) == 1
    assert second >= first


async def test_request_read_never_overwrites_a_decision(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.request_read(hass, entry, "sensor.drucker_status")
    access.set_read_status(hass, entry, ["sensor.drucker_status"], access.AccessStatus.DENIED)

    access.request_read(hass, entry, "sensor.drucker_status")

    assert access.read_grants(entry)["sensor.drucker_status"].status is access.AccessStatus.DENIED


async def test_request_read_is_capped(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    for i in range(MAX_PENDING_ACCESS_REQUESTS):
        access.request_read(hass, entry, f"sensor.entity_{i}")
    access.request_read(hass, entry, "sensor.one_too_many")

    assert len(access.read_grants(entry)) == MAX_PENDING_ACCESS_REQUESTS
    assert "sensor.one_too_many" not in access.read_grants(entry)


async def test_revoke_deletes_the_entry_entirely(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.grant_read(hass, entry, ["sensor.drucker_status"])
    assert access.is_read_granted(entry, "sensor.drucker_status")

    access.revoke_read(hass, entry, ["sensor.drucker_status"])

    assert "sensor.drucker_status" not in access.read_grants(entry)
    # Nach einem Widerruf darf wieder ganz normal (neu) angefragt werden.
    access.request_read(hass, entry, "sensor.drucker_status")
    assert (
        access.read_grants(entry)["sensor.drucker_status"].status is access.AccessStatus.REQUESTED
    )


async def test_call_access_respects_target_restriction(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.grant_call(hass, entry, "light", "turn_on", ["light.buero"])

    assert access.is_call_granted(entry, "light", "turn_on", "light.buero")
    assert not access.is_call_granted(entry, "light", "turn_on", "light.kueche")
    assert not access.is_call_granted(entry, "light", "turn_on", None)


async def test_call_access_unrestricted_when_no_targets(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.grant_call(hass, entry, "light", "turn_on", [])

    assert access.is_call_granted(entry, "light", "turn_on", "light.buero")
    assert access.is_call_granted(entry, "light", "turn_on", None)


async def test_request_call_accumulates_targets_while_pending(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.request_call(hass, entry, "light", "turn_on", "light.buero")
    access.request_call(hass, entry, "light", "turn_on", "light.kueche")

    grant = access.call_grants(entry)[access.call_key("light", "turn_on")]
    assert set(grant.targets) == {"light.buero", "light.kueche"}


async def test_discovery_defaults_off_and_can_be_toggled(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    assert access.discovery_enabled(entry) is False

    access.set_discovery_enabled(hass, entry, True)
    assert access.discovery_enabled(entry) is True


async def test_access_message_reflects_current_table(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    access.grant_read(hass, entry, ["sensor.drucker_status"])
    access.request_read(hass, entry, "light.buero")
    access.set_discovery_enabled(hass, entry, True)

    message = access.access_message(entry)
    assert message["type"] == "access"
    assert message["read"] == {"sensor.drucker_status": "granted", "light.buero": "requested"}
    assert message["discovery"] is True
