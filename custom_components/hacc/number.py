"""number-Plattform: Entitäten entstehen dynamisch aus register, nicht statisch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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
    async_seed_manifest_from_registry(hass, entry, "number")

    @callback
    def _discover(keys: list[str]) -> None:
        new_entities = [
            HaccNumber(entry.entry_id, state.device_id, key, state.manifest[key])
            for key in keys
            if state.manifest[key].domain == "number"
        ]
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ENTITY_REGISTERED.format(entry.entry_id), _discover)
    )
    # Nachholen, falls register schon vor diesem Plattform-Setup ankam.
    _discover(pending_entity_keys(state))


class HaccNumber(HaccEntity, RestoreNumber):
    """Ein vom PC gemeldeter Regler."""

    def _apply_descriptor_fields(self, descriptor: RegisteredEntity) -> None:
        super()._apply_descriptor_fields(descriptor)
        self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_native_min_value = (
            descriptor.min_value if descriptor.min_value is not None else 0.0
        )
        self._attr_native_max_value = (
            descriptor.max_value if descriptor.max_value is not None else 100.0
        )
        self._attr_native_step = descriptor.step if descriptor.step is not None else 1.0

    async def async_set_native_value(self, value: float) -> None:
        await self.async_send_command({self._descriptor.value_field: value})

    def _apply_raw_state(self, value: str, attributes: dict[str, Any]) -> None:
        try:
            self._attr_native_value = float(value)
        except ValueError:
            self._attr_native_value = None
        self._attr_extra_state_attributes = {**self._descriptor.attributes, **attributes}

    async def _async_restore_last_state(self) -> None:
        last = await self.async_get_last_number_data()
        if last is not None:
            self._attr_native_value = last.native_value
