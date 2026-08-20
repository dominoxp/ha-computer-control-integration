"""select-Plattform: Entitäten entstehen dynamisch aus register, nicht statisch.

Die wählbaren Optionen ändern sich zur Laufzeit (angeschlossene Audiogeräte,
gespeicherte Monitor-Profile) und reisen deshalb nicht über `register`, sondern
als dynamisches `state`-Attribut `options` (siehe PROTOCOL.md).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .connection import (
    RegisteredEntity,
    async_seed_manifest_from_registry,
    get_connection_state,
    pending_entity_keys,
)
from .const import SIGNAL_ENTITY_REGISTERED
from .entity import HaccEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    state = get_connection_state(hass, entry.entry_id)
    async_seed_manifest_from_registry(hass, entry, "select")

    @callback
    def _discover(keys: list[str]) -> None:
        new_entities = [
            HaccSelect(entry.entry_id, state.device_id, key, state.manifest[key])
            for key in keys
            if state.manifest[key].domain == "select"
        ]
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ENTITY_REGISTERED.format(entry.entry_id), _discover)
    )
    # Nachholen, falls register schon vor diesem Plattform-Setup ankam.
    _discover(pending_entity_keys(state))


class HaccSelect(HaccEntity, RestoreEntity, SelectEntity):
    """Ein vom PC gemeldetes select - zeigt den Ist-Wert und schaltet ihn um."""

    def _apply_descriptor_fields(self, descriptor: RegisteredEntity) -> None:
        super()._apply_descriptor_fields(descriptor)
        self._attr_options = []

    async def async_select_option(self, option: str) -> None:
        await self.async_send_command({self._descriptor.value_field: option})

    def _apply_raw_state(self, value: str, attributes: dict[str, Any]) -> None:
        attrs = dict(attributes)
        options = attrs.pop("options", None)
        if isinstance(options, list):
            self._attr_options = [str(item) for item in options]
        # Ein Wert, der nicht (mehr) unter den Optionen ist - z.B. "unknown"
        # für "kein Gerät" - zeigt HA als "kein Zustand", nicht als Fehler.
        self._attr_current_option = value if value in self._attr_options else None
        self._attr_extra_state_attributes = {**self._descriptor.attributes, **attrs}

    async def _async_restore_last_state(self) -> None:
        last = await self.async_get_last_state()
        if last is not None and last.state in self._attr_options:
            self._attr_current_option = last.state
