"""The HA Computer Control integration.

Builds the transport (pairing, WebSocket channel, per-device connection state in
hass.data) and, since Step 5.3, forwards to the sensor/binary_sensor platforms
that turn a connected PC's manifest into real entities. Commands and access
grants follow in later steps of Phase 5 - see PROTOCOL.md.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .connection import get_domain_data
from .const import PLATFORMS
from .http import PairView, WebSocketView
from .services import async_register_services


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the pairing/websocket HTTP views and the services exactly once."""
    domain_data = get_domain_data(hass)
    if not domain_data.views_registered:
        hass.http.register_view(PairView())
        hass.http.register_view(WebSocketView())
        async_register_services(hass)
        domain_data.views_registered = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one PC's ConfigEntry: runtime state plus its entity platforms."""
    get_domain_data(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down one PC's ConfigEntry: platforms first, then the open connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain_data = get_domain_data(hass)
    state = domain_data.connections.pop(entry.entry_id, None)
    if state is not None:
        for unsub in state.entity_unsubs.values():
            unsub()
        if state.ws is not None and not state.ws.closed:
            await state.ws.close()
    return unloaded
