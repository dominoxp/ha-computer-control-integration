"""Tests für Geräteanlage, dynamische Entitäten, Verfügbarkeit, RestoreEntity und
Entfernen aus register/state (Step 5.3)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorExtraStoredData
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.hacc import connection as conn
from custom_components.hacc.const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DEVICE_MANUFACTURER,
    DOMAIN,
    PROTOCOL_VERSION,
    WS_PATH,
)
from custom_components.hacc.pairing import generate_device_key, hash_device_key

DEVICE_NAME = "buero_pc"
APP_VERSION = "0.5.0"
OS_VERSION = "Windows-11-10.0.22631"
TIMEOUT = 5.0
CPU_ENTITY = {
    "key": "cpu",
    "name": "CPU-Auslastung",
    "domain": "sensor",
    "icon": "mdi:cpu-64-bit",
    "unit": "%",
    "device_class": None,
    "state_class": "measurement",
    "attributes": {},
}
LOCKED_ENTITY = {
    "key": "locked",
    "name": "Gesperrt",
    "domain": "binary_sensor",
    "icon": None,
    "unit": None,
    "device_class": None,
    "state_class": None,
    "attributes": {},
}


async def _wait_for(predicate: Callable[[], bool], seconds: float = TIMEOUT) -> None:
    """Pollt, bis die Bedingung erfüllt ist - sonst schlägt der Test fehl.

    Nötig, weil die Nachrichten über einen echten WebSocket laufen: ``send_json``
    liefert zurück, sobald der Client geschrieben hat, nicht wenn der
    Server-Task sie gelesen und verarbeitet hat - ``hass.async_block_till_done()``
    wartet nur auf von HA selbst verfolgte Jobs, nicht auf diesen rohen Socket-Transfer.
    """
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
        data={
            DATA_DEVICE_ID: device_id,
            DATA_DEVICE_KEY_HASH: hash_device_key(device_key),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return device_id, device_key, entry


def _ws_url(device_id: str) -> str:
    return f"{WS_PATH}?device_id={device_id}"


def _auth_headers(device_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {device_key}"}


async def _connect_and_register(
    hass: HomeAssistant,
    client: ClientSessionGenerator,
    device_id: str,
    device_key: str,
    entities: list[dict[str, Any]],
    *,
    device_name: str = DEVICE_NAME,
) -> Any:
    """Verbinden, hello/hello_ok, register schicken - wartet, bis HA das Gerät
    daraus angelegt hat, und liefert das offene ws zurück."""
    http_client = await client()
    ws = await http_client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key))
    await ws.send_json(
        {
            "type": "hello",
            "protocol_version": PROTOCOL_VERSION,
            "device_name": device_name,
            "app_version": APP_VERSION,
            "os_version": OS_VERSION,
        }
    )
    await ws.receive_json()  # hello_ok
    await ws.send_json({"type": "register", "device_name": device_name, "entities": entities})
    await _wait_for(lambda: _device(hass, device_id) is not None)
    return ws


def _device(hass: HomeAssistant, device_id: str) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, device_id)})


def _entity_id(hass: HomeAssistant, domain: str, device_id: str, key: str) -> str | None:
    registry = er.async_get(hass)
    return registry.async_get_entity_id(domain, DOMAIN, f"{device_id}_{key}")


async def test_register_creates_device(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY])

    device = _device(hass, device_id)
    assert device is not None
    assert device.name == DEVICE_NAME
    assert device.manufacturer == DEVICE_MANUFACTURER
    assert device.model == OS_VERSION
    assert device.sw_version == APP_VERSION
    assert device.config_entries == {entry.entry_id}

    await ws.close()


async def test_register_creates_entity_dynamically(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY])

    await _wait_for(lambda: _entity_id(hass, "sensor", device_id, "cpu") is not None)
    entity_id = _entity_id(hass, "sensor", device_id, "cpu")
    assert entity_id == "sensor.buero_pc_cpu"
    assert hass.states.get(entity_id) is not None

    await ws.close()


async def test_state_message_updates_entity(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY])
    await _wait_for(lambda: _entity_id(hass, "sensor", device_id, "cpu") is not None)
    entity_id = _entity_id(hass, "sensor", device_id, "cpu")

    await ws.send_json(
        {"type": "state", "states": [{"key": "cpu", "state": "42.0", "attributes": {"cores": 8}}]}
    )
    await _wait_for(
        lambda: (
            hass.states.get(entity_id) is not None and hass.states.get(entity_id).state == "42.0"
        )
    )

    state = hass.states.get(entity_id)
    assert state.attributes["cores"] == 8

    await ws.close()


async def test_timestamp_sensor_survives_connection_signal(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Ein timestamp-Sensor bekommt seinen Wert als String vom Draht; HA verlangt ein
    datetime und warf beim Schreiben des Zustands (auch im Verbindungs-Signal) einen
    ValueError."""
    uptime_entity = {**CPU_ENTITY, "key": "uptime", "unit": None, "device_class": "timestamp"}
    uptime_entity["state_class"] = None
    device_id, device_key, entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [uptime_entity]
    )
    await _wait_for(lambda: _entity_id(hass, "sensor", device_id, "uptime") is not None)
    entity_id = _entity_id(hass, "sensor", device_id, "uptime")

    await ws.send_json(
        {
            "type": "state",
            "states": [
                {"key": "uptime", "state": "2026-09-10T21:24:25.592697+00:00", "attributes": {}}
            ],
        }
    )
    await _wait_for(lambda: hass.states.get(entity_id).state == "2026-09-10T21:24:25+00:00")

    conn.clear_connection(hass, entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    await ws.close()


async def test_availability_follows_connection(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY])
    await _wait_for(lambda: _entity_id(hass, "sensor", device_id, "cpu") is not None)
    entity_id = _entity_id(hass, "sensor", device_id, "cpu")

    await ws.send_json(
        {"type": "state", "states": [{"key": "cpu", "state": "10", "attributes": {}}]}
    )
    await _wait_for(lambda: hass.states.get(entity_id).state == "10")

    # Verbindungsabbruch direkt simulieren statt auf die Schließ-Erkennung des
    # aiohttp-Testservers zu warten - das ist derselbe Code, den http.py im
    # finally-Zweig des Lesegloops aufruft.
    conn.clear_connection(hass, entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    await ws.close()

    ws2 = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY]
    )
    # Zustand ist ohne erneutes state schon wieder da - er stand nur auf Pause.
    await _wait_for(lambda: hass.states.get(entity_id).state == "10")

    await ws2.close()


