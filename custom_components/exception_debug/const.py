"""Constants for the Exception Debug integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

DOMAIN = "exception_debug"

# Title used for the single config entry.
TITLE = "Exception Debug"

# Configuration keys. Used both by the YAML schema (legacy, imported once) and
# by the config entry's options.
CONF_MAX_ENTRIES = "max_entries"
CONF_LEVEL = "level"
CONF_TTL = "ttl"
CONF_ENABLE_EVAL = "enable_eval"
CONF_MAX_REPR = "max_repr"

# Defaults
DEFAULT_MAX_ENTRIES = 50
DEFAULT_LEVEL = "error"
DEFAULT_TTL = 900  # seconds a captured entry keeps its live frames
DEFAULT_ENABLE_EVAL = False
DEFAULT_MAX_REPR = 2000  # max characters for any single repr() we return

# Bounds, shared by the YAML schema, the config flow and the options flow.
MIN_MAX_ENTRIES = 1
MAX_MAX_ENTRIES = 1000
MIN_TTL = 0
MAX_TTL = 86400
MIN_MAX_REPR = 80
MAX_MAX_REPR = 100000

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_LEVEL: DEFAULT_LEVEL,
    CONF_MAX_ENTRIES: DEFAULT_MAX_ENTRIES,
    CONF_TTL: DEFAULT_TTL,
    CONF_MAX_REPR: DEFAULT_MAX_REPR,
    CONF_ENABLE_EVAL: DEFAULT_ENABLE_EVAL,
}

LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# How often expired entries have their live frames released. Without this the
# TTL would only be enforced when a new exception happens to arrive.
EXPIRY_INTERVAL = timedelta(seconds=60)

# LLM API identifier (selectable inside the core mcp_server options)
LLM_API_ID = "exception_debug"
LLM_API_NAME = "Home Assistant Exception Debugger"

LOGGER = logging.getLogger(__package__)
