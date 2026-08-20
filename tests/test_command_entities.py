"""Tests der bedienbaren Entitäten (Step 5.4): button/select/number/switch.

Jede Interaktion schickt einen `command` über den echten WebSocket und wartet
auf `result` - dieselbe Server-Rolle wie in test_entities.py, nur dass der Test
hier zusätzlich als "PC" antworten muss, sonst würde der Aufruf in den Timeout
laufen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.hacc.const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DOMAIN,
    PROTOCOL_VERSION,
    WS_PATH,
)
from custom_components.hacc.pairing import generate_device_key, hash_device_key

DEVICE_NAME = "buero_pc"
TIMEOUT = 5.0

SHUTDOWN_BUTTON = {
    "key": "shutdown",
    "name": "Herunterfahren",
    "domain": "button",
    "icon": "mdi:power",
    "unit": None,
    "device_class": None,
    "state_class": None,
    "attributes": {},
    "command": "shutdown",
    "command_data": {},
    "value_field": "value",
}
AUDIO_SELECT = {
    "key": "audio_output",
    "name": "Audio-Ausgabe",
    "domain": "select",
    "icon": "mdi:speaker",
    "unit": None,
    "device_class": None,
    "state_class": None,
    "attributes": {},
    "command": "set_audio",
    "command_data": {"direction": "output"},
    "value_field": "device",
}
VOLUME_NUMBER = {
    "key": "audio_volume",
    "name": "Lautstärke",
    "domain": "number",
    "icon": "mdi:volume-high",
    "unit": "%",
    "device_class": None,
    "state_class": None,
    "attributes": {},
    "min": 0,
    "max": 100,
    "step": 1,
    "command": "set_volume",
    "command_data": {"direction": "output"},
    "value_field": "level",
}
MUTE_SWITCH = {
    "key": "audio_muted",
    "name": "Ton stumm",
    "domain": "switch",
    "icon": "mdi:volume-off",
    "unit": None,
    "device_class": None,
    "state_class": None,
    "attributes": {},
    "command": "set_mute",
    "command_data": {"direction": "output"},
    "value_field": "mute",
}


async def _wait_for(predicate: Callable[[], bool], seconds: float = TIMEOUT) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


async def _setup_paired_entry(hass: HomeAssistant) -> tuple[str, str, MockConfigEntry]:
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, DOMAIN, {})
    device_id = "device-1"
    device_key = generate_device_key()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={DATA_DEVICE_ID: device_id, DATA_DEVICE_KEY_HASH: hash_device_key(device_key)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return device_id, device_key, entry


def _ws_url(device_id: str, device_key: str) -> str:
    return f"{WS_PATH}?device_id={device_id}&device_key={device_key}"


async def _connect_and_register(
    hass: HomeAssistant,
    client: ClientSessionGenerator,
    device_id: str,
    device_key: str,
    entities: list[dict[str, Any]],
) -> Any:
    http_client = await client()
    ws = await http_client.ws_connect(_ws_url(device_id, device_key))
    await ws.send_json(
        {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "device_name": DEVICE_NAME,
            "app_version": "0.5.0",
            "os_version": "Windows-11",
        }
    )
    await ws.receive_json()  # hello_ok
    await ws.send_json({"type": "register", "device_name": DEVICE_NAME, "entities": entities})
    await _wait_for(
        lambda: _entity_id(hass, entities[0]["domain"], device_id, entities[0]["key"]) is not None
    )
    return ws


def _entity_id(hass: HomeAssistant, domain: str, device_id: str, key: str) -> str | None:
    from homeassistant.helpers import entity_registry as er

    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{device_id}_{key}")


async def _answer_next_command(
    ws: Any, *, success: bool, error: dict[str, str] | None = None
) -> dict[str, Any]:
    """Auf die nächste `command`-Nachricht mit `result` antworten; gibt sie zurück."""
    message = await ws.receive_json()
    assert message["type"] == "command"
    await ws.send_json({"type": "result", "id": message["id"], "success": success, "error": error})
    return message


async def test_button_press_sends_a_command_and_reports_the_result(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [SHUTDOWN_BUTTON]
    )
    entity_id = _entity_id(hass, "button", device_id, "shutdown")
    assert entity_id is not None

    events: list[dict[str, Any]] = []
    hass.bus.async_listen("hacc_command_result", lambda event: events.append(event.data))

    press = asyncio.create_task(
        hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    )
    command = await _answer_next_command(ws, success=True)
    await press

    assert command["command"] == "shutdown"
    assert command["data"] == {}
    await _wait_for(lambda: bool(events))
    assert events[0]["result"] == "executed"
    assert events[0]["device_id"] == device_id

    await ws.close()


async def test_button_press_failure_raises(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [SHUTDOWN_BUTTON]
    )
    entity_id = _entity_id(hass, "button", device_id, "shutdown")

    async def press() -> None:
        await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)

    task = asyncio.create_task(press())
    await _answer_next_command(ws, success=False, error={"code": "disabled", "message": "disabled"})

    with pytest.raises(HomeAssistantError):
        await task

    await ws.close()


async def test_select_option_sends_the_value_field(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [AUDIO_SELECT]
    )
    entity_id = _entity_id(hass, "select", device_id, "audio_output")

    await ws.send_json(
        {
            "type": "state",
            "states": [
                {
                    "key": "audio_output",
                    "state": "Kopfhörer",
                    "attributes": {"options": ["Kopfhörer", "Monitor"]},
                }
            ],
        }
    )
    await _wait_for(
        lambda: (
            hass.states.get(entity_id) is not None
            and hass.states.get(entity_id).state == "Kopfhörer"
        )
    )
    assert hass.states.get(entity_id).attributes["options"] == ["Kopfhörer", "Monitor"]

    task = asyncio.create_task(
        hass.services.async_call(
            "select", "select_option", {"entity_id": entity_id, "option": "Monitor"}, blocking=True
        )
    )
    command = await _answer_next_command(ws, success=True)
    await task

    assert command["command"] == "set_audio"
    assert command["data"] == {"direction": "output", "device": "Monitor"}

    await ws.close()


async def test_number_set_value_sends_the_value_field(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [VOLUME_NUMBER]
    )
    entity_id = _entity_id(hass, "number", device_id, "audio_volume")
    assert hass.states.get(entity_id).attributes["min"] == 0
    assert hass.states.get(entity_id).attributes["max"] == 100

    task = asyncio.create_task(
        hass.services.async_call(
            "number", "set_value", {"entity_id": entity_id, "value": 30}, blocking=True
        )
    )
    command = await _answer_next_command(ws, success=True)
    await task

    assert command["command"] == "set_volume"
    assert command["data"] == {"direction": "output", "level": 30.0}

    await ws.close()


async def test_switch_turn_on_and_off_send_the_mute_field(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [MUTE_SWITCH]
    )
    entity_id = _entity_id(hass, "switch", device_id, "audio_muted")

    task = asyncio.create_task(
        hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    )
    command = await _answer_next_command(ws, success=True)
    await task
    assert command["data"] == {"direction": "output", "mute": True}

    task = asyncio.create_task(
        hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    )
    command = await _answer_next_command(ws, success=True)
    await task
    assert command["data"] == {"direction": "output", "mute": False}

    await ws.close()


async def test_switch_state_tracks_the_reported_value(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [MUTE_SWITCH]
    )
    entity_id = _entity_id(hass, "switch", device_id, "audio_muted")

    await ws.send_json(
        {"type": "state", "states": [{"key": "audio_muted", "state": "on", "attributes": {}}]}
    )
    await _wait_for(lambda: hass.states.get(entity_id).state == "on")

    await ws.close()
