"""Tests for the hacc config and options flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hacc.const import (
    DATA_PAIRING_CODE_EXPIRES_AT,
    DATA_PAIRING_CODE_HASH,
    DOMAIN,
    PAIRING_CODE_LENGTH,
)
from custom_components.hacc.pairing import hash_pairing_code


async def test_user_flow_shows_code_and_only_stores_hash(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    code = result["description_placeholders"]["code"]
    assert len(code) == PAIRING_CODE_LENGTH

    result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result2["type"] == FlowResultType.CREATE_ENTRY

    entry = result2["result"]
    assert entry.data[DATA_PAIRING_CODE_HASH] == hash_pairing_code(code)
    assert DATA_PAIRING_CODE_EXPIRES_AT in entry.data
    assert code not in str(entry.data)


async def test_options_flow_regenerates_code(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    entry = result["result"]
    old_hash = entry.data[DATA_PAIRING_CODE_HASH]

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["type"] == FlowResultType.FORM
    new_code = options_result["description_placeholders"]["code"]

    options_result2 = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {}
    )
    assert options_result2["type"] == FlowResultType.CREATE_ENTRY

    assert entry.data[DATA_PAIRING_CODE_HASH] == hash_pairing_code(new_code)
    assert entry.data[DATA_PAIRING_CODE_HASH] != old_hash
