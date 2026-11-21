"""Diagnostic sensor showing the integral of one thermostat's algorithm."""

from typing import Any, cast

from homeassistant.components.climate.const import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .algo import SatTpiAlgorithm
from .const import VTHERM_DOMAIN, signal_updated
from .handler import get_data


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the integral sensor of the entry's thermostat."""
    async_add_entities([IntegralSensor(hass, entry)])


class IntegralSensor(SensorEntity):
    """Integral I of the thermostat's algorithm, with the latest update as attributes.

    Attached to the thermostat's device and unavailable while the thermostat
    doesn't run tpi_for_sat.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "integral"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Bind to the thermostat whose unique_id is the entry's unique_id."""
        # The config flow always sets unique_id to the thermostat's unique_id.
        self._thermostat_id = cast(str, entry.unique_id)
        self._attr_unique_id = f"{self._thermostat_id}_integral"
        climate_entity_id = er.async_get(hass).async_get_entity_id(
            CLIMATE_DOMAIN, VTHERM_DOMAIN, self._thermostat_id
        )
        if climate_entity_id is not None:
            self.device_entry = async_entity_id_to_device(hass, climate_entity_id)

    async def async_added_to_hass(self) -> None:
        """Refresh whenever the handler updates, appears or goes away."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_updated(self._thermostat_id),
                self.async_write_ha_state,
            )
        )

    @property
    def _algorithm(self) -> SatTpiAlgorithm | None:
        """The running algorithm, or None if the thermostat doesn't use tpi_for_sat."""
        handler = get_data(self.hass).handlers.get(self._thermostat_id)
        return handler.algorithm if handler is not None else None

    @property
    def available(self) -> bool:
        """Available while a tpi_for_sat handler runs for the thermostat."""
        return self._algorithm is not None

    @property
    def native_value(self) -> float | None:
        """The integral I in [-1, 0]."""
        algorithm = self._algorithm
        return algorithm.integral if algorithm is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """e, e_other, t_o, v_p and saturated of the latest update, and on_percent."""
        algorithm = self._algorithm
        if algorithm is None:
            return None
        return {
            "e": algorithm.e,
            "e_other": algorithm.e_other,
            "t_o": algorithm.t_o,
            "v_p": algorithm.v_p,
            "saturated": algorithm.saturated,
            "on_percent": algorithm.on_percent,
        }
