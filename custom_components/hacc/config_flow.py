"""Config flow: create a ConfigEntry and show its pairing code.

No text input is needed from the HA user - the device name comes from the app
during /pair (Step 5.1 scope), not from a form field here.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DATA_DEVICE_ID, DATA_DEVICE_KEY_HASH, DATA_DEVICE_NAME, DOMAIN
from .pairing import new_pairing_entry_data

DEFAULT_TITLE = "HA Computer Control (wird gekoppelt)"
_STALE_DEVICE_KEYS = (DATA_DEVICE_ID, DATA_DEVICE_KEY_HASH, DATA_DEVICE_NAME)


class HaccOptionsFlow(config_entries.OptionsFlow):
    """Let the user generate a fresh pairing code at any time."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self._new_code: str | None = None
        self._new_entry_data: dict[str, str] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self._new_code is None:
            self._new_code, self._new_entry_data = new_pairing_entry_data()

        if user_input is not None:
            new_data = {
                k: v for k, v in self.config_entry.data.items() if k not in _STALE_DEVICE_KEYS
            }
            new_data.update(self._new_entry_data)
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data, title=DEFAULT_TITLE
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
            description_placeholders={"code": self._new_code},
        )


class HaccConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create a new pending PC entry and show its pairing code."""

    VERSION = 1

    def __init__(self) -> None:
        self._pairing_code: str | None = None
        self._entry_data: dict[str, str] | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self._pairing_code is None:
            self._pairing_code, self._entry_data = new_pairing_entry_data()

        if user_input is not None:
            return self.async_create_entry(title=DEFAULT_TITLE, data=self._entry_data)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={"code": self._pairing_code},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> HaccOptionsFlow:
        return HaccOptionsFlow(config_entry)
