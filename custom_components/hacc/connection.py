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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from aiohttp import WSCloseCode
from aiohttp.web import WebSocketResponse
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from . import access
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
    subscribed: frozenset[str] = field(default_factory=frozenset)
    """Letzter `subscribe`-Wunsch des PCs (Step 5.5) - nicht persistiert, wird
    bei jedem (Re-)Connect frisch vom Client geschickt."""
    entity_unsubs: dict[str, Callable[[], None]] = field(default_factory=dict)
    """Laufende Zustands-Abos auf fremde HA-Entitäten, je Entity-Id (Step 5.5)."""


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
    for unsub in state.entity_unsubs.values():
        unsub()
    state.entity_unsubs.clear()
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
    event_data = dict(data) if isinstance(data, dict) else {}
    # device_id kommt zuletzt und gewinnt bewusst - sonst könnte ein Gerät sich
    # über ein gleichnamiges Feld in `data` als ein anderes gepairtes Gerät
    # ausgeben (siehe PROTOCOL.md: "mit device_id ergänzt").
    event_data["device_id"] = state.device_id
    hass.bus.async_fire(mapped, event_data)


# -- Fremde HA-Entitäten lesen (Step 5.5) ------------------------------------


async def async_send_access(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Aktuelle Freigabe-Tabelle an ein verbundenes Gerät schicken (siehe PROTOCOL.md)."""
    state = get_connection_state(hass, entry.entry_id)
    if state.ws is None or state.ws.closed:
        return
    await state.ws.send_json(access.access_message(entry))


async def _push_entity_state(hass: HomeAssistant, entry: ConfigEntry, ha_state: State) -> None:
    state = get_connection_state(hass, entry.entry_id)
    if state.ws is None or state.ws.closed:
        return
    await state.ws.send_json(
        {
            "type": "entity",
            "entity_id": ha_state.entity_id,
            "state": ha_state.state,
            "attributes": dict(ha_state.attributes),
            "last_updated": ha_state.last_updated.timestamp(),
        }
    )


async def _push_current_states(
    hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str]
) -> None:
    for entity_id in entity_ids:
        ha_state = hass.states.get(entity_id)
        if ha_state is not None:
            await _push_entity_state(hass, entry, ha_state)


def _make_state_listener(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[Event], None]:
    @callback
    def _listener(event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        if new_state is not None:
            hass.async_create_task(_push_entity_state(hass, entry, new_state))

    return _listener


@callback
def _sync_entity_listeners(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """`entity_unsubs` gegen `subscribed ∩ granted` abgleichen.

    Legt Abos für neu freigegebene/gewünschte Entitäten an und beendet
    verwaiste (nicht mehr gewünscht oder Freigabe entzogen) - Letzteres ist
    der Grund, warum ein Widerruf sofort wirkt, ohne dass jemand etwas neu
    startet. Gibt die frisch abonnierten Entity-Ids zurück, für die noch ein
    Ist-Zustand nachgeliefert werden muss.
    """
    state = get_connection_state(hass, entry.entry_id)
    granted = set(access.granted_read_entities(entry))
    wanted = state.subscribed & granted

    for entity_id in [key for key in state.entity_unsubs if key not in wanted]:
        state.entity_unsubs.pop(entity_id)()

    newly = [entity_id for entity_id in wanted if entity_id not in state.entity_unsubs]
    for entity_id in newly:
        state.entity_unsubs[entity_id] = async_track_state_change_event(
            hass, entity_id, _make_state_listener(hass, entry)
        )
    return newly


async def async_apply_subscribe(
    hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[Any]
) -> None:
    """`subscribe`-Nachricht verarbeiten (siehe PROTOCOL.md).

    Ersetzt den gesamten Wunsch (kein Diff, wie `register`). Nicht
    freigegebene Entitäten lösen automatisch eine Anfrage aus
    (:func:`hacc.access.request_read`); ein bereits abgelehnter Eintrag wird
    dabei nicht angerührt."""
    state = get_connection_state(hass, entry.entry_id)
    state.subscribed = frozenset(
        str(entity_id)
        for entity_id in entity_ids
        if isinstance(entity_id, str) and entity_id.strip()
    )

    for entity_id in state.subscribed:
        if not access.is_read_granted(entry, entity_id):
            access.request_read(hass, entry, entity_id)

    newly = _sync_entity_listeners(hass, entry)
    await _push_current_states(hass, entry, newly)
    await async_send_access(hass, entry)


async def async_apply_access_change(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Nach einer Options-Flow-Änderung (Freigabe oder Such-Modus) ein gerade
    verbundenes Gerät sofort nachziehen - Bestätigen/Widerrufen wirkt ohne
    Neustart."""
    newly = _sync_entity_listeners(hass, entry)
    await _push_current_states(hass, entry, newly)
    await async_send_access(hass, entry)
