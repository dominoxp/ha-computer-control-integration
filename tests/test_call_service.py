"""Tests für die Freigabe-Durchsetzung bei `call_service` (Step 5.5) -
siehe PROTOCOL.md, Abschnitt `call_service`/`result`."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.hacc import access
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
        data={DATA_DEVICE_ID: device_id, DATA_DEVICE_KEY_HASH: hash_device_key(device_key)},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return device_id, device_key, entry


def _ws_url(device_id: str) -> str:
    return f"{WS_PATH}?device_id={device_id}"


def _auth_headers(device_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {device_key}"}


async def _hello(ws: Any) -> None:
    await ws.send_json({"type": "hello", "protocol_version": PROTOCOL_VERSION, "device_name": "PC"})
    await ws.receive_json()


def _recorder(calls: list[dict[str, Any]]) -> Any:
    """Async Service-Handler statt sync-Lambda - ein sync-Handler liefe über
    HAs Executor-Thread-Pool und stört das Thread-Aufräumen der Testumgebung."""

    async def _handler(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    return _handler


async def test_granted_call_executes_the_real_service(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, entry = await _setup_paired_entry(hass)
    access.grant_call(hass, entry, "test", "do_it", [])
    calls: list[dict[str, Any]] = []
    hass.services.async_register("test", "do_it", _recorder(calls))
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key)) as ws:
        await _hello(ws)
        await ws.send_json(
            {
                "type": "call_service",
                "id": 1,
                "domain": "test",
                "service": "do_it",
                "service_data": {"x": 1},
            }
        )
        result = await ws.receive_json()

    assert result == {"type": "result", "id": 1, "success": True, "error": None}
    assert calls == [{"x": 1}]


async def test_ungranted_call_is_rejected_without_calling_the_service(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, entry = await _setup_paired_entry(hass)
    calls: list[Any] = []
    hass.services.async_register("test", "do_it", lambda call: calls.append(call))
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key)) as ws:
        await _hello(ws)
        await ws.send_json({"type": "call_service", "id": 2, "domain": "test", "service": "do_it"})
        result = await ws.receive_json()

    assert result["success"] is False
    assert result["error"]["code"] == "access_pending"
    assert calls == []
    assert access.call_grants(entry)["test.do_it"].status is access.AccessStatus.REQUESTED


async def test_denied_call_is_rejected_and_does_not_re_request(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, entry = await _setup_paired_entry(hass)
    access.request_call(hass, entry, "test", "do_it", None)
    access.set_call_status(hass, entry, ["test.do_it"], access.AccessStatus.DENIED)
    first_timestamp = access.call_grants(entry)["test.do_it"].requested_at
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key)) as ws:
        await _hello(ws)
        await ws.send_json({"type": "call_service", "id": 3, "domain": "test", "service": "do_it"})
        result = await ws.receive_json()

    assert result["error"]["code"] == "access_denied"
    grant = access.call_grants(entry)["test.do_it"]
    assert grant.status is access.AccessStatus.DENIED
    assert grant.requested_at == first_timestamp


async def test_call_restricted_to_a_different_target_is_rejected(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    device_id, device_key, entry = await _setup_paired_entry(hass)
    access.grant_call(hass, entry, "test", "do_it", ["light.buero"])
    calls: list[Any] = []
    hass.services.async_register("test", "do_it", lambda call: calls.append(call))
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key)) as ws:
        await _hello(ws)
        await ws.send_json(
            {
                "type": "call_service",
                "id": 4,
                "domain": "test",
                "service": "do_it",
                "target": {"entity_id": "light.kueche"},
            }
        )
        result = await ws.receive_json()

    assert result["success"] is False
    assert result["error"]["code"] == "access_denied"
    assert calls == []
    # Kein neuer Antrag - der Service ist ja schon (für ein anderes Ziel) freigegeben.
    assert access.call_grants(entry)["test.do_it"].status is access.AccessStatus.GRANTED


async def test_call_restricted_to_a_target_rejects_extra_entities_in_a_list(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Regression: eine Freigabe für ein Ziel darf nicht durch eine Entity-Id-
    Liste, die das erlaubte Ziel nur als erstes Element enthält, auf weitere
    Entitäten ausgeweitet werden können."""
    device_id, device_key, entry = await _setup_paired_entry(hass)
    access.grant_call(hass, entry, "test", "do_it", ["light.buero"])
    calls: list[Any] = []
    hass.services.async_register("test", "do_it", lambda call: calls.append(call))
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key)) as ws:
        await _hello(ws)
        await ws.send_json(
            {
                "type": "call_service",
                "id": 5,
                "domain": "test",
                "service": "do_it",
                "target": {"entity_id": ["light.buero", "light.schlafzimmer"]},
            }
        )
        result = await ws.receive_json()

    assert result["success"] is False
    assert result["error"]["code"] == "access_denied"
    assert calls == []


async def test_call_restricted_to_a_target_rejects_an_area_selector(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Regression: ein zusätzlicher area_id-Selektor neben der erlaubten
    entity_id darf die Freigabe nicht auf eine ganze Area ausweiten."""
    device_id, device_key, entry = await _setup_paired_entry(hass)
    access.grant_call(hass, entry, "test", "do_it", ["light.buero"])
    calls: list[Any] = []
    hass.services.async_register("test", "do_it", lambda call: calls.append(call))
    client = await hass_client_no_auth()

    async with client.ws_connect(_ws_url(device_id), headers=_auth_headers(device_key)) as ws:
        await _hello(ws)
        await ws.send_json(
            {
                "type": "call_service",
                "id": 6,
                "domain": "test",
                "service": "do_it",
                "target": {"entity_id": "light.buero", "area_id": "wohnzimmer"},
            }
        )
        result = await ws.receive_json()

    assert result["success"] is False
    assert result["error"]["code"] == "access_denied"
    assert calls == []
