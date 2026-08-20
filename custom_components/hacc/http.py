"""HTTP views: the unauthenticated pairing endpoint and the device WebSocket.

Both views set requires_auth = False and must therefore treat every input as
hostile: no secret in a log line, and no error message that reveals whether a
code or device ever existed versus merely having expired.
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from http import HTTPStatus
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.service import async_get_all_descriptions

from . import access
from . import connection as conn
from .connection import clear_connection, get_connection_state, get_domain_data, replace_connection
from .const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DATA_DEVICE_NAME,
    DATA_PAIRING_CODE_EXPIRES_AT,
    DATA_PAIRING_CODE_HASH,
    DOMAIN,
    ERROR_ACCESS_DENIED,
    ERROR_ACCESS_PENDING,
    ERROR_INVALID_MESSAGE,
    ERROR_INVALID_OR_EXPIRED,
    ERROR_PROTOCOL_VERSION,
    HELLO_TIMEOUT_SECONDS,
    PAIR_PATH,
    PROTOCOL_VERSION,
    WS_HEARTBEAT_SECONDS,
    WS_PATH,
)
from .pairing import (
    PairingCodeState,
    generate_device_key,
    hash_device_key,
    hash_pairing_code,
    is_pair_rate_limited,
    register_pair_failure,
)

_LOGGER = logging.getLogger(__name__)

MAX_DEVICE_NAME_LENGTH = 64
_STALE_PAIRING_KEYS = (DATA_PAIRING_CODE_HASH, DATA_PAIRING_CODE_EXPIRES_AT)


def _generic_pair_error() -> web.Response:
    # Deliberately identical for "no such code", "expired" and "rate limited" -
    # nothing here may leak which one it was.
    return web.json_response(
        {
            "error": {
                "code": ERROR_INVALID_OR_EXPIRED,
                "message": "Code ungültig oder abgelaufen.",
            }
        },
        status=HTTPStatus.BAD_REQUEST,
    )


def _pending_pairing_entries(hass: HomeAssistant) -> list[PairingCodeState]:
    pending = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        code_hash = entry.data.get(DATA_PAIRING_CODE_HASH)
        expires_at_iso = entry.data.get(DATA_PAIRING_CODE_EXPIRES_AT)
        if code_hash and expires_at_iso:
            pending.append(PairingCodeState(entry.entry_id, code_hash, expires_at_iso))
    return pending


class PairView(HomeAssistantView):
    """POST /api/hacc/pair - exchange a one-time pairing code for a device key."""

    url = PAIR_PATH
    name = f"api:{DOMAIN}:pair"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        domain_data = get_domain_data(hass)

        if is_pair_rate_limited(domain_data):
            return _generic_pair_error()

        try:
            body: Any = await request.json()
        except ValueError:
            register_pair_failure(domain_data)
            return _generic_pair_error()

        code = body.get("code") if isinstance(body, dict) else None
        device_name = body.get("device_name") if isinstance(body, dict) else None
        if not isinstance(code, str) or not code.strip():
            register_pair_failure(domain_data)
            return _generic_pair_error()
        if not isinstance(device_name, str) or not device_name.strip():
            register_pair_failure(domain_data)
            return _generic_pair_error()

        code_hash = hash_pairing_code(code)

        for pending in _pending_pairing_entries(hass):
            if not hmac.compare_digest(pending.code_hash, code_hash):
                continue

            entry = hass.config_entries.async_get_entry(pending.entry_id)
            if entry is None or pending.is_expired():
                break  # matched but unusable - fall through to the generic error

            return self._complete_pairing(hass, entry, device_name.strip())

        register_pair_failure(domain_data)
        return _generic_pair_error()

    @staticmethod
    def _complete_pairing(
        hass: HomeAssistant, entry: ConfigEntry, device_name: str
    ) -> web.Response:
        device_id = uuid.uuid4().hex
        device_key = generate_device_key()
        clean_name = device_name[:MAX_DEVICE_NAME_LENGTH]

        new_data = {k: v for k, v in entry.data.items() if k not in _STALE_PAIRING_KEYS}
        new_data.update(
            {
                DATA_DEVICE_ID: device_id,
                DATA_DEVICE_KEY_HASH: hash_device_key(device_key),
                DATA_DEVICE_NAME: clean_name,
            }
        )
        hass.config_entries.async_update_entry(entry, data=new_data, title=clean_name)
        _LOGGER.info("Gerät gekoppelt für ConfigEntry %s", entry.entry_id)

        return web.json_response({"device_id": device_id, "device_key": device_key})


async def _send_json(ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
    await ws.send_json(payload)


async def _send_error(
    ws: web.WebSocketResponse, code: str, message: str, msg_id: str | None = None
) -> None:
    await _send_json(ws, {"type": "error", "id": msg_id, "code": code, "message": message})


async def _send_result(
    ws: web.WebSocketResponse, msg_id: Any, success: bool, code: str = "", message: str = ""
) -> None:
    error = None if success else {"code": code, "message": message}
    await _send_json(ws, {"type": "result", "id": msg_id, "success": success, "error": error})


async def _handle_call_service(
    hass: HomeAssistant, entry: ConfigEntry, ws: web.WebSocketResponse, payload: dict[str, Any]
) -> None:
    """`call_service`-Nachricht verarbeiten (Step 5.5) - siehe PROTOCOL.md.

    Fremde Services laufen nur mit ausdrücklicher Freigabe durch; ohne sie
    entsteht (gedeckelt, mit Denied-bleibt-Denied) automatisch eine Anfrage
    im Options-Flow, und die Antwort kommt sofort statt in einen Timeout zu
    laufen - die Ablehnung ist ja schon bekannt."""
    msg_id = payload.get("id")
    domain = str(payload.get("domain") or "")
    service = str(payload.get("service") or "")
    if not domain or not service:
        await _send_result(ws, msg_id, False, ERROR_INVALID_MESSAGE, "domain/service fehlt")
        return

    target = payload.get("target")
    target_entity = target.get("entity_id") if isinstance(target, dict) else None
    if isinstance(target_entity, list):
        target_entity = target_entity[0] if target_entity else None

    if not access.is_call_granted(entry, domain, service, target_entity):
        grant = access.call_grants(entry).get(access.call_key(domain, service))
        if grant is not None and grant.status is access.AccessStatus.DENIED:
            await _send_result(
                ws, msg_id, False, ERROR_ACCESS_DENIED, "Zugriff in Home Assistant abgelehnt."
            )
        elif grant is not None and grant.status is access.AccessStatus.GRANTED:
            # Freigegeben, aber nicht für dieses Ziel - keine neue Anfrage nötig
            # (request_call würde bei granted ohnehin nichts ändern).
            await _send_result(ws, msg_id, False, ERROR_ACCESS_DENIED, "Ziel nicht freigegeben.")
        else:
            access.request_call(hass, entry, domain, service, target_entity)
            await _send_result(
                ws,
                msg_id,
                False,
                ERROR_ACCESS_PENDING,
                "Noch nicht in Home Assistant bestätigt.",
            )
            # Erst die eindeutige Antwort auf diesen Aufruf, dann die (separate)
            # Tabellen-Aktualisierung - der Aufrufer soll nicht auf sie warten müssen.
            await conn.async_send_access(hass, entry)
        return

    service_data = payload.get("service_data")
    try:
        await hass.services.async_call(
            domain,
            service,
            dict(service_data) if isinstance(service_data, dict) else {},
            target=target if isinstance(target, dict) else None,
            blocking=True,
        )
    except (ServiceNotFound, HomeAssistantError, vol.Invalid) as exc:
        await _send_result(ws, msg_id, False, "failed", str(exc))
        return
    await _send_result(ws, msg_id, True)


async def _handle_catalog(
    hass: HomeAssistant, entry: ConfigEntry, ws: web.WebSocketResponse, payload: dict[str, Any]
) -> None:
    """`catalog`-Anfrage beantworten (Step 5.5) - nur bereits Freigegebenes,
    plus Namen-only, wenn der Such-Modus für dieses Gerät an ist."""
    entities: list[dict[str, Any]] = []
    for entity_id in access.granted_read_entities(entry):
        ha_state = hass.states.get(entity_id)
        if ha_state is not None:
            entities.append(
                {
                    "entity_id": entity_id,
                    "state": ha_state.state,
                    "attributes": dict(ha_state.attributes),
                    "last_updated": ha_state.last_updated.timestamp(),
                }
            )

    descriptions = await async_get_all_descriptions(hass)
    services: list[dict[str, Any]] = []
    for key in access.granted_call_keys(entry):
        domain, _, service = key.partition(".")
        info = descriptions.get(domain, {}).get(service) or {}
        services.append(
            {
                "domain": domain,
                "service": service,
                "name": info.get("name", ""),
                "description": info.get("description", ""),
            }
        )

    result: dict[str, Any] = {
        "type": "catalog",
        "id": payload.get("id"),
        "entities": entities,
        "services": services,
    }
    if access.discovery_enabled(entry):
        result["discoverable_entities"] = [
            {"entity_id": s.entity_id, "name": s.attributes.get("friendly_name", "")}
            for s in hass.states.async_all()
        ]
        result["discoverable_services"] = [
            {
                "domain": domain,
                "service": service,
                "name": (descriptions.get(domain, {}).get(service) or {}).get("name", ""),
            }
            for domain, services_map in hass.services.async_services().items()
            for service in services_map
        ]
    await _send_json(ws, result)


class WebSocketView(HomeAssistantView):
    """GET /api/hacc/ws - the persistent, device-key-authenticated connection."""

    url = WS_PATH
    name = f"api:{DOMAIN}:ws"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        device_id = request.query.get("device_id")
        device_key = request.query.get("device_key")
        if not device_id or not device_key:
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        entry = self._find_entry_for_device(hass, device_id)
        if entry is None:
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        stored_hash = entry.data.get(DATA_DEVICE_KEY_HASH)
        if not stored_hash or not hmac.compare_digest(stored_hash, hash_device_key(device_key)):
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        # Erst ab hier legt HA etwas an - der Key ist geprüft, bevor der Upgrade passiert.
        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT_SECONDS)
        await ws.prepare(request)

        hello = await self._handshake(ws, entry)
        if hello is None:
            await ws.close()
            return ws

        await replace_connection(hass, entry.entry_id, device_id, ws)
        conn.async_set_hello_info(
            hass,
            entry.entry_id,
            str(hello.get("device_name") or entry.title),
            hello.get("app_version"),
            hello.get("os_version"),
        )
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_message(hass, entry, ws, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    _LOGGER.warning(
                        "hacc-WebSocket-Fehler für ConfigEntry %s: %s",
                        entry.entry_id,
                        ws.exception(),
                    )
        finally:
            if get_connection_state(hass, entry.entry_id).ws is ws:
                clear_connection(hass, entry.entry_id)

        return ws

    @staticmethod
    async def _handle_message(
        hass: HomeAssistant, entry: ConfigEntry, ws: web.WebSocketResponse, raw: str
    ) -> None:
        try:
            payload = json.loads(raw)
        except ValueError:
            _LOGGER.warning("Ungültiges JSON von ConfigEntry %s ignoriert", entry.entry_id)
            return
        if not isinstance(payload, dict):
            return

        kind = payload.get("type")
        if kind == "register":
            conn.async_apply_register(
                hass,
                entry,
                str(payload.get("device_name") or entry.title),
                payload.get("entities") or [],
            )
        elif kind == "state":
            conn.async_apply_state(hass, entry.entry_id, payload.get("states") or [])
        elif kind == "result":
            conn.async_apply_command_result(hass, entry, payload)
        elif kind == "event":
            conn.async_apply_event(hass, entry, payload)
        elif kind == "subscribe":
            await conn.async_apply_subscribe(hass, entry, payload.get("entities") or [])
        elif kind == "call_service":
            await _handle_call_service(hass, entry, ws, payload)
        elif kind == "catalog":
            await _handle_catalog(hass, entry, ws, payload)
        # Unbekannte Typen werden bewusst stillschweigend ignoriert (Vorwärtskompatibilität).

    @staticmethod
    def _find_entry_for_device(hass: HomeAssistant, device_id: str) -> ConfigEntry | None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(DATA_DEVICE_ID) == device_id:
                return entry
        return None

    @staticmethod
    async def _handshake(ws: web.WebSocketResponse, entry: ConfigEntry) -> dict[str, Any] | None:
        """hello annehmen, hello_ok/error beantworten; liefert die hello-Payload zurück."""
        try:
            msg = await ws.receive(timeout=HELLO_TIMEOUT_SECONDS)
        except TimeoutError:
            await _send_error(
                ws, ERROR_INVALID_MESSAGE, "Zeitüberschreitung beim Warten auf hello."
            )
            return None

        if msg.type != web.WSMsgType.TEXT:
            await _send_error(ws, ERROR_INVALID_MESSAGE, "Erwarte eine hello-Nachricht.")
            return None

        try:
            payload = json.loads(msg.data)
        except ValueError:
            await _send_error(ws, ERROR_INVALID_MESSAGE, "Ungültiges JSON.")
            return None

        if not isinstance(payload, dict) or payload.get("type") != "hello":
            await _send_error(ws, ERROR_INVALID_MESSAGE, "Erwarte eine hello-Nachricht.")
            return None

        if payload.get("protocol_version") != PROTOCOL_VERSION:
            _LOGGER.warning(
                "Gerät %s spricht Protokollversion %s, HA erwartet %s - Verbindung abgelehnt.",
                entry.data.get(DATA_DEVICE_ID),
                payload.get("protocol_version"),
                PROTOCOL_VERSION,
            )
            await _send_error(
                ws,
                ERROR_PROTOCOL_VERSION,
                f"HA erwartet Protokollversion {PROTOCOL_VERSION}.",
            )
            return None

        await _send_json(
            ws,
            {
                "type": "hello_ok",
                "protocol_version": PROTOCOL_VERSION,
                "ha_version": HA_VERSION,
                "device_id": entry.data.get(DATA_DEVICE_ID),
            },
        )
        return payload
