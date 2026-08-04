"""Runtime data held on the config entry, plus lookup helpers.

Lives in its own module so the HTTP/WebSocket/service surfaces can resolve the
active store without importing ``__init__`` (which imports them in turn).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .store import ExceptionStore


@dataclass(slots=True)
class ExceptionDebugData:
    """Objects owned by a loaded config entry."""

    store: ExceptionStore


type ExceptionDebugConfigEntry = ConfigEntry[ExceptionDebugData]


class NotConfiguredError(ServiceValidationError):
    """Raised when no Exception Debug config entry is loaded.

    A ``ServiceValidationError`` rather than a plain ``HomeAssistantError``:
    calling the service with nothing set up is a user-actionable mistake, not
    an internal or device failure.
    """

    def __init__(self) -> None:
        """Initialise with the translated message."""
        super().__init__(translation_domain=DOMAIN, translation_key="not_configured")


@callback
def async_get_store(hass: HomeAssistant) -> ExceptionStore | None:
    """Return the store of the loaded config entry, or None if not set up."""
    entries: list[ExceptionDebugConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data.store
    return None


@callback
def async_require_store(hass: HomeAssistant) -> ExceptionStore:
    """Return the loaded store, raising if the integration is not set up."""
    if (store := async_get_store(hass)) is None:
        raise NotConfiguredError
    return store
