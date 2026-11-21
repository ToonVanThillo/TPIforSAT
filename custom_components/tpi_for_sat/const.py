"""Constants for the TPI for SAT integration."""

from typing import Any

from .algo import (
    DEFAULT_C_INT,
    DEFAULT_E_FULL,
    DEFAULT_E_START,
    DEFAULT_K_I,
    DEFAULT_TAU_LEAK_H,
)

DOMAIN = "tpi_for_sat"
VTHERM_DOMAIN = "versatile_thermostat"

# Keys of DEFAULT_PARAMETERS match the keyword arguments of set_parameters.
CONF_C_INT = "c_int"
CONF_K_I = "k_i"
CONF_TAU_LEAK_H = "tau_leak_h"
CONF_E_START = "e_start"
CONF_E_FULL = "e_full"
CONF_OTHER_ENABLED = "other_enabled"
CONF_OTHER_CLIMATES = "other_climates"

DEFAULT_PARAMETERS: dict[str, float] = {
    CONF_C_INT: DEFAULT_C_INT,
    CONF_K_I: DEFAULT_K_I,
    CONF_TAU_LEAK_H: DEFAULT_TAU_LEAK_H,
    CONF_E_START: DEFAULT_E_START,
    CONF_E_FULL: DEFAULT_E_FULL,
}

DEFAULT_OPTIONS: dict[str, Any] = {
    **DEFAULT_PARAMETERS,
    CONF_OTHER_ENABLED: True,
    CONF_OTHER_CLIMATES: (),
}


def signal_updated(unique_id: str) -> str:
    """Return the signal sent when a thermostat's handler or algorithm changes."""
    return f"{DOMAIN}_updated_{unique_id}"
