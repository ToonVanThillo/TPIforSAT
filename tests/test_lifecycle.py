"""Entry lifecycle against a running handler."""

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant

from .helpers import FakeThermostat, make_tpi_entry, setup_entry, start_handler

CUSTOM = {"c_int": 4.0, "k_i": 1.0, "tau_leak_h": 8.0}


async def test_setting_up_entry_applies_options_to_running_handler(
    hass: HomeAssistant, thermostat: FakeThermostat, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass, make_tpi_entry(hass, "vt-other"))
    thermostat.current_temperature = 20.1
    await start_handler(hass, thermostat)  # no entry yet: defaults
    assert "No tpi_for_sat config entry" in caplog.text
    thermostat.async_control_heating.reset_mock()

    await setup_entry(hass, make_tpi_entry(hass, options=CUSTOM))

    assert thermostat.prop_algorithm.on_percent == pytest.approx(1 - 4 * 0.1)
    assert thermostat.async_control_heating.await_count == 1


@pytest.mark.parametrize("how", ["disable", "delete"])
async def test_unloading_entry_falls_back_to_defaults_with_warning(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    caplog: pytest.LogCaptureFixture,
    how: str,
) -> None:
    await setup_entry(hass, make_tpi_entry(hass, "vt-other"))
    entry = make_tpi_entry(hass, options=CUSTOM)
    await setup_entry(hass, entry)
    thermostat.current_temperature = 20.1
    await start_handler(hass, thermostat)
    await thermostat.handler.async_recalculate()
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.6)
    assert "No tpi_for_sat config entry" not in caplog.text
    thermostat.async_control_heating.reset_mock()

    if how == "disable":
        await hass.config_entries.async_set_disabled_by(
            entry.entry_id, ConfigEntryDisabler.USER
        )
    else:
        await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert "No tpi_for_sat config entry" in caplog.text
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.8)
    assert thermostat.async_control_heating.await_count == 1


async def test_unloading_entry_keeps_the_integral(
    hass: HomeAssistant, thermostat: FakeThermostat, hass_storage: dict[str, Any]
) -> None:
    hass_storage["tpi_for_sat.vt-living"] = {"version": 1, "data": {"integral": -0.4}}
    entry = make_tpi_entry(hass)
    await setup_entry(hass, entry)
    await start_handler(hass, thermostat)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert thermostat.prop_algorithm.integral == pytest.approx(-0.4)
