"""Tests for the hacc config and options flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hacc import access
from custom_components.hacc.const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DATA_PAIRING_CODE_EXPIRES_AT,
    DATA_PAIRING_CODE_HASH,
    DOMAIN,
    PAIRING_CODE_LENGTH,
)
from custom_components.hacc.pairing import generate_device_key, hash_device_key, hash_pairing_code


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            DATA_DEVICE_ID: "device-1",
            DATA_DEVICE_KEY_HASH: hash_device_key(generate_device_key()),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _open_access_menu(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "access"}
    )


async def test_user_flow_shows_code_and_only_stores_hash(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    code = result["description_placeholders"]["code"]
    assert len(code) == PAIRING_CODE_LENGTH

    result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result2["type"] == FlowResultType.CREATE_ENTRY

    entry = result2["result"]
    assert entry.data[DATA_PAIRING_CODE_HASH] == hash_pairing_code(code)
    assert DATA_PAIRING_CODE_EXPIRES_AT in entry.data
    assert code not in str(entry.data)


async def test_options_flow_regenerates_code(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    entry = result["result"]
    old_hash = entry.data[DATA_PAIRING_CODE_HASH]

    menu_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu_result["type"] == FlowResultType.MENU
    assert "pairing_code" in menu_result["menu_options"]

    options_result = await hass.config_entries.options.async_configure(
        menu_result["flow_id"], {"next_step_id": "pairing_code"}
    )
    assert options_result["type"] == FlowResultType.FORM
    new_code = options_result["description_placeholders"]["code"]

    options_result2 = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {}
    )
    assert options_result2["type"] == FlowResultType.CREATE_ENTRY

    assert entry.data[DATA_PAIRING_CODE_HASH] == hash_pairing_code(new_code)
    assert entry.data[DATA_PAIRING_CODE_HASH] != old_hash


async def test_access_menu_hides_empty_categories(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    access_menu = await _open_access_menu(hass, entry)
    assert access_menu["type"] == FlowResultType.MENU
    assert access_menu["menu_options"] == [
        "access_grant_read",
        "access_grant_call",
        "access_discovery",
    ]


async def test_access_grant_read_then_revoke(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    access_menu = await _open_access_menu(hass, entry)

    result = await hass.config_entries.options.async_configure(
        access_menu["flow_id"], {"next_step_id": "access_grant_read"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entities": ["sensor.drucker_status"]}
    )
    assert result["type"] == FlowResultType.MENU  # zurück im access-Menü
    assert access.is_read_granted(entry, "sensor.drucker_status")

    granted_menu = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "access_granted"}
    )
    result = await hass.config_entries.options.async_configure(
        granted_menu["flow_id"], {"revoke": ["read:sensor.drucker_status"]}
    )
    assert result["type"] == FlowResultType.MENU
    assert not access.is_read_granted(entry, "sensor.drucker_status")
    assert "sensor.drucker_status" not in access.read_grants(entry)


async def test_access_pending_grant_and_deny(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    access.request_read(hass, entry, "sensor.drucker_status")
    access.request_read(hass, entry, "light.buero")

    access_menu = await _open_access_menu(hass, entry)
    assert "access_pending" in access_menu["menu_options"]

    pending = await hass.config_entries.options.async_configure(
        access_menu["flow_id"], {"next_step_id": "access_pending"}
    )
    await hass.config_entries.options.async_configure(
        pending["flow_id"],
        {"grant": ["read:sensor.drucker_status"], "deny": ["read:light.buero"]},
    )

    assert access.is_read_granted(entry, "sensor.drucker_status")
    denied = access.read_grants(entry)["light.buero"]
    assert denied.status is access.AccessStatus.DENIED


async def test_access_denied_can_be_reopened(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    access.request_read(hass, entry, "light.buero")
    access.set_read_status(hass, entry, ["light.buero"], access.AccessStatus.DENIED)

    access_menu = await _open_access_menu(hass, entry)
    assert "access_denied" in access_menu["menu_options"]

    denied_step = await hass.config_entries.options.async_configure(
        access_menu["flow_id"], {"next_step_id": "access_denied"}
    )
    await hass.config_entries.options.async_configure(
        denied_step["flow_id"], {"reopen": ["read:light.buero"]}
    )

    assert access.read_grants(entry)["light.buero"].status is access.AccessStatus.REQUESTED


async def test_access_discovery_toggle_defaults_off(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    assert access.discovery_enabled(entry) is False

    access_menu = await _open_access_menu(hass, entry)
    discovery_step = await hass.config_entries.options.async_configure(
        access_menu["flow_id"], {"next_step_id": "access_discovery"}
    )
    assert discovery_step["type"] == FlowResultType.FORM

    await hass.config_entries.options.async_configure(discovery_step["flow_id"], {"enabled": True})
    assert access.discovery_enabled(entry) is True


async def test_access_grant_call_with_target(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    access_menu = await _open_access_menu(hass, entry)

    call_step = await hass.config_entries.options.async_configure(
        access_menu["flow_id"], {"next_step_id": "access_grant_call"}
    )
    await hass.config_entries.options.async_configure(
        call_step["flow_id"], {"service": "light.turn_on", "target": "light.buero"}
    )

    assert access.is_call_granted(entry, "light", "turn_on", "light.buero")
    assert not access.is_call_granted(entry, "light", "turn_on", "light.kueche")
