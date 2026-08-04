"""The Exception Debug integration.

Captures Home Assistant exceptions with their live traceback frames and exposes
them for post-mortem introspection: list exceptions, read frame locals, and
(optionally) evaluate code in a frame's context — a debugtoolbar-style console
queryable by an AI agent over MCP, plus authenticated REST/WebSocket surfaces.

Setup is split in two:

* :func:`async_setup` registers the process-wide surfaces (REST views,
  WebSocket commands, services) exactly once, and imports any legacy YAML
  configuration into a config entry.
* :func:`async_setup_entry` owns everything that depends on the options: the
  store, the root-logger capture handler, the LLM API registration and the
  expiry timer. All of it is torn down through ``entry.async_on_unload`` so a
  reload (e.g. after an options change) fully re-applies the new settings.
"""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, issue_registry as ir, llm
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    CONF_ENABLE_EVAL,
    CONF_LEVEL,
    CONF_MAX_ENTRIES,
    CONF_MAX_REPR,
    CONF_TTL,
    DEFAULT_ENABLE_EVAL,
    DEFAULT_LEVEL,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_REPR,
    DEFAULT_OPTIONS,
    DEFAULT_TTL,
    DOMAIN,
    EXPIRY_INTERVAL,
    LEVELS,
    LOGGER,
    MAX_MAX_ENTRIES,
    MAX_MAX_REPR,
    MAX_TTL,
    MIN_MAX_ENTRIES,
    MIN_MAX_REPR,
    MIN_TTL,
)
from .data import (
    ExceptionDebugConfigEntry,
    ExceptionDebugData,
    async_require_store,
)
from .handler import ExceptionCaptureHandler
from .http_api import async_register_http
from .llm_api import ExceptionDebugAPI
from .store import ExceptionStore

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_MAX_ENTRIES, default=DEFAULT_MAX_ENTRIES): vol.All(
                    int, vol.Range(min=MIN_MAX_ENTRIES, max=MAX_MAX_ENTRIES)
                ),
                vol.Optional(CONF_TTL, default=DEFAULT_TTL): vol.All(
                    int, vol.Range(min=MIN_TTL, max=MAX_TTL)
                ),
                vol.Optional(CONF_MAX_REPR, default=DEFAULT_MAX_REPR): vol.All(
                    int, vol.Range(min=MIN_MAX_REPR, max=MAX_MAX_REPR)
                ),
                vol.Optional(CONF_ENABLE_EVAL, default=DEFAULT_ENABLE_EVAL): cv.boolean,
                vol.Optional(CONF_LEVEL, default=DEFAULT_LEVEL): vol.In(LEVELS),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the process-wide surfaces and import legacy YAML config."""
    async_register_http(hass)
    _async_register_services(hass)

    if (conf := config.get(DOMAIN)) is not None:
        hass.async_create_task(_async_import_yaml(hass, conf))

    return True


async def _async_import_yaml(hass: HomeAssistant, conf: ConfigType) -> None:
    """Create a config entry from YAML and raise a deprecation repair issue."""
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_yaml",
        breaks_in_ha_version=None,
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ExceptionDebugConfigEntry
) -> bool:
    """Set up the capture handler, store and LLM API for a config entry."""
    options = {**DEFAULT_OPTIONS, **entry.options}
    level = LEVELS[options[CONF_LEVEL]]
    enable_eval: bool = options[CONF_ENABLE_EVAL]

    store = ExceptionStore(
        max_entries=options[CONF_MAX_ENTRIES],
        ttl=options[CONF_TTL],
        max_repr=options[CONF_MAX_REPR],
    )

    # Attach the capture handler directly to the root logger so it sees live
    # exc_info before Home Assistant's queue handler strips it.
    handler = ExceptionCaptureHandler(store, level)
    logging.root.addHandler(handler)

    @callback
    def _detach() -> None:
        logging.root.removeHandler(handler)
        store.clear()

    entry.async_on_unload(_detach)

    # Config entries are not unloaded on shutdown, so detach explicitly rather
    # than keep capturing (and pinning frames) all the way through teardown.
    # Both paths are idempotent.
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: _detach())
    )

    # Register the LLM API so core mcp_server can expose these tools to agents.
    entry.async_on_unload(
        llm.async_register_api(hass, ExceptionDebugAPI(hass, store, enable_eval))
    )

    @callback
    def _expire(now: datetime) -> None:
        store.expire(now.timestamp())

    # The TTL would otherwise only be applied when a new exception arrives,
    # leaving frames (and everything their locals pin) alive indefinitely on a
    # quiet system.
    entry.async_on_unload(
        async_track_time_interval(
            hass, _expire, EXPIRY_INTERVAL, cancel_on_shutdown=True
        )
    )

    entry.runtime_data = ExceptionDebugData(store=store)

    LOGGER.debug(
        "Exception Debug active (level=%s, eval=%s, max_entries=%s, ttl=%ss)",
        options[CONF_LEVEL],
        enable_eval,
        options[CONF_MAX_ENTRIES],
        options[CONF_TTL],
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ExceptionDebugConfigEntry
) -> bool:
    """Unload a config entry; teardown runs via ``entry.async_on_unload``."""
    return True


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the clear service."""

    @callback
    def _clear(call: ServiceCall) -> None:
        async_require_store(hass).clear()

    # Empty-but-strict schema: without it voluptuous is bypassed entirely
    # and arbitrary extra keys are silently accepted.
    hass.services.async_register(DOMAIN, "clear", _clear, schema=vol.Schema({}))
