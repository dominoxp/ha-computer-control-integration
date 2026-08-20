"""sensor-Plattform: Entitäten entstehen dynamisch aus register, nicht statisch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
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
from .entity import HaccEntity, parse_enum


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    state = get_connection_state(hass, entry.entry_id)
    async_seed_manifest_from_registry(hass, entry, "sensor")

    @callback
    def _discover(keys: list[str]) -> None:
        new_entities = [
            HaccSensor(entry.entry_id, state.device_id, key, state.manifest[key])
            for key in keys
            if state.manifest[key].domain == "sensor"
        ]
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ENTITY_REGISTERED.format(entry.entry_id), _discover)
    )
    # Nachholen, falls register schon vor diesem Plattform-Setup ankam.
    _discover(pending_entity_keys(state))


class HaccSensor(HaccEntity, RestoreSensor):
    """Ein vom PC gemeldeter sensor."""

    def _apply_descriptor_fields(self, descriptor: RegisteredEntity) -> None:
        super()._apply_descriptor_fields(descriptor)
        self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_device_class = parse_enum(SensorDeviceClass, descriptor.device_class)
        self._attr_state_class = parse_enum(SensorStateClass, descriptor.state_class)

    def _apply_raw_state(self, value: str, attributes: dict[str, Any]) -> None:
        self._attr_native_value = value
        self._attr_extra_state_attributes = {**self._descriptor.attributes, **attributes}

    async def _async_restore_last_state(self) -> None:
        last = await self.async_get_last_sensor_data()
        if last is not None:
            self._attr_native_value = last.native_value
