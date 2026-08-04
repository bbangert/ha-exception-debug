"""The Exception Debug integration.

Captures Home Assistant exceptions with their live traceback frames and exposes
them for post-mortem introspection: list exceptions, read frame locals, and
(optionally) evaluate code in a frame's context — a debugtoolbar-style console
queryable by an AI agent over MCP, plus authenticated REST/WebSocket surfaces.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ENABLE_EVAL,
    CONF_LEVEL,
    CONF_MAX_ENTRIES,
    CONF_MAX_REPR,
    CONF_TTL,
    DATA_HANDLER,
    DATA_STORE,
    DATA_UNREGISTER,
    DEFAULT_ENABLE_EVAL,
    DEFAULT_LEVEL,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_REPR,
    DEFAULT_TTL,
    DOMAIN,
    LOGGER,
)
from .handler import ExceptionCaptureHandler
from .http_api import async_register_http
from .llm_api import ExceptionDebugAPI
from .store import ExceptionStore

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_MAX_ENTRIES, default=DEFAULT_MAX_ENTRIES): vol.All(
                    int, vol.Range(min=1, max=1000)
                ),
                vol.Optional(CONF_TTL, default=DEFAULT_TTL): vol.All(
                    int, vol.Range(min=0, max=86400)
                ),
                vol.Optional(CONF_MAX_REPR, default=DEFAULT_MAX_REPR): vol.All(
                    int, vol.Range(min=80, max=100000)
                ),
                vol.Optional(CONF_ENABLE_EVAL, default=DEFAULT_ENABLE_EVAL): cv.boolean,
                vol.Optional(CONF_LEVEL, default="error"): vol.In(_LEVELS),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Exception Debug integration from YAML."""
    conf = config.get(DOMAIN, {})
    level = _LEVELS.get(conf.get(CONF_LEVEL, "error"), DEFAULT_LEVEL)
    enable_eval = conf.get(CONF_ENABLE_EVAL, DEFAULT_ENABLE_EVAL)

    store = ExceptionStore(
        max_entries=conf.get(CONF_MAX_ENTRIES, DEFAULT_MAX_ENTRIES),
        ttl=conf.get(CONF_TTL, DEFAULT_TTL),
        max_repr=conf.get(CONF_MAX_REPR, DEFAULT_MAX_REPR),
    )

    # Attach the capture handler directly to the root logger so it sees live
    # exc_info before Home Assistant's queue handler strips it.
    handler = ExceptionCaptureHandler(store, level)
    logging.root.addHandler(handler)

    # Register the LLM API so core mcp_server can expose these tools to agents.
    unregister_llm = llm.async_register_api(
        hass, ExceptionDebugAPI(hass, store, enable_eval)
    )

    hass.data[DOMAIN] = {
        DATA_STORE: store,
        DATA_HANDLER: handler,
        DATA_UNREGISTER: unregister_llm,
    }

    async_register_http(hass)
    _async_register_services(hass, store)

    @callback
    def _teardown(event) -> None:
        logging.root.removeHandler(handler)
        unregister_llm()
        store.clear()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _teardown)

    LOGGER.info(
        "Exception Debug active (level=%s, eval=%s, max_entries=%s, ttl=%ss)",
        logging.getLevelName(level),
        enable_eval,
        conf.get(CONF_MAX_ENTRIES, DEFAULT_MAX_ENTRIES),
        conf.get(CONF_TTL, DEFAULT_TTL),
    )
    return True


@callback
def _async_register_services(hass: HomeAssistant, store: ExceptionStore) -> None:
    """Register the clear service."""

    @callback
    def _clear(call: ServiceCall) -> None:
        store.clear()

    hass.services.async_register(DOMAIN, "clear", _clear)