async def test_shrunk_register_removes_entity(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, _entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY, LOCKED_ENTITY]
    )
    await _wait_for(lambda: _entity_id(hass, "binary_sensor", device_id, "locked") is not None)
    cpu_id = _entity_id(hass, "sensor", device_id, "cpu")
    locked_id = _entity_id(hass, "binary_sensor", device_id, "locked")
    assert cpu_id is not None
    assert locked_id is not None

    await ws.send_json({"type": "register", "device_name": DEVICE_NAME, "entities": [CPU_ENTITY]})
    await _wait_for(lambda: _entity_id(hass, "binary_sensor", device_id, "locked") is None)

    assert hass.states.get(locked_id) is None
    assert hass.states.get(cpu_id) is not None

    await ws.close()


async def test_reload_restores_last_state_without_reconnect(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Nach einem HA-Neustart (hier simuliert: unload + reload) ist die Entität
    sofort wieder da (nur unavailable, keine Verbindung) und zeigt den restaurierten
    Wert, sobald die App wieder verbindet - ganz ohne dass sie den Wert erneut
    schicken musste."""
    device_id, device_key, entry = await _setup_paired_entry(hass)
    ws = await _connect_and_register(hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY])
    await _wait_for(lambda: _entity_id(hass, "sensor", device_id, "cpu") is not None)
    entity_id = _entity_id(hass, "sensor", device_id, "cpu")
    await ws.close()
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    mock_restore_cache_with_extra_data(
        hass,
        [(State(entity_id, "37.5"), SensorExtraStoredData(37.5, "%").as_dict())],
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Entität existiert sofort wieder (aus der Entity-Registry rekonstruiert),
    # aber ohne Verbindung logischerweise unavailable statt schon "37.5".
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Reconnect ohne erneutes state - der restaurierte Wert war die ganze Zeit da.
    ws2 = await _connect_and_register(
        hass, hass_client_no_auth, device_id, device_key, [CPU_ENTITY]
    )
    await _wait_for(lambda: hass.states.get(entity_id).state == "37.5")

    await ws2.close()


async def test_register_before_platform_setup_is_replayed(hass: HomeAssistant) -> None:
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

    # register kommt an, bevor die sensor-Plattform je gelaufen ist.
    state = conn.get_connection_state(hass, entry.entry_id)
    state.device_id = device_id
    conn.async_apply_register(hass, entry, DEVICE_NAME, [CPU_ENTITY])

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", device_id, "cpu")
    assert entity_id is not None
    assert hass.states.get(entity_id) is not None
