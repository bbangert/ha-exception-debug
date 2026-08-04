"""Tests for the Exception Debug config and options flows."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.exception_debug.const import (
    CONF_ENABLE_EVAL,
    CONF_LEVEL,
    CONF_MAX_ENTRIES,
    CONF_MAX_REPR,
    CONF_TTL,
    DEFAULT_OPTIONS,
    DOMAIN,
    TITLE,
)
from custom_components.exception_debug.handler import ExceptionCaptureHandler

USER_INPUT: dict[str, Any] = {
    CONF_LEVEL: "warning",
    CONF_MAX_ENTRIES: 10,
    CONF_TTL: 60,
    CONF_MAX_REPR: 500,
    CONF_ENABLE_EVAL: True,
}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """The UI flow shows a form and creates an entry from the answers."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == {}
    assert result["options"] == USER_INPUT


async def test_user_flow_defaults(hass: HomeAssistant) -> None:
    """Submitting the form unchanged yields the documented defaults."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(DEFAULT_OPTIONS)
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == DEFAULT_OPTIONS
    assert result["options"][CONF_ENABLE_EVAL] is False


@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_IMPORT])
async def test_single_instance_only(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, source: str
) -> None:
    """A second entry is refused for both the UI and the import path."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source}, data={}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_import_flow_maps_yaml(hass: HomeAssistant) -> None:
    """YAML keys become entry options; unset ones fall back to defaults."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={CONF_LEVEL: "critical", CONF_TTL: 30},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {
        **DEFAULT_OPTIONS,
        CONF_LEVEL: "critical",
        CONF_TTL: 30,
    }


async def test_import_flow_ignores_unknown_keys(hass: HomeAssistant) -> None:
    """Stray YAML keys are dropped rather than stored as options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={CONF_MAX_ENTRIES: 7, "bogus": "value"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {**DEFAULT_OPTIONS, CONF_MAX_ENTRIES: 7}


async def test_options_flow_updates_and_reloads(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Saving options persists them and reloads the entry with new settings."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options == USER_INPUT
    # OptionsFlowWithReload reloaded the entry, so the running integration
    # reflects the new settings rather than the ones it was first built with.
    assert setup_integration.runtime_data.store.max_repr == 500
    # The capture level is the whole point of the reload, so assert it moved.
    capture_handlers = [
        handler
        for handler in logging.root.handlers
        if isinstance(handler, ExceptionCaptureHandler)
    ]
    assert [h.level for h in capture_handlers] == [logging.WARNING]
