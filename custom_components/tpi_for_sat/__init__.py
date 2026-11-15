"""TPI for SAT: a Versatile Thermostat valve algorithm for SAT-controlled boilers."""

from typing import cast

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType
from vtherm_api.vtherm_api import VThermAPI

from .const import DOMAIN, VTHERM_DOMAIN
from .handler import SatTpiHandlerFactory, get_data

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]


def _register_factory(hass: HomeAssistant, api_class: type[VThermAPI]) -> VThermAPI:
    """Register the tpi_for_sat factory unless the API has it; return the API."""
    api: VThermAPI = api_class.get_vtherm_api(hass)  # type: ignore[no-untyped-call]
    factory = SatTpiHandlerFactory()
    if api.get_prop_algorithm(factory.name) is None:
        api.register_prop_algorithm(factory)
    return api


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the factory and register it again whenever VTherm rebuilds its API.

    VTherm drops its API when its last entry unloads and builds a new one when an
    entry sets up again. The API is rebuilt with VTherm's own class, as smartPI
    does, because VTherm can't run on a bare VThermAPI.
    """
    del config
    api_class = type(_register_factory(hass, VThermAPI))

    @callback
    def _async_entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
        del change
        if (
            entry.domain == VTHERM_DOMAIN
            and entry.state is ConfigEntryState.SETUP_IN_PROGRESS
        ):
            _register_factory(hass, api_class)

    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _async_entry_changed)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Apply the entry to a running handler and set up its diagnostic sensor."""
    await _async_apply_entry(hass, entry, entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the sensor; on disable the running handler falls back to the defaults.

    A plain reload keeps the handler's parameters.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.disabled_by:
        await _async_apply_entry(hass, entry, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """On deletion the running handler falls back to the defaults."""
    await _async_apply_entry(hass, entry, None)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply new options live, without reloading this entry or VTherm."""
    await _async_apply_entry(hass, entry, entry)


async def _async_apply_entry(
    hass: HomeAssistant, entry: ConfigEntry, source: ConfigEntry | None
) -> None:
    """Apply `source` to the running handler of the entry's thermostat, if any.

    The handler then recalculates, so the change reaches the valve immediately.
    """
    # The config flow always sets unique_id to the thermostat's unique_id.
    thermostat_id = cast(str, entry.unique_id)
    if (handler := get_data(hass).handlers.get(thermostat_id)) is not None:
        handler.apply_entry(source)
        await handler.async_recalculate()
