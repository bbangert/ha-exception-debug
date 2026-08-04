"""Shared fixtures for the Exception Debug tests."""

from __future__ import annotations

from collections.abc import Generator
import logging
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.exception_debug.const import (
    CONF_ENABLE_EVAL,
    DEFAULT_OPTIONS,
    DOMAIN,
    TITLE,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def restore_root_handlers() -> Generator[None]:
    """Keep a capture handler from leaking between tests.

    Home Assistant does not unload config entries on shutdown, so an entry left
    loaded at the end of a test would keep its handler attached to the global
    root logger. Teardown-on-unload is asserted explicitly in ``test_init``.
    """
    before = list(logging.root.handlers)
    yield
    logging.root.handlers[:] = before


@pytest.fixture
def options() -> dict[str, Any]:
    """Default entry options; override in a test via parametrisation."""
    return dict(DEFAULT_OPTIONS)


@pytest.fixture
def mock_config_entry(options: dict[str, Any]) -> MockConfigEntry:
    """Return a config entry for the integration, not yet added to hass."""
    return MockConfigEntry(domain=DOMAIN, title=TITLE, data={}, options=options)


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Add and set up the config entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
def eval_options() -> dict[str, Any]:
    """Options with in-frame evaluation enabled."""
    return {**DEFAULT_OPTIONS, CONF_ENABLE_EVAL: True}
