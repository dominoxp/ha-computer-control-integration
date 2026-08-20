"""Runtime state that lives in hass.data: one ConnectionState per ConfigEntry.

Nothing here is persisted - it is rebuilt from scratch on every HA restart, and
that is fine because it only describes the *current* transport plus the manifest
the connected PC last sent, not anything a user configured. Entity availability
reads ConnectionState.connected; entity creation/removal reacts to `manifest`.

Eine Ausnahme: Direkt nach einem HA-Neustart ist `manifest` leer, bis die App
erneut `register` schickt - ohne Gegenmaßnahme blieben Entitäten bis zum
nächsten Reconnect komplett weg statt nur `unavailable` zu sein. Deshalb seeden
die Plattformen `manifest` beim Setup zusätzlich aus der (persistierten)
Entity-Registry (:func:`async_seed_manifest_from_registry`) - RestoreEntity
greift dann sofort, ein echtes `register` frischt Icon/Einheit/Klassen danach
über :meth:`HaccEntity.async_apply_descriptor` auf.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from aiohttp import WSCloseCode
from aiohttp.web import WebSocketResponse
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DATA_DEVICE_ID,
    DEVICE_MANUFACTURER,
    DOMAIN,
    EVENT_COMMAND_RESULT,
    EVENT_TYPE_MAP,
    SIGNAL_CONNECTION_STATE,
    SIGNAL_ENTITY_REGISTERED,
)

if TYPE_CHECKING:
    from .entity import HaccEntity

_LOGGER = logging.getLogger(__name__)

_KNOWN_DOMAINS = ("sensor", "binary_sensor", "button", "select", "number", "switch")


class CommandOutcome(StrEnum):
    """Ausgang eines an den PC geschickten ``command`` - siehe PROTOCOL.md."""

    EXECUTED = "executed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(slots=True, frozen=True)
class RegisteredEntity:
    """Deckungsgleich mit hacc.core.entities.EntityDef auf der Windows-Seite -
    kein zweites Datenmodell, nur die HA-seitige Sicht auf dieselben Felder."""

    key: str
    name: str
    domain: str  # "sensor" | "binary_sensor" | "button" | "select" | "number" | "switch"
    icon: str | None
    unit: str | None
    device_class: str | None
    state_class: str | None
    attributes: dict[str, Any]
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    command: str | None = None
    """Welcher command bei einer Interaktion an den PC geschickt wird - ``None``
    heisst reine Anzeige-Entität (gilt hier praktisch nie, siehe _KNOWN_DOMAINS)."""
    command_data: dict[str, Any] = field(default_factory=dict)
    value_field: str = "value"


@dataclass(slots=True)
class PendingCommand:
    """Ein an den PC geschickter ``command``, der noch auf ``result`` wartet."""

    future: asyncio.Future[tuple[CommandOutcome, str]]
    command: str


@dataclass(slots=True)
class RawState:
    """Letzter roher `state`-Wert eines Keys, gecacht für den Fall, dass er

    ankommt, bevor die zugehörige Entität geladen ist (Reihenfolge zwischen
    Plattform-Setup und WebSocket-Nachrichten ist nicht garantiert)."""

    state: str
    attributes: dict[str, Any]


@dataclass(slots=True)
class ConnectionState:
    """Live connection state of one paired PC."""

    device_id: str | None = None
    connected: bool = False
    last_hello_at: datetime | None = None
    ws: WebSocketResponse | None = None
    device_name: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    manifest: dict[str, RegisteredEntity] = field(default_factory=dict)
    raw_states: dict[str, RawState] = field(default_factory=dict)
    live_entities: dict[str, HaccEntity] = field(default_factory=dict)
    next_command_id: int = 0
    pending_commands: dict[int, PendingCommand] = field(default_factory=dict)


@dataclass(slots=True)
class DomainData:
    """Everything the integration keeps in hass.data[DOMAIN]."""

    views_registered: bool = False
    connections: dict[str, ConnectionState] = field(default_factory=dict)  # entry_id -> state
    pair_failure_times: list[datetime] = field(default_factory=list)


def get_domain_data(hass: HomeAssistant) -> DomainData:
    """Return (creating if necessary) this integration's hass.data bucket."""
    return hass.data.setdefault(DOMAIN, DomainData())


def get_connection_state(hass: HomeAssistant, entry_id: str) -> ConnectionState:
    """Return (creating if necessary) the connection state for one ConfigEntry."""
    domain_data = get_domain_data(hass)
    return domain_data.connections.setdefault(entry_id, ConnectionState())


