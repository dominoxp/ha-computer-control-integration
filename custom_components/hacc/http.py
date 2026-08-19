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

from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import HomeAssistantView

from .connection import clear_connection, get_connection_state, get_domain_data, replace_connection
from .const import (
    DATA_DEVICE_ID,
    DATA_DEVICE_KEY_HASH,
    DATA_DEVICE_NAME,
    DATA_PAIRING_CODE_EXPIRES_AT,
    DATA_PAIRING_CODE_HASH,
    DOMAIN,
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

        if not await self._handshake(ws, entry):
            await ws.close()
            return ws

        await replace_connection(hass, entry.entry_id, device_id, ws)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.ERROR:
                    _LOGGER.warning(
                        "hacc-WebSocket-Fehler für ConfigEntry %s: %s",
                        entry.entry_id,
                        ws.exception(),
                    )
                # Weitere Nachrichtentypen (register/state/command/...) kommen ab Step 5.2.
        finally:
            if get_connection_state(hass, entry.entry_id).ws is ws:
                clear_connection(hass, entry.entry_id)

        return ws

    @staticmethod
    def _find_entry_for_device(hass: HomeAssistant, device_id: str) -> ConfigEntry | None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(DATA_DEVICE_ID) == device_id:
                return entry
        return None

    @staticmethod
    async def _handshake(ws: web.WebSocketResponse, entry: ConfigEntry) -> bool:
        try:
            msg = await ws.receive(timeout=HELLO_TIMEOUT_SECONDS)
        except TimeoutError:
            await _send_error(
                ws, ERROR_INVALID_MESSAGE, "Zeitüberschreitung beim Warten auf hello."
            )
            return False

        if msg.type != web.WSMsgType.TEXT:
            await _send_error(ws, ERROR_INVALID_MESSAGE, "Erwarte eine hello-Nachricht.")
            return False

        try:
            payload = json.loads(msg.data)
        except ValueError:
            await _send_error(ws, ERROR_INVALID_MESSAGE, "Ungültiges JSON.")
            return False

        if not isinstance(payload, dict) or payload.get("type") != "hello":
            await _send_error(ws, ERROR_INVALID_MESSAGE, "Erwarte eine hello-Nachricht.")
            return False

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
            return False

        await _send_json(
            ws,
            {
                "type": "hello_ok",
                "protocol_version": PROTOCOL_VERSION,
                "ha_version": HA_VERSION,
                "device_id": entry.data.get(DATA_DEVICE_ID),
            },
        )
        return True
