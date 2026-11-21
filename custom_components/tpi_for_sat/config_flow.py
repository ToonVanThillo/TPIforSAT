"""Config flow for TPI for SAT: one entry per Versatile Thermostat."""

import math
from typing import Any, cast

import probatio
from homeassistant.components.climate.const import DOMAIN as CLIMATE_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_C_INT,
    CONF_E_FULL,
    CONF_E_START,
    CONF_K_I,
    CONF_OTHER_CLIMATES,
    CONF_OTHER_ENABLED,
    CONF_TAU_LEAK_H,
    DEFAULT_OPTIONS,
    DOMAIN,
    VTHERM_DOMAIN,
)

CONF_THERMOSTAT = "thermostat"


def _finite(value: float) -> float:
    """Reject NaN and infinity, which pass NumberSelector's bounds check."""
    if not math.isfinite(value):
        raise probatio.Invalid("Value must be finite")
    return value


def _number(minimum: float) -> probatio.All[float]:
    """Return a finite free-entry number box with a lower bound."""
    return probatio.All(
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=minimum, step="any", mode=selector.NumberSelectorMode.BOX
            )
        ),
        _finite,
    )


def _options_schema(thermostat: str | None = None) -> probatio.Schema:
    """Return the options form; `thermostat` is left out of the other climates."""
    return probatio.Schema(
        {
            probatio.Required(CONF_C_INT): _number(0),
            probatio.Required(CONF_K_I): _number(0),
            # At least 1 h so the leak step stays accurate at the 0.5 h dt cap.
            probatio.Required(CONF_TAU_LEAK_H): _number(1.0),
            probatio.Required(CONF_OTHER_ENABLED): selector.BooleanSelector(),
            probatio.Optional(
                CONF_OTHER_CLIMATES, default=list
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=CLIMATE_DOMAIN,
                    multiple=True,
                    exclude_entities=[thermostat] if thermostat is not None else [],
                )
            ),
            probatio.Required(CONF_E_START): _number(0),
            probatio.Required(CONF_E_FULL): _number(0),
        }
    )


USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_THERMOSTAT): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=CLIMATE_DOMAIN, integration=VTHERM_DOMAIN
            )
        )
    }
).extend(_options_schema().schema)


def _other_room_errors(
    user_input: dict[str, Any], thermostat: str | None
) -> dict[str, str]:
    """Return the form errors of the other-room fields; None skips the climate check."""
    errors: dict[str, str] = {}
    if user_input[CONF_E_START] >= user_input[CONF_E_FULL]:
        errors[CONF_E_FULL] = "invalid_e_range"
    if thermostat in user_input[CONF_OTHER_CLIMATES]:
        errors[CONF_OTHER_CLIMATES] = "other_includes_thermostat"
    return errors


class TpiForSatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create an entry targeting one VTherm thermostat."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the thermostat and set the parameters.

        The thermostat's unique_id becomes the entry's unique_id; the parameters
        are stored as options so the options flow edits them in place.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_THERMOSTAT]
            reg_entry = er.async_get(self.hass).async_get(entity_id)
            if reg_entry is None:
                errors[CONF_THERMOSTAT] = "invalid_thermostat"
            else:
                await self.async_set_unique_id(reg_entry.unique_id)
                self._abort_if_unique_id_configured()
                errors = _other_room_errors(user_input, entity_id)
                if not errors:
                    state = self.hass.states.get(entity_id)
                    return self.async_create_entry(
                        title=state.name if state is not None else entity_id,
                        data={},
                        options={key: user_input[key] for key in DEFAULT_OPTIONS},
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or DEFAULT_OPTIONS
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TpiForSatOptionsFlow()


class TpiForSatOptionsFlow(OptionsFlow):
    """Edit the parameters; the running handler picks them up without a reload."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the parameters, prefilled with the current options or defaults."""
        errors: dict[str, str] = {}
        thermostat = er.async_get(self.hass).async_get_entity_id(
            CLIMATE_DOMAIN, VTHERM_DOMAIN, cast(str, self.config_entry.unique_id)
        )
        if user_input is not None:
            # The selector already excludes the thermostat.
            errors = _other_room_errors(user_input, None)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _options_schema(thermostat),
                user_input or {**DEFAULT_OPTIONS, **self.config_entry.options},
            ),
            errors=errors,
        )
