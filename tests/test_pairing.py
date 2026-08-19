"""Tests for POST /api/hacc/pair."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.hacc.const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DATA_PAIRING_CODE_EXPIRES_AT,
    DATA_PAIRING_CODE_HASH,
    DOMAIN,
    PAIR_PATH,
    PAIR_RATE_LIMIT_MAX_FAILURES,
)
from custom_components.hacc.pairing import hash_device_key, new_pairing_entry_data


async def _setup_pending_entry(hass: HomeAssistant) -> tuple[str, MockConfigEntry]:
    await async_setup_component(hass, "http", {})
    await async_setup_component(hass, DOMAIN, {})
    code, data = new_pairing_entry_data()
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return code, entry


async def test_valid_code_returns_device_id_and_key(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    code, entry = await _setup_pending_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(PAIR_PATH, json={"code": code, "device_name": "Büro-PC"})
    assert resp.status == 200
    payload = await resp.json()
    assert "device_id" in payload
    assert "device_key" in payload
    await hass.async_block_till_done()

    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated.data[DATA_DEVICE_ID] == payload["device_id"]
    assert updated.data[DATA_DEVICE_KEY_HASH] == hash_device_key(payload["device_key"])
    assert DATA_PAIRING_CODE_HASH not in updated.data


async def test_code_is_single_use(hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator):
    code, _entry = await _setup_pending_entry(hass)
    client = await hass_client_no_auth()

    first = await client.post(PAIR_PATH, json={"code": code, "device_name": "Büro-PC"})
    assert first.status == 200
    await hass.async_block_till_done()

    second = await client.post(PAIR_PATH, json={"code": code, "device_name": "Büro-PC"})
    assert second.status == 400


async def test_expired_code_is_rejected(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    code, entry = await _setup_pending_entry(hass)
    expired_data = dict(entry.data)
    expired_data[DATA_PAIRING_CODE_EXPIRES_AT] = (
        dt_util.utcnow() - timedelta(seconds=1)
    ).isoformat()
    hass.config_entries.async_update_entry(entry, data=expired_data)

    client = await hass_client_no_auth()
    resp = await client.post(PAIR_PATH, json={"code": code, "device_name": "Büro-PC"})
    assert resp.status == 400


async def test_wrong_code_is_rejected(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    await _setup_pending_entry(hass)
    client = await hass_client_no_auth()

    resp = await client.post(PAIR_PATH, json={"code": "ZZZZZZZZ", "device_name": "Büro-PC"})
    assert resp.status == 400


async def test_rate_limit_blocks_even_the_correct_code(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
):
    code, _entry = await _setup_pending_entry(hass)
    client = await hass_client_no_auth()

    for _ in range(PAIR_RATE_LIMIT_MAX_FAILURES):
        resp = await client.post(PAIR_PATH, json={"code": "ZZZZZZZZ", "device_name": "PC"})
        assert resp.status == 400

    resp = await client.post(PAIR_PATH, json={"code": code, "device_name": "PC"})
    assert resp.status == 400
