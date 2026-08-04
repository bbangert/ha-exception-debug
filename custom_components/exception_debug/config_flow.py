"""Config and options flow for the Exception Debug integration.

The integration holds a single config entry — it is a global capture hook on
the root logger, so a second instance would double-capture. That is enforced by
``single_config_entry`` in ``manifest.json``: the flow manager aborts any user
or import flow with ``single_instance_allowed`` before these steps run, so no
guard is needed here.

Every tunable lives in the entry's *options* (not ``data``) so the options flow
can change them; :class:`OptionsFlowWithReload` reloads the entry afterwards,
which re-attaches the log handler at the new level and re-registers the LLM API
with the new ``enable_eval`` setting.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import (
    CONF_ENABLE_EVAL,
    CONF_LEVEL,
    CONF_MAX_ENTRIES,
    CONF_MAX_REPR,
    CONF_TTL,
    DEFAULT_OPTIONS,
    DOMAIN,
    LEVELS,
    MAX_MAX_ENTRIES,
    MAX_MAX_REPR,
    MAX_TTL,
    MIN_MAX_ENTRIES,
    MIN_MAX_REPR,
    MIN_TTL,
    TITLE,
)


def _int_selector(minimum: int, maximum: int) -> vol.All:
    """Build a whole-number box selector (NumberSelector yields floats)."""
    return vol.All(
        NumberSelector(
            NumberSelectorConfig(
                min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX
            )
        ),
        vol.Coerce(int),
    )


def options_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Build the shared config/options form, pre-filled from ``current``."""
    values = {**DEFAULT_OPTIONS, **current}
    return vol.Schema(
        {
            vol.Required(CONF_LEVEL, default=values[CONF_LEVEL]): SelectSelector(
                SelectSelectorConfig(
                    options=list(LEVELS),
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_LEVEL,
                )
            ),
            vol.Required(
                CONF_MAX_ENTRIES, default=values[CONF_MAX_ENTRIES]
            ): _int_selector(MIN_MAX_ENTRIES, MAX_MAX_ENTRIES),
            vol.Required(CONF_TTL, default=values[CONF_TTL]): _int_selector(
                MIN_TTL, MAX_TTL
            ),
            vol.Required(CONF_MAX_REPR, default=values[CONF_MAX_REPR]): _int_selector(
                MIN_MAX_REPR, MAX_MAX_REPR
            ),
            vol.Required(
                CONF_ENABLE_EVAL, default=values[CONF_ENABLE_EVAL]
            ): BooleanSelector(),
        }
    )


class ExceptionDebugConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up the integration from the UI."""
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=user_input)
        return self.async_show_form(
            step_id="user", data_schema=options_schema(DEFAULT_OPTIONS)
        )

    async def async_step_import(
        self, import_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Import the deprecated YAML configuration into a config entry."""
        options = {
            **DEFAULT_OPTIONS,
            **{k: v for k, v in import_data.items() if k in DEFAULT_OPTIONS},
        }
        return self.async_create_entry(title=TITLE, data={}, options=options)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ExceptionDebugOptionsFlow:
        """Return the options flow."""
        return ExceptionDebugOptionsFlow()


class ExceptionDebugOptionsFlow(OptionsFlowWithReload):
    """Change retention/capture options; reloads the entry on save."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and persist the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=options_schema(self.config_entry.options)
        )
