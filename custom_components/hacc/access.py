"""Freigabe-Datenmodell: welche fremden HA-Entitäten/Services ein PC lesen bzw.
schalten darf. Lebt in ``ConfigEntry.options["access"]`` - nicht in einer
eigenen Datei, damit die Freigaben mit dem Gerät verschwinden und im
HA-Backup auftauchen (siehe PROTOCOL.md, Abschnitt "access", und
steps/phase_5/step_5.5_zugriffs_freigaben.md).

Drei Zustände (:class:`AccessStatus`) plus ein impliziter vierter: kein
Eintrag heißt "nie gefragt". Ein Eintrag entsteht automatisch nur als
``REQUESTED`` (über :func:`request_read`/:func:`request_call`, aufgerufen von
``subscribe``/``call_service``) oder von Hand über den Options-Flow.

``GRANTED``/``DENIED`` sind bewusste Nutzer-Entscheidungen und werden von
automatischen Anfragen nie überschrieben - sonst tauchte ein abgelehnter
Eintrag bei jedem Reconnect wieder auf. Widerruf (:func:`revoke_read`/
:func:`revoke_call`) ist etwas anderes als Ablehnung: Er **löscht** den
Eintrag komplett, damit eine künftige Anfrage wieder ganz normal ``REQUESTED``
werden kann - Ablehnung soll genau das verhindern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import MAX_PENDING_ACCESS_REQUESTS

OPTIONS_ACCESS = "access"
OPTIONS_READ = "read"
OPTIONS_CALL = "call"
OPTIONS_DISCOVERY = "discovery_enabled"


class AccessStatus(StrEnum):
    GRANTED = "granted"
    REQUESTED = "requested"
    DENIED = "denied"


@dataclass(slots=True, frozen=True)
class ReadGrant:
    entity_id: str
    status: AccessStatus
    requested_at: str = ""


@dataclass(slots=True, frozen=True)
class CallGrant:
    key: str
    """``<domain>.<service>`` - siehe :func:`call_key`."""
    status: AccessStatus
    requested_at: str = ""
    targets: tuple[str, ...] = ()
    """Leer = unbeschränkt (jedes Ziel); sonst nur diese Zielentitäten."""


def call_key(domain: str, service: str) -> str:
    return f"{domain}.{service}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _access_options(entry: ConfigEntry) -> dict[str, Any]:
    return dict(entry.options.get(OPTIONS_ACCESS, {}))


def _read_raw(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    return dict(_access_options(entry).get(OPTIONS_READ, {}))


def _call_raw(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    return dict(_access_options(entry).get(OPTIONS_CALL, {}))


def read_grants(entry: ConfigEntry) -> dict[str, ReadGrant]:
    return {
        entity_id: ReadGrant(
            entity_id,
            AccessStatus(data.get("status", AccessStatus.REQUESTED.value)),
            str(data.get("requested_at", "")),
        )
        for entity_id, data in _read_raw(entry).items()
    }


def call_grants(entry: ConfigEntry) -> dict[str, CallGrant]:
    return {
        key: CallGrant(
            key,
            AccessStatus(data.get("status", AccessStatus.REQUESTED.value)),
            str(data.get("requested_at", "")),
            tuple(data.get("targets") or ()),
        )
        for key, data in _call_raw(entry).items()
    }


def discovery_enabled(entry: ConfigEntry) -> bool:
    return bool(_access_options(entry).get(OPTIONS_DISCOVERY, False))


def is_read_granted(entry: ConfigEntry, entity_id: str) -> bool:
    grant = read_grants(entry).get(entity_id)
    return grant is not None and grant.status is AccessStatus.GRANTED


def is_call_granted(entry: ConfigEntry, domain: str, service: str, target: str | None) -> bool:
    grant = call_grants(entry).get(call_key(domain, service))
    if grant is None or grant.status is not AccessStatus.GRANTED:
        return False
    return not grant.targets or target in grant.targets


_BROAD_TARGET_SELECTORS = ("area_id", "device_id", "floor_id", "label_id")


def is_call_target_allowed(entry: ConfigEntry, domain: str, service: str, target: Any) -> bool:
    """Ob der komplette ``target``-Payload einer call_service-Nachricht durch die
    Freigabe gedeckt ist - nicht nur eine einzelne repräsentative Entity-Id.

    Anders als :func:`is_call_granted` (die nur auf eine einzelne Ziel-Entität
    für die Anfrage-/UI-Logik schaut) prüft das hier *jede* in ``target``
    genannte Entity-Id gegen ``grant.targets`` und lehnt ``area_id``/
    ``device_id``/``floor_id``/``label_id``-Selektoren ab, sobald die Freigabe
    auf bestimmte Ziele beschränkt ist. Ohne das könnte ein Gerät eine Freigabe
    für eine einzelne Entität durch eine Entity-Id-Liste oder einen zusätzlichen
    Area-Selektor auf beliebige weitere Ziele ausweiten.
    """
    grant = call_grants(entry).get(call_key(domain, service))
    if grant is None or grant.status is not AccessStatus.GRANTED:
        return False
    if not grant.targets:
        return True
    if not isinstance(target, dict) or any(key in target for key in _BROAD_TARGET_SELECTORS):
        return False
    entity_ids = target.get("entity_id")
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    if not isinstance(entity_ids, list) or not entity_ids:
        return False
    return all(entity_id in grant.targets for entity_id in entity_ids)


def granted_read_entities(entry: ConfigEntry) -> list[str]:
    return [g.entity_id for g in read_grants(entry).values() if g.status is AccessStatus.GRANTED]


def granted_call_keys(entry: ConfigEntry) -> list[str]:
    return [g.key for g in call_grants(entry).values() if g.status is AccessStatus.GRANTED]


def _pending_count(entry: ConfigEntry) -> int:
    reads = sum(1 for g in read_grants(entry).values() if g.status is AccessStatus.REQUESTED)
    calls = sum(1 for g in call_grants(entry).values() if g.status is AccessStatus.REQUESTED)
    return reads + calls


def _write(
    hass: HomeAssistant,
    entry: ConfigEntry,
    read: dict[str, Any],
    call: dict[str, Any],
    discovery: bool,
) -> None:
    new_options = dict(entry.options)
    new_options[OPTIONS_ACCESS] = {
        OPTIONS_READ: read,
        OPTIONS_CALL: call,
        OPTIONS_DISCOVERY: discovery,
    }
    hass.config_entries.async_update_entry(entry, options=new_options)


def request_read(hass: HomeAssistant, entry: ConfigEntry, entity_id: str) -> None:
    """Automatische Anfrage aus ``subscribe``: legt ``requested`` an, dedupliziert.

    Ein bestehender ``granted``/``denied``-Eintrag bleibt unangetastet; ein
    bestehender ``requested``-Eintrag bekommt nur einen neuen Zeitstempel
    ("wann zuletzt angefragt"). Neue Einträge über dem Deckel
    (:data:`MAX_PENDING_ACCESS_REQUESTS`) werden verworfen.
    """
    read = _read_raw(entry)
    existing = read.get(entity_id)
    if existing is not None and existing.get("status") != AccessStatus.REQUESTED.value:
        return
    if existing is None and _pending_count(entry) >= MAX_PENDING_ACCESS_REQUESTS:
        return
    read[entity_id] = {"status": AccessStatus.REQUESTED.value, "requested_at": _now_iso()}
    _write(hass, entry, read, _call_raw(entry), discovery_enabled(entry))


def request_call(
    hass: HomeAssistant, entry: ConfigEntry, domain: str, service: str, target: str | None
) -> None:
    """Automatische Anfrage aus ``call_service`` - siehe :func:`request_read`."""
    call = _call_raw(entry)
    key = call_key(domain, service)
    existing = call.get(key)
    if existing is not None and existing.get("status") != AccessStatus.REQUESTED.value:
        return
    if existing is None and _pending_count(entry) >= MAX_PENDING_ACCESS_REQUESTS:
        return
    targets = list(existing.get("targets") or []) if existing else []
    if target and target not in targets:
        targets.append(target)
    call[key] = {
        "status": AccessStatus.REQUESTED.value,
        "requested_at": _now_iso(),
        "targets": targets,
    }
    _write(hass, entry, _read_raw(entry), call, discovery_enabled(entry))


def set_read_status(
    hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str], status: AccessStatus
) -> None:
    """Bulk-Aktion aus dem Options-Flow (bestätigen/ablehnen/wieder öffnen)."""
    read = _read_raw(entry)
    for entity_id in entity_ids:
        read[entity_id] = {**read.get(entity_id, {}), "status": status.value}
    _write(hass, entry, read, _call_raw(entry), discovery_enabled(entry))


def set_call_status(
    hass: HomeAssistant, entry: ConfigEntry, keys: list[str], status: AccessStatus
) -> None:
    call = _call_raw(entry)
    for key in keys:
        call[key] = {**call.get(key, {}), "status": status.value}
    _write(hass, entry, _read_raw(entry), call, discovery_enabled(entry))


def revoke_read(hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str]) -> None:
    """Freigabe zurücknehmen - löscht den Eintrag (anders als Ablehnung, die
    stehen bleibt, damit nicht sofort wieder automatisch angefragt wird)."""
    read = _read_raw(entry)
    for entity_id in entity_ids:
        read.pop(entity_id, None)
    _write(hass, entry, read, _call_raw(entry), discovery_enabled(entry))


def revoke_call(hass: HomeAssistant, entry: ConfigEntry, keys: list[str]) -> None:
    call = _call_raw(entry)
    for key in keys:
        call.pop(key, None)
    _write(hass, entry, _read_raw(entry), call, discovery_enabled(entry))


def grant_read(hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str]) -> None:
    """Proaktive Freigabe aus dem Options-Flow, bevor der PC überhaupt gefragt hat."""
    set_read_status(hass, entry, entity_ids, AccessStatus.GRANTED)


def grant_call(
    hass: HomeAssistant, entry: ConfigEntry, domain: str, service: str, targets: list[str]
) -> None:
    """Proaktive Service-Freigabe; ``targets`` leer = unbeschränkt."""
    call = _call_raw(entry)
    key = call_key(domain, service)
    call[key] = {
        "status": AccessStatus.GRANTED.value,
        "requested_at": call.get(key, {}).get("requested_at") or _now_iso(),
        "targets": targets,
    }
    _write(hass, entry, _read_raw(entry), call, discovery_enabled(entry))


def set_discovery_enabled(hass: HomeAssistant, entry: ConfigEntry, enabled: bool) -> None:
    _write(hass, entry, _read_raw(entry), _call_raw(entry), enabled)


def access_message(entry: ConfigEntry) -> dict[str, Any]:
    """Payload der ``access``-Nachricht - siehe PROTOCOL.md."""
    return {
        "type": "access",
        "read": {entity_id: g.status.value for entity_id, g in read_grants(entry).items()},
        "call": {key: g.status.value for key, g in call_grants(entry).items()},
        "discovery": discovery_enabled(entry),
    }
