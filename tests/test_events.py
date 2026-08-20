"""Tests der `event`-Nachricht (Step 5.4): pc_notification(_action) wird zu
hacc_notification(_action)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
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

TIMEOUT = 5.0


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


async def _connect(
    hass: HomeAssistant, client: ClientSessionGenerator, device_id: str, device_key: str
) -> Any:
    http_client = await client()
    ws = await http_client.ws_connect(f"{WS_PATH}?device_id={device_id}&device_key={device_key}")
    await ws.send_json(
        {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "device_name": "buero_pc",
            "app_version": "0.5.0",
            "os_version": "Windows-11",
        }
    )
    await ws.receive_json()  # hello_ok
    return ws


async def test_pc_notification_becomes_hacc_notification(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect(hass, hass_client_no_auth, device_id, device_key)
    events: list[dict[str, Any]] = []
    hass.bus.async_listen("hacc_notification", lambda event: events.append(event.data))

    await ws.send_json(
        {"type": "event", "event_type": "pc_notification", "data": {"title": "Download fertig"}}
    )

    await _wait_for(lambda: bool(events))
    assert events[0]["title"] == "Download fertig"
    assert events[0]["device_id"] == device_id

    await ws.close()


async def test_pc_notification_action_becomes_hacc_notification_action(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect(hass, hass_client_no_auth, device_id, device_key)
    events: list[dict[str, Any]] = []
    hass.bus.async_listen("hacc_notification_action", lambda event: events.append(event.data))

    await ws.send_json(
        {
            "type": "event",
            "event_type": "pc_notification_action",
            "data": {"action": "confirm", "title": "Fortfahren?"},
        }
    )

    await _wait_for(lambda: bool(events))
    assert events[0]["action"] == "confirm"
    assert events[0]["device_id"] == device_id

    await ws.close()


async def test_an_unknown_event_type_is_ignored(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect(hass, hass_client_no_auth, device_id, device_key)
    events: list[dict[str, Any]] = []
    hass.bus.async_listen_once("hacc_notification", lambda event: events.append(event.data))

    await ws.send_json({"type": "event", "event_type": "something_else", "data": {}})
    # Ein bekanntes Event danach beweist, dass die Verbindung intakt blieb.
    await ws.send_json(
        {"type": "event", "event_type": "pc_notification", "data": {"title": "noch da"}}
    )

    await _wait_for(lambda: bool(events))
    assert events[0]["title"] == "noch da"

    await ws.close()
