"""sensor-Plattform: Entitäten entstehen dynamisch aus register, nicht statisch."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .connection import (
    RegisteredEntity,
    async_seed_manifest_from_registry,
    get_connection_state,
    pending_entity_keys,
)
from .const import SIGNAL_ENTITY_REGISTERED
from .entity import HaccEntity, parse_enum

_LOGGER = logging.getLogger(__name__)


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
        self._attr_native_value = self._convert_native_value(value)
        self._attr_extra_state_attributes = {**self._descriptor.attributes, **attributes}

    def _convert_native_value(self, value: str) -> str | date | datetime | None:
        """Wire-Strings für timestamp/date-Sensoren in echte Objekte wandeln - HA
        verlangt dafür datetime/date als native_value und wirft bei einem str
        einen ValueError beim Schreiben des Zustands."""
        device_class = self._attr_device_class
        if device_class not in (SensorDeviceClass.TIMESTAMP, SensorDeviceClass.DATE):
            return value
        if device_class is SensorDeviceClass.TIMESTAMP:
            parsed: date | datetime | None = dt_util.parse_datetime(value)
            if parsed is not None and parsed.tzinfo is None:
                parsed = dt_util.as_utc(parsed)
        else:
            parsed = dt_util.parse_date(value)
        if parsed is None:
            _LOGGER.debug(
                "Wert %r von %s ist kein gültiger %s - Zustand unbekannt",
                value,
                self._key,
                device_class.value,
            )
        return parsed

    async def _async_restore_last_state(self) -> None:
        last = await self.async_get_last_sensor_data()
        if last is not None:
            restored = last.native_value
            if isinstance(restored, str):
                # Vor dem Fix gespeichert: timestamp/date-Werte lagen noch als str vor.
                restored = self._convert_native_value(restored)
            self._attr_native_value = restored
