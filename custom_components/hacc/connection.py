"""Runtime state that lives in hass.data: one ConnectionState per ConfigEntry.

Nothing here is persisted - it is rebuilt from scratch on every HA restart, and
that is fine because it only describes the *current* transport, not anything a
user configured. Entity availability (Step 5.3) will read ConnectionState.connected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aiohttp import WSCloseCode
from aiohttp.web import WebSocketResponse
from homeassistant.core import HomeAssistant

from .const import DOMAIN


@dataclass(slots=True)
class ConnectionState:
    """Live connection state of one paired PC."""

    device_id: str | None = None
    connected: bool = False
    last_hello_at: datetime | None = None
    ws: WebSocketResponse | None = None


@dataclass(slots=True)
class DomainData:
    """Everything the integration keeps in hass.data[DOMAIN]."""

    views_registered: bool = False
    connections: dict[str, ConnectionState] = field(default_factory=dict)  # entry_id -> state
    pair_failure_times: list[datetime] = field(default_factory=list)


def get_domain_data(hass: HomeAssistant) -> DomainData:
    """Return (creating if necessary) this integration's hass.data bucket."""
    return hass.data.setdefault(DOMAIN, DomainData())


def get_connection_state(hass: HomeAssistant, entry_id: str) -> ConnectionState:
    """Return (creating if necessary) the connection state for one ConfigEntry."""
    domain_data = get_domain_data(hass)
    return domain_data.connections.setdefault(entry_id, ConnectionState())


async def replace_connection(
    hass: HomeAssistant, entry_id: str, device_id: str, ws: WebSocketResponse
) -> None:
    """Register a freshly authenticated connection, closing any previous one.

    A second connection from the same device replaces the first instead of both
    lingering half-alive.
    """
    state = get_connection_state(hass, entry_id)
    old_ws = state.ws
    state.device_id = device_id
    state.ws = ws
    state.connected = True

    if old_ws is not None and not old_ws.closed:
        await old_ws.close(code=WSCloseCode.POLICY_VIOLATION, message=b"replaced by new connection")


def clear_connection(hass: HomeAssistant, entry_id: str) -> None:
    """Mark a device's connection as gone."""
    state = get_connection_state(hass, entry_id)
    state.connected = False
    state.ws = None
