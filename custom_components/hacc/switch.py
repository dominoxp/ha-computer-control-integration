"""switch-Plattform: Entitäten entstehen dynamisch aus register, nicht statisch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .connection import async_seed_manifest_from_registry, get_connection_state, pending_entity_keys
from .const import SIGNAL_ENTITY_REGISTERED
from .entity import HaccEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    state = get_connection_state(hass, entry.entry_id)
    async_seed_manifest_from_registry(hass, entry, "switch")

    @callback
    def _discover(keys: list[str]) -> None:
        new_entities = [
            HaccSwitch(entry.entry_id, state.device_id, key, state.manifest[key])
            for key in keys
            if state.manifest[key].domain == "switch"
        ]
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ENTITY_REGISTERED.format(entry.entry_id), _discover)
    )
    # Nachholen, falls register schon vor diesem Plattform-Setup ankam.
    _discover(pending_entity_keys(state))


class HaccSwitch(HaccEntity, RestoreEntity, SwitchEntity):
    """Ein vom PC gemeldeter switch."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_send_command({self._descriptor.value_field: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_send_command({self._descriptor.value_field: False})

    def _apply_raw_state(self, value: str, attributes: dict[str, Any]) -> None:
        self._attr_is_on = value == STATE_ON
        self._attr_extra_state_attributes = {**self._descriptor.attributes, **attributes}

    async def _async_restore_last_state(self) -> None:
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON
