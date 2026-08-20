"""Tests der zweckgebundenen Services `hacc.notify`/`hacc.launch`/`hacc.set_displays`.

Wie in test_commands.py steckt hier ein Fake-WebSocket dahinter statt eines
echten - schneller, und die Antwort lässt sich exakt steuern."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hacc import connection as conn
from custom_components.hacc.const import DATA_DEVICE_ID, DATA_DEVICE_KEY_HASH, DOMAIN
from custom_components.hacc.pairing import generate_device_key, hash_device_key


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


async def _setup_connected_device(
    hass: HomeAssistant,
) -> tuple[str, MockConfigEntry, conn.ConnectionState]:
    """Ein gekoppeltes, "verbundenes" Gerät ohne echten WebSocket - gibt die
    HA-Geräte-Registry-Id zurück (das, was ein Target-Selector liefert)."""
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

    state = conn.get_connection_state(hass, entry.entry_id)
    state.device_id = "device-1"
    conn.async_apply_register(hass, entry, "buero_pc", [])
    state.connected = True
    state.ws = _FakeWs()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "device-1")})
    assert device is not None
    return device.id, entry, state


async def test_notify_sends_the_declared_fields(hass: HomeAssistant) -> None:
    device_registry_id, entry, state = await _setup_connected_device(hass)

    task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "notify",
            {"title": "Waschmaschine", "message": "fertig"},
            target={ATTR_DEVICE_ID: device_registry_id},
            blocking=True,
        )
    )
    await asyncio.sleep(0)
    sent = state.ws.sent[0]
    assert sent["command"] == "notify"
    assert sent["data"] == {"title": "Waschmaschine", "message": "fertig"}

    conn.async_apply_command_result(hass, entry, {"id": sent["id"], "success": True, "error": None})
    await task


async def test_launch_sends_the_entry_field(hass: HomeAssistant) -> None:
    device_registry_id, entry, state = await _setup_connected_device(hass)

    task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "launch",
            {"entry": "elden_ring"},
            target={ATTR_DEVICE_ID: device_registry_id},
            blocking=True,
        )
    )
    await asyncio.sleep(0)
    sent = state.ws.sent[0]
    assert sent["command"] == "launch"
    assert sent["data"] == {"entry": "elden_ring"}

    conn.async_apply_command_result(hass, entry, {"id": sent["id"], "success": True, "error": None})
    await task


async def test_set_displays_sends_the_declared_fields(hass: HomeAssistant) -> None:
    device_registry_id, entry, state = await _setup_connected_device(hass)

    task = asyncio.create_task(
        hass.services.async_call(
            DOMAIN,
            "set_displays",
            {"monitors": ["links", "mitte"], "primary": "mitte"},
            target={ATTR_DEVICE_ID: device_registry_id},
            blocking=True,
        )
    )
    await asyncio.sleep(0)
    sent = state.ws.sent[0]
    assert sent["command"] == "set_displays"
    assert sent["data"] == {"monitors": ["links", "mitte"], "primary": "mitte"}

    conn.async_apply_command_result(hass, entry, {"id": sent["id"], "success": True, "error": None})
    await task


async def test_a_failed_command_raises_from_the_service(hass: HomeAssistant) -> None:
    device_registry_id, entry, state = await _setup_connected_device(hass)

    async def call() -> None:
        await hass.services.async_call(
            DOMAIN,
            "notify",
            {"title": "Klappt nicht"},
            target={ATTR_DEVICE_ID: device_registry_id},
            blocking=True,
        )

    task = asyncio.create_task(call())
    await asyncio.sleep(0)
    sent = state.ws.sent[0]
    conn.async_apply_command_result(
        hass,
        entry,
        {
            "id": sent["id"],
            "success": False,
            "error": {"code": "not_shown", "message": "not_shown"},
        },
    )

    with pytest.raises(HomeAssistantError):
        await task


async def test_an_unknown_device_is_rejected(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, DOMAIN, {})

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "notify",
            {"title": "x"},
            target={ATTR_DEVICE_ID: "does-not-exist"},
            blocking=True,
        )


async def test_launch_requires_the_entry_field(hass: HomeAssistant) -> None:
    device_registry_id, _entry, _state = await _setup_connected_device(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "launch", {}, target={ATTR_DEVICE_ID: device_registry_id}, blocking=True
        )
