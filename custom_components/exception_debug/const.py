"""Constants for the Exception Debug integration."""

from __future__ import annotations

import logging

DOMAIN = "exception_debug"

# Data keys stored on hass.data[DOMAIN]
DATA_STORE = "store"
DATA_HANDLER = "handler"
DATA_UNREGISTER = "unregister"

# YAML configuration keys
CONF_MAX_ENTRIES = "max_entries"
CONF_LEVEL = "level"
CONF_TTL = "ttl"
CONF_ENABLE_EVAL = "enable_eval"
CONF_MAX_REPR = "max_repr"

# Defaults
DEFAULT_MAX_ENTRIES = 50
DEFAULT_LEVEL = logging.ERROR
DEFAULT_TTL = 900  # seconds a captured entry keeps its live frames
DEFAULT_ENABLE_EVAL = False
DEFAULT_MAX_REPR = 2000  # max characters for any single repr() we return

# LLM API identifier (selectable inside the core mcp_server options)
LLM_API_ID = "exception_debug"
LLM_API_NAME = "Home Assistant Exception Debugger"

LOGGER = logging.getLogger(__name__)
