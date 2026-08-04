"""Tests for setup, teardown, YAML import and the clear service."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir, llm
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.exception_debug.const import (
    CONF_LEVEL,
    CONF_MAX_ENTRIES,
    CONF_TTL,
    DEFAULT_OPTIONS,
    DOMAIN,
    LLM_API_ID,
)
from custom_components.exception_debug.handler import ExceptionCaptureHandler

from .helpers import LOGGER_NAME, log_exception


def _root_capture_handlers() -> list[ExceptionCaptureHandler]:
    """Capture handlers currently attached to the root logger."""
    return [
        handler
        for handler in logging.root.handlers
        if isinstance(handler, ExceptionCaptureHandler)
    ]


async def test_setup_entry_attaches_handler(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Setting up an entry attaches exactly one handler and builds the store."""
    assert setup_integration.state is ConfigEntryState.LOADED
    assert len(_root_capture_handlers()) == 1
    assert _root_capture_handlers()[0].level == logging.ERROR
    assert setup_integration.runtime_data.store.max_repr == 2000


async def test_unload_entry_detaches_everything(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Unloading removes the handler, clears the store and drops the LLM API."""
    log_exception(LOGGER_NAME)
    store = setup_integration.runtime_data.store
    assert store.list() != []

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()

    assert setup_integration.state is ConfigEntryState.NOT_LOADED
    assert _root_capture_handlers() == []
    assert store.list() == []


async def test_reload_leaves_one_handler(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A reload must not stack a second handler on the root logger."""
    assert await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()

    assert len(_root_capture_handlers()) == 1


async def test_shutdown_detaches_handler(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Config entries are not unloaded on stop, so shutdown detaches directly."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert _root_capture_handlers() == []


async def test_llm_api_is_registered(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The LLM API is registered so core mcp_server can expose the tools."""
    assert LLM_API_ID in {api.id for api in llm.async_get_apis(hass)}


@pytest.mark.parametrize("options", [{**DEFAULT_OPTIONS, CONF_TTL: 60}])
async def test_expiry_timer_releases_frames(
    hass: HomeAssistant, freezer, setup_integration: MockConfigEntry
) -> None:
    """The periodic timer applies the TTL even with no new exceptions."""
    log_exception(LOGGER_NAME)
    entry = setup_integration.runtime_data.store.list()[0]
    assert entry.is_live

    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done()

    assert not entry.is_live
    assert setup_integration.runtime_data.store.get(entry.id) is entry


async def test_yaml_import_creates_entry_and_repair_issue(
    hass: HomeAssistant,
) -> None:
    """YAML config is imported once and flagged as deprecated."""
    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {CONF_LEVEL: "warning", CONF_MAX_ENTRIES: 5}}
    )
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].options == {
        **DEFAULT_OPTIONS,
        CONF_LEVEL: "warning",
        CONF_MAX_ENTRIES: 5,
    }

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "deprecated_yaml")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_setup_without_yaml_creates_no_entry(hass: HomeAssistant) -> None:
    """Loading the component with no YAML block imports nothing."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, "deprecated_yaml") is None


async def test_clear_service(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The clear service empties the store and releases frames."""
    log_exception(LOGGER_NAME)
    store = setup_integration.runtime_data.store
    entry = store.list()[0]

    await hass.services.async_call(DOMAIN, "clear", blocking=True)

    assert store.list() == []
    assert not entry.is_live


async def test_clear_service_without_a_loaded_entry(hass: HomeAssistant) -> None:
    """Calling the service with nothing set up reports it, not a KeyError."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="not set up"):
        await hass.services.async_call(DOMAIN, "clear", blocking=True)
