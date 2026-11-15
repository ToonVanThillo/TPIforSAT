"""Config and options flow."""

import math
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.tpi_for_sat.algo import SatTpiAlgorithm
from custom_components.tpi_for_sat.const import DOMAIN

from .helpers import FakeThermostat, make_tpi_entry, setup_entry, start_handler

PARAMS = {"c_int": 3.0, "k_i": 0.5, "tau_leak_h": 6.0}


async def _start(hass: HomeAssistant) -> ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_creates_entry_for_the_thermostat(
    hass: HomeAssistant, register_climate: Callable[..., str]
) -> None:
    entity_id = register_climate()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"thermostat": entity_id, **PARAMS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living"
    assert result["data"] == {}
    assert result["options"] == PARAMS
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "vt-living"


async def test_user_flow_title_falls_back_to_entity_id_without_state(
    hass: HomeAssistant, register_climate: Callable[..., str]
) -> None:
    entity_id = register_climate()
    hass.states.async_remove(entity_id)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"thermostat": entity_id, **PARAMS}
    )
    assert result["title"] == entity_id


async def test_user_flow_form_offers_defaults(hass: HomeAssistant) -> None:
    result = await _start(hass)
    schema = result["data_schema"]
    assert schema is not None
    suggested = {
        str(key): key.description["suggested_value"]
        for key in schema.schema
        if key.description
    }
    assert suggested == {"c_int": 2.0, "k_i": 1.0, "tau_leak_h": 8.0}


async def test_user_flow_aborts_for_thermostat_already_configured(
    hass: HomeAssistant, register_climate: Callable[..., str]
) -> None:
    entity_id = register_climate()
    make_tpi_entry(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"thermostat": entity_id, **PARAMS}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_rejects_entity_missing_from_registry(
    hass: HomeAssistant,
) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"thermostat": "climate.ghost", **PARAMS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"thermostat": "invalid_thermostat"}
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.parametrize("bad", [{"c_int": -0.1}, {"k_i": -1.0}, {"tau_leak_h": 0.9}])
async def test_user_flow_rejects_out_of_range_parameters(
    hass: HomeAssistant, register_climate: Callable[..., str], bad: dict[str, float]
) -> None:
    entity_id = register_climate()
    result = await _start(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"thermostat": entity_id, **PARAMS, **bad}
        )


@pytest.mark.parametrize("name", ["c_int", "k_i", "tau_leak_h"])
@pytest.mark.parametrize("bad", [math.nan, math.inf])
async def test_flows_reject_non_finite_parameters(
    hass: HomeAssistant, register_climate: Callable[..., str], name: str, bad: float
) -> None:
    entity_id = register_climate()
    result = await _start(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"thermostat": entity_id, **PARAMS, name: bad}
        )
    entry = make_tpi_entry(hass, "vt-other")
    await setup_entry(hass, entry)
    options = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            options["flow_id"], {**PARAMS, name: bad}
        )


@pytest.mark.parametrize("good", [{"c_int": 0.0}, {"k_i": 0.0}, {"tau_leak_h": 1.0}])
async def test_user_flow_accepts_parameter_lower_bounds(
    hass: HomeAssistant, register_climate: Callable[..., str], good: dict[str, float]
) -> None:
    entity_id = register_climate()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"thermostat": entity_id, **PARAMS, **good}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_applies_live_keeping_integral_without_reload(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
) -> None:
    hass_storage["tpi_for_sat.vt-living"] = {"version": 1, "data": {"integral": -0.4}}
    entry = make_tpi_entry(hass)
    await setup_entry(hass, entry)
    handler = await start_handler(hass, thermostat)
    algorithm: SatTpiAlgorithm = thermostat.prop_algorithm
    thermostat.current_temperature = 20.1  # e = -0.1
    thermostat.async_control_heating.reset_mock()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"c_int": 4.0, "k_i": 1.0, "tau_leak_h": 8.0}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.state is ConfigEntryState.LOADED
    assert thermostat.async_control_heating.await_count == 1
    assert thermostat.handler is handler
    assert thermostat.prop_algorithm is algorithm
    assert algorithm.integral == pytest.approx(-0.4)
    # v_p = 1 + 4*(-0.1) - 0.4 with the new c_int
    assert algorithm.on_percent == pytest.approx(0.2)
