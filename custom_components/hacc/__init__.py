"""The HA Computer Control integration.

This step only builds the transport: pairing, the WebSocket channel and a
per-device connection state in hass.data. Entities, commands and access grants
follow in later steps of Phase 5 - see PROTOCOL.md (Step 5.2) once it exists.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .connection import get_domain_data
from .http import PairView, WebSocketView

PLATFORMS: list[str] = []


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the pairing/websocket HTTP views exactly once for the whole integration."""
    domain_data = get_domain_data(hass)
    if not domain_data.views_registered:
        hass.http.register_view(PairView())
        hass.http.register_view(WebSocketView())
        domain_data.views_registered = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one PC's ConfigEntry - just ensures its runtime state exists."""
    get_domain_data(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down one PC's ConfigEntry, closing any open connection."""
    domain_data = get_domain_data(hass)
    state = domain_data.connections.pop(entry.entry_id, None)
    if state is not None and state.ws is not None and not state.ws.closed:
        await state.ws.close()
    return True