async def replace_connection(
    hass: HomeAssistant, entry_id: str, device_id: str, ws: WebSocketResponse
) -> None:
    """Register a freshly authenticated connection, closing any previous one.

    A second connection from the same device replaces the first instead of both
    lingering half-alive.
    """
    state = get_connection_state(hass, entry_id)
    old_ws = state.ws
    state.device_id = device_id
    state.ws = ws
    state.connected = True

    if old_ws is not None and not old_ws.closed:
        await old_ws.close(code=WSCloseCode.POLICY_VIOLATION, message=b"replaced by new connection")
    async_dispatcher_send(hass, SIGNAL_CONNECTION_STATE.format(entry_id), None)


def clear_connection(hass: HomeAssistant, entry_id: str) -> None:
    """Mark a device's connection as gone - entities go unavailable, nothing is removed."""
    state = get_connection_state(hass, entry_id)
    state.connected = False
    state.ws = None
    async_dispatcher_send(hass, SIGNAL_CONNECTION_STATE.format(entry_id), None)


def async_set_hello_info(
    hass: HomeAssistant,
    entry_id: str,
    device_name: str,
    app_version: str | None,
    os_version: str | None,
) -> None:
    """Vom Handshake aufgerufen - hello trägt device_name/app_version/os_version,
    register später nur noch den (evtl. gleichen) device_name. Wird beim
    Geräte-Anlegen in async_apply_register für Modell/Software-Version gebraucht."""
    state = get_connection_state(hass, entry_id)
    state.device_name = device_name
    state.app_version = app_version
    state.os_version = os_version


def pending_entity_keys(state: ConnectionState) -> list[str]:
    """Manifest-Keys ohne lebende Entität - neu anzulegen.

    Das umfasst auch unveränderte Keys, deren Entität z.B. über die HA-UI
    gelöscht wurde: das nächste register legt sie bewusst wieder an."""
    return [key for key in state.manifest if key not in state.live_entities]


def async_seed_manifest_from_registry(hass: HomeAssistant, entry: ConfigEntry, domain: str) -> None:
    """Manifest-Lücken direkt nach einem HA-Neustart aus der Entity-Registry füllen.

    `ConnectionState.manifest` ist nicht persistiert; ohne diese Rekonstruktion
    blieben Entitäten bis zum nächsten `register` der App komplett verschwunden
    statt nur `unavailable`. Die device_id kommt bewusst aus `entry.data`, nicht
    aus `state.device_id` - Letzteres ist vor der ersten Verbindung noch leer.
    """
    state = get_connection_state(hass, entry.entry_id)
    device_id = entry.data.get(DATA_DEVICE_ID)
    if not device_id:
        return
    if state.device_id is None:
        # Noch keine Verbindung stand - HaccEntity.__init__() braucht die device_id
        # trotzdem schon für unique_id/device_info der gleich erzeugten Entitäten.
        state.device_id = device_id
    prefix = f"{device_id}_"

    for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if reg_entry.domain != domain or not reg_entry.unique_id.startswith(prefix):
            continue
        key = reg_entry.unique_id.removeprefix(prefix)
        if key in state.manifest:
            continue
        state.manifest[key] = RegisteredEntity(
            key=key,
            name=reg_entry.original_name or key,
            domain=domain,
            icon=reg_entry.original_icon,
            unit=reg_entry.unit_of_measurement,
            device_class=reg_entry.original_device_class,
            state_class=None,  # nicht in der Registry gespeichert - kommt mit dem nächsten register
            attributes={},
        )


def _parse_manifest(entities: list[Any]) -> dict[str, RegisteredEntity]:
    parsed: dict[str, RegisteredEntity] = {}
    for entry in entities:
        if not isinstance(entry, dict):
            _LOGGER.warning("register: Eintrag kein Objekt - ignoriert: %r", entry)
            continue
        key = entry.get("key")
        name = entry.get("name")
        domain = entry.get("domain")
        if not key or not name or domain not in _KNOWN_DOMAINS:
            _LOGGER.warning("register: unbrauchbarer Eintrag - ignoriert: %r", entry)
            continue
        parsed[key] = RegisteredEntity(
            key=key,
            name=name,
            domain=domain,
            icon=entry.get("icon"),
            unit=entry.get("unit"),
            device_class=entry.get("device_class"),
            state_class=entry.get("state_class"),
            attributes=dict(entry.get("attributes") or {}),
            min_value=entry.get("min"),
            max_value=entry.get("max"),
            step=entry.get("step"),
            command=entry.get("command"),
            command_data=dict(entry.get("command_data") or {}),
            value_field=str(entry.get("value_field") or "value"),
        )
    return parsed


