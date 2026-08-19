"""Tests for the /api/hacc/ws WebSocket endpoint."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.hacc.connection import get_domain_data
from custom_components.hacc.const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DOMAIN,
    PROTOCOL_VERSION,
    WS_PATH,
)
from custom_components.hacc.pairing import generate_device_key, hash_device_key


async def _setup_paired_entry(hass: HomeAssistant) -> tuple[str, str, MockConfigEntry]:
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, DOMAIN, {})
    device_id = "device-1"
    device_key = generate_device_key()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            DATA_DEVICE_ID: device_id,
            DATA_DEVICE_KEY_HASH: hash_device_key(device_key),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return device_id, device_key, entry


def _ws_url(device_id: str, device_key: str) -> str:
    return f"{WS_PATH}?device_id={device_id}&device_key={device_key}"


async def test_handshake_succeeds_with_valid_key(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id, device_key)) as ws:
        await ws.send_json(
            {
                "type": "hello",
                "protocol_version": PROTOCOL_VERSION,
                "device_name": "PC",
                "app_version": "0.1.0",
            }
        )
        msg = await ws.receive_json()
        assert msg["type"] == "hello_ok"
        assert msg["protocol_version"] == PROTOCOL_VERSION
        assert msg["device_id"] == device_id


async def test_connection_refused_with_wrong_key(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    device_id, _device_key, entry = await _setup_paired_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.get(_ws_url(device_id, "not-the-right-key"))
    assert resp.status == 401
    assert entry.entry_id not in get_domain_data(hass).connections


async def test_connection_refused_without_key(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    device_id, _device_key, _entry = await _setup_paired_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.get(f"{WS_PATH}?device_id={device_id}")
    assert resp.status == 401


async def test_protocol_version_mismatch_is_rejected(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id, device_key)) as ws:
        await ws.send_json({"type": "hello", "protocol_version": PROTOCOL_VERSION + 1})
        msg = await ws.receive_json()
        assert msg["type"] == "error"
        assert msg["code"] == "protocol_version_mismatch"


async def test_second_connection_replaces_first(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    client = await hass_client_no_auth()

    ws1 = await client.ws_connect(_ws_url(device_id, device_key))
    await ws1.send_json(
        {"type": "hello", "protocol_version": PROTOCOL_VERSION, "device_name": "PC"}
    )
    await ws1.receive_json()

    ws2 = await client.ws_connect(_ws_url(device_id, device_key))
    await ws2.send_json(
        {"type": "hello", "protocol_version": PROTOCOL_VERSION, "device_name": "PC"}
    )
    await ws2.receive_json()

    closing_msg = await ws1.receive()
    assert closing_msg.type in (
        aiohttp.WSMsgType.CLOSE,
        aiohttp.WSMsgType.CLOSED,
        aiohttp.WSMsgType.CLOSING,
    )

    await ws1.close()
    await ws2.close()
