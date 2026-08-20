"""Tests von `commands.async_send_command` und `connection.async_apply_command_result`
auf Ebene der reinen Python-Objekte - ohne echten WebSocket, dafür mit einem
Fake dafür (schneller, und die Korrelation über mehrere gleichzeitige Befehle
lässt sich so exakt steuern)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.hacc import commands
from custom_components.hacc.connection import (
    CommandOutcome,
    async_apply_command_result,
    get_connection_state,
)


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def _entry(entry_id: str) -> Any:
    return SimpleNamespace(entry_id=entry_id)


async def test_send_command_without_connection_fails_fast(hass: HomeAssistant) -> None:
    outcome, reason = await commands.async_send_command(hass, "entry-1", "shutdown", {})

    assert outcome is CommandOutcome.FAILED
    assert reason


async def test_send_command_resolves_on_a_matching_result(hass: HomeAssistant) -> None:
    entry_id = "entry-2"
    state = get_connection_state(hass, entry_id)
    state.connected = True
    state.ws = _FakeWs()

    task = asyncio.create_task(
        commands.async_send_command(hass, entry_id, "shutdown", {"force": True})
    )
    await asyncio.sleep(0)
    assert state.ws.sent[0]["command"] == "shutdown"
    assert state.ws.sent[0]["data"] == {"force": True}
    command_id = state.ws.sent[0]["id"]

    async_apply_command_result(
        hass, _entry(entry_id), {"id": command_id, "success": True, "error": None}
    )

    outcome, reason = await task
    assert outcome is CommandOutcome.EXECUTED
    assert reason == ""
    assert command_id not in state.pending_commands


async def test_a_cancelled_result_is_distinguished_from_a_failure(hass: HomeAssistant) -> None:
    entry_id = "entry-3"
    state = get_connection_state(hass, entry_id)
    state.connected = True
    state.ws = _FakeWs()

    task = asyncio.create_task(commands.async_send_command(hass, entry_id, "shutdown", {}))
    await asyncio.sleep(0)
    command_id = state.ws.sent[0]["id"]

    async_apply_command_result(
        hass,
        _entry(entry_id),
        {"id": command_id, "success": False, "error": {"code": "cancelled", "message": "user"}},
    )

    outcome, reason = await task
    assert outcome is CommandOutcome.CANCELLED
    assert reason == "user"


async def test_concurrent_commands_are_not_mixed_up(hass: HomeAssistant) -> None:
    entry_id = "entry-4"
    state = get_connection_state(hass, entry_id)
    state.connected = True
    state.ws = _FakeWs()

    first = asyncio.create_task(
        commands.async_send_command(hass, entry_id, "set_volume", {"level": 10})
    )
    await asyncio.sleep(0)
    second = asyncio.create_task(
        commands.async_send_command(hass, entry_id, "set_volume", {"level": 90})
    )
    await asyncio.sleep(0)

    first_id = state.ws.sent[0]["id"]
    second_id = state.ws.sent[1]["id"]
    assert first_id != second_id

    # Antworten bewusst in umgekehrter Reihenfolge - die Zuordnung läuft über
    # die Id, nicht über die Reihenfolge des Eintreffens.
    async_apply_command_result(
        hass, _entry(entry_id), {"id": second_id, "success": True, "error": None}
    )
    async_apply_command_result(
        hass,
        _entry(entry_id),
        {"id": first_id, "success": False, "error": {"code": "failed", "message": "nope"}},
    )

    first_outcome, first_reason = await first
    second_outcome, second_reason = await second
    assert (first_outcome, first_reason) == (CommandOutcome.FAILED, "nope")
    assert (second_outcome, second_reason) == (CommandOutcome.EXECUTED, "")


async def test_a_timeout_resolves_as_failed_and_fires_the_event(hass: HomeAssistant) -> None:
    entry_id = "entry-5"
    state = get_connection_state(hass, entry_id)
    state.connected = True
    state.ws = _FakeWs()
    events: list[dict[str, Any]] = []
    hass.bus.async_listen("hacc_command_result", lambda event: events.append(event.data))

    outcome, reason = await commands.async_send_command(
        hass, entry_id, "shutdown", {}, timeout_seconds=0.05
    )

    assert outcome is CommandOutcome.FAILED
    assert reason == "timeout"
    assert state.pending_commands == {}
    await hass.async_block_till_done()
    assert events[-1] == {
        "device_id": None,
        "command": "shutdown",
        "result": "failed",
        "reason": "timeout",
    }


async def test_result_without_a_waiting_caller_still_fires_the_event(hass: HomeAssistant) -> None:
    """Ein Timeout darf das spaeter eintreffende result nicht verschlucken -
    Automationen sollen die Wahrheit trotzdem sehen."""
    entry_id = "entry-6"
    events: list[dict[str, Any]] = []
    hass.bus.async_listen("hacc_command_result", lambda event: events.append(event.data))

    async_apply_command_result(hass, _entry(entry_id), {"id": 999, "success": True, "error": None})
    await hass.async_block_till_done()

    assert events[-1]["result"] == "executed"
