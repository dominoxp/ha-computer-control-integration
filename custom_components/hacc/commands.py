"""Befehle HA -> PC senden und auf ihr Ergebnis warten (Step 5.4).

Das Gegenstück zu `HAClient.call_service` auf der Windows-Seite, nur in der
anderen Richtung: Hier ist HA der Initiator (`command`), der PC antwortet
(`result`). Jede Entität-Interaktion und jeder der zweckgebundenen Services
(`hacc.notify`, `hacc.launch`, `hacc.set_displays`) läuft über
:func:`async_send_command`.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant

from .connection import (
    CommandOutcome,
    PendingCommand,
    async_fire_command_result,
    get_connection_state,
)
from .const import DEFAULT_COMMAND_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)


async def async_send_command(
    hass: HomeAssistant,
    entry_id: str,
    command: str,
    data: dict[str, object],
    *,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> tuple[CommandOutcome, str]:
    """Einen command an den verbundenen PC schicken und auf `result` warten.

    Läuft der PC in einen Timeout oder ist er gar nicht verbunden, kommt
    ``(FAILED, <grund>)`` zurück statt einer Exception - Aufrufer (Entities,
    Services) entscheiden selbst, wie sie das melden.
    """
    state = get_connection_state(hass, entry_id)
    if not state.connected or state.ws is None:
        return CommandOutcome.FAILED, "keine Verbindung zum PC"

    state.next_command_id += 1
    command_id = state.next_command_id
    future: asyncio.Future[tuple[CommandOutcome, str]] = hass.loop.create_future()
    state.pending_commands[command_id] = PendingCommand(future=future, command=command)

    try:
        await state.ws.send_json(
            {"type": "command", "id": command_id, "command": command, "data": data}
        )
    except (ConnectionError, RuntimeError) as exc:
        state.pending_commands.pop(command_id, None)
        return CommandOutcome.FAILED, str(exc)

    try:
        async with asyncio.timeout(timeout_seconds):
            return await future
    except TimeoutError:
        _LOGGER.warning(
            "Befehl %r (id=%s) hat nicht innerhalb von %.0f s geantwortet",
            command,
            command_id,
            timeout_seconds,
        )
        async_fire_command_result(hass, entry_id, command, CommandOutcome.FAILED, "timeout")
        return CommandOutcome.FAILED, "timeout"
    finally:
        state.pending_commands.pop(command_id, None)
