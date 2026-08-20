"""Zweckgebundene Services: `hacc.notify`, `hacc.launch`, `hacc.set_displays`.

Kein generischer Notausgang (`hacc.command`) - jeder Befehl, der keine eigene
Entität ist (Toast-Text ist pro Aufruf frei, ein Launcher-Eintrag kann eine
lange Liste sein, ein Ad-hoc-Monitor-Layout braucht Metadaten), bekommt einen
eigenen, typisierten Service. Das ergibt echte Felder im Web-Editor statt
eines freien `data`-Objekts.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from . import commands
from .connection import CommandOutcome, get_domain_data
from .const import DOMAIN, SERVICE_LAUNCH, SERVICE_NOTIFY, SERVICE_SET_DISPLAYS

_NOTIFY_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required("title"): cv.string,
        vol.Optional("message"): cv.string,
        vol.Optional("icon"): cv.string,
        vol.Optional("duration"): cv.string,
        vol.Optional("actions"): object,
    }
)
_LAUNCH_SCHEMA = cv.make_entity_service_schema({vol.Required("entry"): cv.string})
_SET_DISPLAYS_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("profile"): cv.string,
        vol.Optional("monitors"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("primary"): cv.string,
    }
)


def _entry_ids_for(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Ausgewählte HA-Geräte-Ids auf hacc-ConfigEntry-Ids abbilden."""
    raw = call.data.get(ATTR_DEVICE_ID) or []
    device_ids = [raw] if isinstance(raw, str) else list(raw)
    if not device_ids:
        raise HomeAssistantError("Kein Gerät ausgewählt")

    device_registry = dr.async_get(hass)
    known_entries = get_domain_data(hass).connections
    entry_ids: list[str] = []
    for device_id in device_ids:
        device = device_registry.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Unbekanntes Gerät: {device_id}")
        matching = [entry_id for entry_id in device.config_entries if entry_id in known_entries]
        if not matching:
            raise HomeAssistantError(f"Gerät {device_id} gehört zu keiner hacc-Kopplung")
        entry_ids.extend(matching)
    return entry_ids


async def _async_call(hass: HomeAssistant, call: ServiceCall, command: str, data: dict) -> None:
    for entry_id in _entry_ids_for(hass, call):
        outcome, reason = await commands.async_send_command(hass, entry_id, command, data)
        if outcome is not CommandOutcome.EXECUTED:
            raise HomeAssistantError(reason or outcome.value)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Die drei Services einmalig registrieren (parallel zu den HTTP-Views)."""

    async def handle_notify(call: ServiceCall) -> None:
        data = {
            k: v
            for k, v in call.data.items()
            if k in ("title", "message", "icon", "duration", "actions")
        }
        await _async_call(hass, call, "notify", data)

    async def handle_launch(call: ServiceCall) -> None:
        await _async_call(hass, call, "launch", {"entry": call.data["entry"]})

    async def handle_set_displays(call: ServiceCall) -> None:
        data = {k: v for k, v in call.data.items() if k in ("profile", "monitors", "primary")}
        await _async_call(hass, call, "set_displays", data)

    hass.services.async_register(DOMAIN, SERVICE_NOTIFY, handle_notify, schema=_NOTIFY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_LAUNCH, handle_launch, schema=_LAUNCH_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_DISPLAYS, handle_set_displays, schema=_SET_DISPLAYS_SCHEMA
    )
