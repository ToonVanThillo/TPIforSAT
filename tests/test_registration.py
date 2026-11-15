"""The algorithm is offered to VTherm."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from vtherm_api.vtherm_api import VThermAPI

from custom_components.tpi_for_sat.algo import SatTpiAlgorithm
from custom_components.tpi_for_sat.const import VTHERM_DOMAIN

from .helpers import FakeThermostat, make_tpi_entry, setup_entry, start_handler


async def test_factory_is_registered_with_vtherm_after_setup(
    hass: HomeAssistant,
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    api = VThermAPI.get_vtherm_api(hass)  # type: ignore[no-untyped-call]
    assert "tpi_for_sat" in api.list_prop_algorithms()


async def test_factory_is_registered_again_when_vtherm_rebuilds_its_api(
    hass: HomeAssistant,
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    VThermAPI.reset_vtherm_api()  # type: ignore[no-untyped-call]
    api = VThermAPI.get_vtherm_api(hass)  # type: ignore[no-untyped-call]
    assert api.get_prop_algorithm("tpi_for_sat") is None

    vt_entry = MockConfigEntry(domain=VTHERM_DOMAIN)
    vt_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(vt_entry.entry_id)
    await hass.async_block_till_done()

    api = VThermAPI.get_vtherm_api(hass)  # type: ignore[no-untyped-call]
    assert "tpi_for_sat" in api.list_prop_algorithms()


async def test_created_handler_puts_algorithm_on_thermostat(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    await start_handler(hass, thermostat)
    assert isinstance(thermostat.prop_algorithm, SatTpiAlgorithm)
