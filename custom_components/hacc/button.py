"""button-Plattform: Entitäten entstehen dynamisch aus register, nicht statisch.

Ein button hat in HA keinen Zustand - der Ausgang eines Befehls gehört ins
Event `hacc_command_result`, nicht in die Entität (siehe PROTOCOL.md /
step_5.4). `_apply_raw_state`/`_async_restore_last_state` sind deshalb No-ops.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .connection import async_seed_manifest_from_registry, get_connection_state, pending_entity_keys
from .const import SIGNAL_ENTITY_REGISTERED
from .entity import HaccEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    state = get_connection_state(hass, entry.entry_id)
    async_seed_manifest_from_registry(hass, entry, "button")

    @callback
    def _discover(keys: list[str]) -> None:
        new_entities = [
            HaccButton(entry.entry_id, state.device_id, key, state.manifest[key])
            for key in keys
            if state.manifest[key].domain == "button"
        ]
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ENTITY_REGISTERED.format(entry.entry_id), _discover)
    )
    # Nachholen, falls register schon vor diesem Plattform-Setup ankam.
    _discover(pending_entity_keys(state))


class HaccButton(HaccEntity, ButtonEntity):
    """Ein vom PC gemeldeter Knopf - Druck löst dessen Befehl aus."""

    async def async_press(self) -> None:
        await self.async_send_command({})

    def _apply_raw_state(self, value: str, attributes: dict[str, Any]) -> None:
        pass

    async def _async_restore_last_state(self) -> None:
        pass
