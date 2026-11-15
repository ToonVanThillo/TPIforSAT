"""Constants for the TPI for SAT integration."""

from .algo import DEFAULT_C_INT, DEFAULT_K_I, DEFAULT_TAU_LEAK_H

DOMAIN = "tpi_for_sat"
VTHERM_DOMAIN = "versatile_thermostat"

# Option keys match the keyword arguments of SatTpiAlgorithm.set_parameters.
CONF_C_INT = "c_int"
CONF_K_I = "k_i"
CONF_TAU_LEAK_H = "tau_leak_h"

DEFAULT_PARAMETERS: dict[str, float] = {
    CONF_C_INT: DEFAULT_C_INT,
    CONF_K_I: DEFAULT_K_I,
    CONF_TAU_LEAK_H: DEFAULT_TAU_LEAK_H,
}


def signal_updated(unique_id: str) -> str:
    """Return the signal sent when a thermostat's handler or algorithm changes."""
    return f"{DOMAIN}_updated_{unique_id}"
