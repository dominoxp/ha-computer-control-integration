"""Gemeinsame Basis für sensor.py/binary_sensor.py: eine Instanz pro registrierter
Entität, Zustand kommt per state-Nachricht (Push), nicht per Polling."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .connection import RegisteredEntity, get_connection_state
from .const import DOMAIN, SIGNAL_CONNECTION_STATE

_LOGGER = logging.getLogger(__name__)


def parse_enum[EnumT: Enum](enum_cls: type[EnumT], value: str | None) -> EnumT | None:
    """Wire-Wert tolerant in eine HA-Enum übersetzen - eine unbekannte device_class/
    state_class (z.B. eine neuere App-Version) wird geloggt statt die Entität crashen
    zu lassen."""
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        _LOGGER.warning("Unbekannter Wert %r für %s - ignoriert", value, enum_cls.__name__)
        return None


class HaccEntity(Entity):
    """Push-Entität: kein Polling, Zustand kommt über :meth:`async_apply_state`."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self, entry_id: str, device_id: str, key: str, descriptor: RegisteredEntity
    ) -> None:
        self._entry_id = entry_id
        self._key = key
        self._descriptor = descriptor
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
        self._apply_descriptor_fields(descriptor)

    @property
    def suggested_object_id(self) -> str | None:
        # Ohne diesen Override würde HA die object_id aus dem Namen ableiten
        # ("cpu_auslastung" statt "cpu") - der Key ist die bewusst gewählte,
        # in der README dokumentierte Konvention (sensor.<geraet>_cpu).
        return self._key

    @property
    def available(self) -> bool:
        return get_connection_state(self.hass, self._entry_id).connected

    def _apply_descriptor_fields(self, descriptor: RegisteredEntity) -> None:
        self._descriptor = descriptor
        self._attr_name = descriptor.name
        self._attr_icon = descriptor.icon

    @callback
    def async_apply_descriptor(self, descriptor: RegisteredEntity) -> None:
        """Neuer register mit unverändertem Key - statische Felder auffrischen."""
        self._apply_descriptor_fields(descriptor)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = get_connection_state(self.hass, self._entry_id)
        state.live_entities[self._key] = self

        def _unregister() -> None:
            if state.live_entities.get(self._key) is self:
                del state.live_entities[self._key]

        self.async_on_remove(_unregister)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONNECTION_STATE.format(self._entry_id),
                self._async_on_connection_signal,
            )
        )

        raw = state.raw_states.get(self._key)
        if raw is not None:
            # Frischer als jeder Restore - die Verbindung stand schon, bevor
            # diese Entität geladen wurde.
            self._apply_raw_state(raw.state, raw.attributes)
        else:
            await self._async_restore_last_state()

    @callback
    def _async_on_connection_signal(self, *_args: Any) -> None:
        # Ohne @callback hält HA den dispatcher-Handler für nicht threadsicher und
        # führt ihn in einem Executor-Thread aus - async_write_ha_state() darf aber
        # nur auf dem Event-Loop laufen.
        self.async_write_ha_state()

    @callback
    def async_apply_state(self, value: str, attributes: dict[str, Any]) -> None:
        self._apply_raw_state(value, attributes)
        self.async_write_ha_state()

    def _apply_raw_state(self, value: str, attributes: dict[str, Any]) -> None:
        raise NotImplementedError

    async def _async_restore_last_state(self) -> None:
        raise NotImplementedError
