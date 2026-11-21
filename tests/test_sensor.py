"""Diagnostic integral sensor."""

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .helpers import FakeThermostat, make_tpi_entry, setup_entry, start_handler


def sensor_entity_id(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "tpi_for_sat", "vt-living_integral"
    )
    assert entity_id is not None
    return entity_id


async def test_sensor_is_unavailable_until_handler_runs_and_after_it_is_removed(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    entity_id = sensor_entity_id(hass)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    handler = await start_handler(hass, thermostat)
    assert hass.states.get(entity_id).state == "0.0"  # type: ignore[union-attr]

    handler.remove()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


async def test_sensor_shows_integral_and_latest_update_as_attributes(
    hass: HomeAssistant, thermostat: FakeThermostat, hass_storage: dict[str, Any]
) -> None:
    hass_storage["tpi_for_sat.vt-living"] = {"version": 1, "data": {"integral": -0.3}}
    await setup_entry(hass, make_tpi_entry(hass))
    thermostat.current_temperature = 19.8  # e = 0.2, v_p = 1 + 0.4 - 0.3 = 1.1
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()
    await hass.async_block_till_done()

    state = hass.states.get(sensor_entity_id(hass))
    assert state is not None
    assert float(state.state) == pytest.approx(-0.3)
    assert state.attributes["e"] == pytest.approx(0.2)
    assert state.attributes["v_p"] == pytest.approx(1.1)
    assert state.attributes["saturated"] is True
    assert state.attributes["on_percent"] == pytest.approx(1.0)


async def test_sensor_follows_updates_of_the_handler(
    hass: HomeAssistant, thermostat: FakeThermostat, freezer: Any
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    thermostat.current_temperature = 20.1
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()
    freezer.tick(1800)
    await handler.async_recalculate()
    await hass.async_block_till_done()
    state = hass.states.get(sensor_entity_id(hass))
    assert state is not None
    assert float(state.state) == pytest.approx(thermostat.prop_algorithm.integral)
    assert float(state.state) < 0


async def test_sensor_metadata_and_device(
    hass: HomeAssistant, register_climate: Callable[..., str]
) -> None:
    climate_id = register_climate()
    await setup_entry(hass, make_tpi_entry(hass))
    registry = er.async_get(hass)
    entry = registry.async_get(sensor_entity_id(hass))
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.has_entity_name is True
    climate = registry.async_get(climate_id)
    assert climate is not None
    assert entry.device_id == climate.device_id
    assert dr.async_get(hass).async_get(entry.device_id) is not None  # type: ignore[arg-type]
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert state.name == "Living TPI for SAT integral"


async def test_sensor_is_per_entry(hass: HomeAssistant) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    await setup_entry(hass, make_tpi_entry(hass, "vt-bedroom"))
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", "tpi_for_sat", "vt-living_integral")
    assert registry.async_get_entity_id("sensor", "tpi_for_sat", "vt-bedroom_integral")


async def test_sensor_attributes_include_other_room_term_and_none_while_frozen(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    hass.states.async_set(
        "climate.a", "heat", {"temperature": 21.0, "current_temperature": 20.25}
    )
    options = {
        "c_int": 2.0,
        "k_i": 1.0,
        "tau_leak_h": 8.0,
        "other_enabled": True,
        "other_climates": ["climate.a"],
        "e_start": 0.5,
        "e_full": 1.0,
    }
    await setup_entry(hass, make_tpi_entry(hass, options=options))
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()  # e_other = 0.75 -> T_o = -0.5
    await hass.async_block_till_done()
    state = hass.states.get(sensor_entity_id(hass))
    assert state is not None
    assert state.attributes["e_other"] == pytest.approx(0.75)
    assert state.attributes["t_o"] == pytest.approx(-0.5)

    thermostat.vtherm_hvac_mode = "off"
    await handler.async_recalculate()
    await hass.async_block_till_done()
    state = hass.states.get(sensor_entity_id(hass))
    assert state is not None
    assert state.attributes["e_other"] is None
    assert state.attributes["t_o"] is None