def async_apply_register(
    hass: HomeAssistant, entry: ConfigEntry, device_name: str, entities: list[Any]
) -> None:
    """register-Nachricht verarbeiten: Gerät anlegen/aktualisieren, verschwundene
    Entitäten aus der Entity-Registry entfernen, bestehende auffrischen und neue
    per Signal bekannt machen."""
    state = get_connection_state(hass, entry.entry_id)
    state.device_name = device_name

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, state.device_id)},
        name=device_name,
        manufacturer=DEVICE_MANUFACTURER,
        model=state.os_version,
        sw_version=state.app_version,
    )

    new_manifest = _parse_manifest(entities)

    removed_keys = state.manifest.keys() - new_manifest.keys()
    if removed_keys:
        entity_registry = er.async_get(hass)
        for key in removed_keys:
            old = state.manifest[key]
            unique_id = f"{state.device_id}_{key}"
            entity_id = entity_registry.async_get_entity_id(old.domain, DOMAIN, unique_id)
            if entity_id is not None:
                # Löscht auch eine evtl. lebende Instanz mit - Entity._async_process_
                # registry_update_or_remove hört selbst auf diese Registry-Änderung.
                entity_registry.async_remove(entity_id)
            state.raw_states.pop(key, None)

    state.manifest = new_manifest

    for key, descriptor in new_manifest.items():
        live = state.live_entities.get(key)
        if live is not None:
            live.async_apply_descriptor(descriptor)

    async_dispatcher_send(
        hass, SIGNAL_ENTITY_REGISTERED.format(entry.entry_id), pending_entity_keys(state)
    )


def async_apply_state(hass: HomeAssistant, entry_id: str, states: list[Any]) -> None:
    """state-Nachricht verarbeiten: Rohwert cachen und eine schon geladene
    Entität sofort aktualisieren."""
    state = get_connection_state(hass, entry_id)
    for entry in states:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if key is None or key not in state.manifest:
            continue  # unbekannter oder inzwischen entfernter Key - ignorieren
        value = str(entry.get("state", ""))
        attributes = dict(entry.get("attributes") or {})
        state.raw_states[key] = RawState(value, attributes)
        live = state.live_entities.get(key)
        if live is not None:
            live.async_apply_state(value, attributes)


def async_fire_command_result(
    hass: HomeAssistant, entry_id: str, command: str, outcome: CommandOutcome, reason: str = ""
) -> None:
    """`hacc_command_result` feuern - der eine Ort, der das tut.

    Läuft sowohl für ein echtes `result` vom PC (:func:`async_apply_command_result`)
    als auch für einen lokalen Timeout (:func:`hacc.commands.async_send_command`),
    damit eine Automation den Ausgang so oder so sieht.
    """
    state = get_connection_state(hass, entry_id)
    hass.bus.async_fire(
        EVENT_COMMAND_RESULT,
        {
            "device_id": state.device_id,
            "command": command,
            "result": outcome.value,
            "reason": reason,
        },
    )


def async_apply_command_result(
    hass: HomeAssistant, entry: ConfigEntry, payload: dict[str, Any]
) -> None:
    """result-Nachricht auf einen command verarbeiten: die wartende Future lösen
    (falls noch jemand wartet) und immer `hacc_command_result` feuern - auch
    wenn der Aufrufer inzwischen in einen Timeout gelaufen ist, soll eine
    Automation die Wahrheit sehen."""
    state = get_connection_state(hass, entry.entry_id)
    command_id = payload.get("id")
    success = bool(payload.get("success", False))
    error = payload.get("error")
    code = ""
    message = ""
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        message = str(error.get("message") or "")

    if success:
        outcome = CommandOutcome.EXECUTED
    elif code == "cancelled":
        outcome = CommandOutcome.CANCELLED
    else:
        outcome = CommandOutcome.FAILED
    reason = message or code

    pending = state.pending_commands.pop(command_id, None) if isinstance(command_id, int) else None
    command_name = pending.command if pending is not None else ""
    if pending is not None and not pending.future.done():
        pending.future.set_result((outcome, reason))

    async_fire_command_result(hass, entry.entry_id, command_name, outcome, reason)


def async_apply_event(hass: HomeAssistant, entry: ConfigEntry, payload: dict[str, Any]) -> None:
    """event-Nachricht verarbeiten: bekannte event_type-Werte als HA-Event feuern.

    Unbekannte Werte werden geloggt und ignoriert - Vorwärtskompatibilität, wie
    bei register/state schon üblich."""
    state = get_connection_state(hass, entry.entry_id)
    event_type = str(payload.get("event_type") or "")
    mapped = EVENT_TYPE_MAP.get(event_type)
    if mapped is None:
        _LOGGER.debug("event: unbekannter event_type %r - ignoriert", event_type)
        return
    data = payload.get("data")
    hass.bus.async_fire(
        mapped, {"device_id": state.device_id, **(data if isinstance(data, dict) else {})}
    )
