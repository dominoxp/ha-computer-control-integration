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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

from . import access, connection
from .const import DATA_DEVICE_ID, DATA_DEVICE_KEY_HASH, DATA_DEVICE_NAME, DOMAIN
from .pairing import new_pairing_entry_data

DEFAULT_TITLE = "HA Computer Control (wird gekoppelt)"
_STALE_DEVICE_KEYS = (DATA_DEVICE_ID, DATA_DEVICE_KEY_HASH, DATA_DEVICE_NAME)

_READ_PREFIX = "read:"
_CALL_PREFIX = "call:"


def _read_option(entity_id: str) -> str:
    return f"{_READ_PREFIX}{entity_id}"


def _call_option(key: str) -> str:
    return f"{_CALL_PREFIX}{key}"


def _split_options(keys: list[str]) -> tuple[list[str], list[str]]:
    """Ausgewählte Multi-Select-Schlüssel in (Entity-Ids, Service-Keys) trennen."""
    reads = [k.removeprefix(_READ_PREFIX) for k in keys if k.startswith(_READ_PREFIX)]
    calls = [k.removeprefix(_CALL_PREFIX) for k in keys if k.startswith(_CALL_PREFIX)]
    return reads, calls


def _call_label(key: str, grant: access.CallGrant) -> str:
    target = f" (Ziel: {', '.join(grant.targets)})" if grant.targets else ""
    return f"Schalten: {key}{target}"


def _service_id(value: Any) -> str:
    """``ActionSelector``-Wert auf ``domain.service`` normalisieren.

    Je nach Frontend-Version liefert er einen blanken String oder ein Objekt
    mit ``action``/``service`` - beides landet hier als derselbe String."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("action") or value.get("service") or "")
    return ""


class HaccOptionsFlow(config_entries.OptionsFlow):
    """Kopplungs-Code neu erzeugen oder Zugriffs-Freigaben verwalten."""

    def __init__(self) -> None:
        # ``config_entry`` nicht selbst setzen - seit HA 2024.11 ist es eine
        # Nur-Lese-Property, die der Flow-Manager selbst füllt.
        self._new_code: str | None = None
        self._new_entry_data: dict[str, str] | None = None

    # -- Einstiegs-Menü -----------------------------------------------------

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="init", menu_options=["pairing_code", "access"])

    async def async_step_pairing_code(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
            step_id="pairing_code",
            data_schema=vol.Schema({}),
            description_placeholders={"code": self._new_code},
        )

    # -- Freigaben ------------------------------------------------------------

    def _has_status(self, status: access.AccessStatus) -> bool:
        reads = access.read_grants(self.config_entry).values()
        calls = access.call_grants(self.config_entry).values()
        return any(g.status is status for g in reads) or any(g.status is status for g in calls)

    async def async_step_access(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        menu = []
        if self._has_status(access.AccessStatus.REQUESTED):
            menu.append("access_pending")
        if self._has_status(access.AccessStatus.GRANTED):
            menu.append("access_granted")
        if self._has_status(access.AccessStatus.DENIED):
            menu.append("access_denied")
        menu += ["access_grant_read", "access_grant_call", "access_discovery"]
        return self.async_show_menu(step_id="access", menu_options=menu)

    async def _async_apply_and_return(self) -> FlowResult:
        await connection.async_apply_access_change(self.hass, self.config_entry)
        return await self.async_step_access()

    async def async_step_access_pending(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        options: dict[str, str] = {}
        for entity_id, grant in access.read_grants(self.config_entry).items():
            if grant.status is access.AccessStatus.REQUESTED:
                options[_read_option(entity_id)] = f"Lesen: {entity_id}"
        for key, grant in access.call_grants(self.config_entry).items():
            if grant.status is access.AccessStatus.REQUESTED:
                options[_call_option(key)] = _call_label(key, grant)

        if user_input is not None:
            self._apply_bulk(user_input.get("grant", []), access.AccessStatus.GRANTED)
            self._apply_bulk(user_input.get("deny", []), access.AccessStatus.DENIED)
            return await self._async_apply_and_return()

        schema = vol.Schema(
            {
                vol.Optional("grant", default=[]): cv.multi_select(options),
                vol.Optional("deny", default=[]): cv.multi_select(options),
            }
        )
        return self.async_show_form(step_id="access_pending", data_schema=schema)

    async def async_step_access_granted(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        options: dict[str, str] = {}
        for entity_id, grant in access.read_grants(self.config_entry).items():
            if grant.status is access.AccessStatus.GRANTED:
                options[_read_option(entity_id)] = f"Lesen: {entity_id}"
        for key, grant in access.call_grants(self.config_entry).items():
            if grant.status is access.AccessStatus.GRANTED:
                options[_call_option(key)] = _call_label(key, grant)

        if user_input is not None:
            reads, calls = _split_options(user_input.get("revoke", []))
            if reads:
                access.revoke_read(self.hass, self.config_entry, reads)
            if calls:
                access.revoke_call(self.hass, self.config_entry, calls)
            return await self._async_apply_and_return()

        schema = vol.Schema({vol.Optional("revoke", default=[]): cv.multi_select(options)})
        return self.async_show_form(step_id="access_granted", data_schema=schema)

    async def async_step_access_denied(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        options: dict[str, str] = {}
        for entity_id, grant in access.read_grants(self.config_entry).items():
            if grant.status is access.AccessStatus.DENIED:
                options[_read_option(entity_id)] = f"Lesen: {entity_id}"
        for key, grant in access.call_grants(self.config_entry).items():
            if grant.status is access.AccessStatus.DENIED:
                options[_call_option(key)] = _call_label(key, grant)

        if user_input is not None:
            self._apply_bulk(user_input.get("reopen", []), access.AccessStatus.REQUESTED)
            return await self._async_apply_and_return()

        schema = vol.Schema({vol.Optional("reopen", default=[]): cv.multi_select(options)})
        return self.async_show_form(step_id="access_denied", data_schema=schema)

    def _apply_bulk(self, keys: list[str], status: access.AccessStatus) -> None:
        reads, calls = _split_options(keys)
        if reads:
            access.set_read_status(self.hass, self.config_entry, reads, status)
        if calls:
            access.set_call_status(self.hass, self.config_entry, calls, status)

    async def async_step_access_grant_read(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            entities = user_input.get("entities", [])
            if entities:
                access.grant_read(self.hass, self.config_entry, entities)
            return await self._async_apply_and_return()

        schema = vol.Schema(
            {
                vol.Optional("entities", default=[]): selector.EntitySelector(
                    selector.EntitySelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(step_id="access_grant_read", data_schema=schema)

    async def async_step_access_grant_call(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            service = _service_id(user_input.get("service"))
            target = user_input.get("target")
            if service and "." in service:
                domain, _, svc = service.partition(".")
                access.grant_call(
                    self.hass, self.config_entry, domain, svc, [target] if target else []
                )
            return await self._async_apply_and_return()

        schema = vol.Schema(
            {
                vol.Required("service"): selector.ActionSelector(),
                vol.Optional("target"): selector.EntitySelector(),
            }
        )
        return self.async_show_form(step_id="access_grant_call", data_schema=schema)

    async def async_step_access_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            access.set_discovery_enabled(
                self.hass, self.config_entry, bool(user_input.get("enabled", False))
            )
            return await self._async_apply_and_return()

        schema = vol.Schema(
            {vol.Optional("enabled", default=access.discovery_enabled(self.config_entry)): bool}
        )
        return self.async_show_form(step_id="access_discovery", data_schema=schema)


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
        return HaccOptionsFlow()
