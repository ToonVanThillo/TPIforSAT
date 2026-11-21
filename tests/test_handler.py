"""Handler behaviour against a fake VTherm thermostat in the real HA harness."""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.tpi_for_sat.algo import SatTpiAlgorithm

from .helpers import (
    FakeThermostat,
    make_tpi_entry,
    setup_entry,
    start_handler,
)

STORE_KEY = "tpi_for_sat.vt-living"


async def tick(hass: HomeAssistant, freezer: Any, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_parameters_come_from_the_entry_of_this_thermostat(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    make_tpi_entry(hass, "vt-other", {"c_int": 9.0, "k_i": 1.0, "tau_leak_h": 8.0})
    entry = make_tpi_entry(hass, options={"c_int": 4.0, "k_i": 1.0, "tau_leak_h": 8.0})
    await setup_entry(hass, entry)
    thermostat.current_temperature = 20.1  # e = -0.1
    await start_handler(hass, thermostat)
    await thermostat.handler.async_recalculate()
    assert thermostat.prop_algorithm.on_percent == pytest.approx(1 - 4 * 0.1)


async def test_without_entry_defaults_apply_and_a_warning_is_logged(
    hass: HomeAssistant, thermostat: FakeThermostat, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass, make_tpi_entry(hass, "vt-other"))
    thermostat.current_temperature = 20.1
    await start_handler(hass, thermostat)
    await thermostat.handler.async_recalculate()
    assert "No tpi_for_sat config entry" in caplog.text
    assert thermostat.prop_algorithm.on_percent == pytest.approx(1 - 2 * 0.1)


async def test_disabled_entry_is_ignored_like_a_missing_one(
    hass: HomeAssistant, thermostat: FakeThermostat, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass, make_tpi_entry(hass, "vt-other"))
    make_tpi_entry(
        hass,
        options={"c_int": 4.0, "k_i": 1.0, "tau_leak_h": 8.0},
        disabled_by=ConfigEntryDisabler.USER,
    )
    thermostat.current_temperature = 20.1
    await start_handler(hass, thermostat)
    await thermostat.handler.async_recalculate()
    assert "No tpi_for_sat config entry" in caplog.text
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.8)


async def test_restored_integral_is_used_by_the_first_update(
    hass: HomeAssistant, thermostat: FakeThermostat, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORE_KEY] = {"version": 1, "data": {"integral": -0.4}}
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    assert thermostat.prop_algorithm.integral == pytest.approx(-0.4)
    assert thermostat.prop_algorithm.on_percent is None  # VTherm's startup update
    await handler.async_recalculate()
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.6)


@pytest.mark.parametrize(
    "data", [{"integral": "abc"}, {"other": 1}, {"integral": None}]
)
async def test_malformed_stored_state_is_ignored_with_a_warning(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    data: dict[str, Any],
) -> None:
    hass_storage[STORE_KEY] = {"version": 1, "data": data}
    await setup_entry(hass, make_tpi_entry(hass))
    await start_handler(hass, thermostat)
    assert thermostat.prop_algorithm.integral == 0.0
    assert "Could not restore the integral, starting from 0" in caplog.text


async def test_unreadable_store_starts_from_zero_with_a_warning(
    hass: HomeAssistant, thermostat: FakeThermostat, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    with patch.object(Store, "async_load", side_effect=OSError("disk gone")):
        await start_handler(hass, thermostat)
    assert thermostat.prop_algorithm.integral == 0.0
    assert "Could not restore the integral, starting from 0" in caplog.text


async def test_control_heating_starts_cycle_with_on_percent(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    thermostat.current_temperature = 20.3  # v = 0.4
    await handler.async_recalculate()
    thermostat.cycle_scheduler.start_cycle.reset_mock()
    await handler.control_heating(force=True)
    thermostat.cycle_scheduler.start_cycle.assert_awaited_once_with(
        "heat", pytest.approx(0.4), True
    )


async def test_control_heating_skips_cycle_when_on_percent_unknown(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    thermostat.current_temperature = None
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()
    assert thermostat.on_percent is None
    await handler.control_heating()
    thermostat.cycle_scheduler.start_cycle.assert_not_awaited()


async def test_control_heating_in_off_mode_turns_active_device_off(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    thermostat.vtherm_hvac_mode = "off"
    thermostat.is_device_active = True
    await handler.control_heating()
    thermostat.async_underlying_entity_turn_off.assert_awaited_once()
    thermostat.cycle_scheduler.start_cycle.assert_not_awaited()


async def test_control_heating_in_off_mode_cancels_running_cycle(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    thermostat.vtherm_hvac_mode = "off"
    thermostat.cycle_scheduler.is_cycle_running = True
    await handler.control_heating()
    thermostat.cycle_scheduler.cancel_cycle.assert_awaited_once()
    thermostat.async_underlying_entity_turn_off.assert_not_awaited()
    thermostat.cycle_scheduler.start_cycle.assert_not_awaited()


async def test_integral_is_saved_debounced_and_only_when_changed(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    thermostat.current_temperature = 20.1
    await handler.async_recalculate()  # records time
    await tick(hass, freezer, 60)  # not a tick of the 5 min timer
    await handler.async_recalculate()  # integrates 1 min: I changes
    assert thermostat.prop_algorithm.integral < 0
    assert STORE_KEY not in hass_storage

    # Stop the periodic update so I can't change before the write is due.
    thermostat.vtherm_hvac_mode = "off"
    await handler.on_state_changed(True)
    await tick(hass, freezer, 300)
    saved = hass_storage[STORE_KEY]
    assert saved["version"] == 1
    assert saved["data"]["integral"] == pytest.approx(
        thermostat.prop_algorithm.integral
    )

    # Nothing changed since the write, so nothing is written again.
    del hass_storage[STORE_KEY]
    await handler.control_heating()
    await tick(hass, freezer, 301)
    assert STORE_KEY not in hass_storage


async def test_remove_saves_immediately(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    thermostat.current_temperature = 20.1
    await handler.async_recalculate()
    freezer.tick(timedelta(minutes=1))
    await handler.async_recalculate()
    integral = thermostat.prop_algorithm.integral
    handler.remove()
    await hass.async_block_till_done()
    assert hass_storage[STORE_KEY]["data"]["integral"] == pytest.approx(integral)


async def test_recalculate_runs_algorithm_then_has_vtherm_apply_it(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    calls = thermostat.async_control_heating.await_count
    thermostat.current_temperature = 20.3
    await handler.async_recalculate()
    assert isinstance(thermostat.prop_algorithm, SatTpiAlgorithm)
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.4)
    assert thermostat.async_control_heating.await_count == calls + 1


async def test_periodic_tick_moves_output_while_temperature_is_steady(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    hass_storage[STORE_KEY] = {"version": 1, "data": {"integral": -0.5}}
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()  # VTherm's startup update
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.5)
    calls = thermostat.async_control_heating.await_count

    await tick(hass, freezer, 300)

    # Leak over 1/12 h with tau 8 h (e = 0, so no integration).
    leaked = -0.5 * (1 - (1 / 12) / 8)
    assert thermostat.prop_algorithm.integral == pytest.approx(leaked)
    assert thermostat.prop_algorithm.on_percent == pytest.approx(1 + leaked)
    assert thermostat.async_control_heating.await_count == calls + 1


async def test_periodic_tick_runs_only_in_heat_mode(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    hass_storage[STORE_KEY] = {"version": 1, "data": {"integral": -0.5}}
    thermostat.vtherm_hvac_mode = "off"
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    calls = thermostat.async_control_heating.await_count

    await tick(hass, freezer, 600)
    assert thermostat.async_control_heating.await_count == calls  # off: no timer

    thermostat.vtherm_hvac_mode = "heat"
    await handler.on_state_changed(True)
    await handler.async_recalculate()
    calls = thermostat.async_control_heating.await_count
    await tick(hass, freezer, 300)
    assert thermostat.async_control_heating.await_count == calls + 1

    thermostat.vtherm_hvac_mode = "off"
    await handler.on_state_changed(True)
    calls = thermostat.async_control_heating.await_count
    await tick(hass, freezer, 600)
    assert thermostat.async_control_heating.await_count == calls


async def test_remove_stops_the_periodic_tick(
    hass: HomeAssistant, thermostat: FakeThermostat, freezer: Any
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    handler.remove()
    await hass.async_block_till_done()
    calls = thermostat.async_control_heating.await_count
    await tick(hass, freezer, 600)
    assert thermostat.async_control_heating.await_count == calls


async def test_removed_handler_never_restarts_its_timer(
    hass: HomeAssistant, thermostat: FakeThermostat, freezer: Any
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    handler.remove()
    await handler.on_state_changed(True)
    await handler.async_recalculate()
    calls = thermostat.async_control_heating.await_count
    await tick(hass, freezer, 600)
    assert thermostat.async_control_heating.await_count == calls


async def test_vtherm_reload_keeps_integral_even_if_store_is_older(
    hass: HomeAssistant, hass_storage: dict[str, Any], freezer: Any
) -> None:
    hass_storage[STORE_KEY] = {"version": 1, "data": {"integral": -0.1}}
    await setup_entry(hass, make_tpi_entry(hass))
    old = FakeThermostat(hass)
    handler = await start_handler(hass, old)
    old.current_temperature = 20.1
    await handler.async_recalculate()
    freezer.tick(timedelta(minutes=20))
    await handler.async_recalculate()
    changed = old.prop_algorithm.integral
    assert changed < -0.1
    handler.remove()

    # The stale value is what is on disk when the new handler starts.
    hass_storage[STORE_KEY] = {"version": 1, "data": {"integral": -0.1}}
    new = FakeThermostat(hass)
    new_handler = await start_handler(hass, new)
    try:
        assert new.prop_algorithm.integral == pytest.approx(changed)
    finally:
        new_handler.remove()
        await hass.async_block_till_done()


async def test_pending_save_is_flushed_at_shutdown(
    hass: HomeAssistant,
    thermostat: FakeThermostat,
    hass_storage: dict[str, Any],
    freezer: Any,
) -> None:
    await setup_entry(hass, make_tpi_entry(hass))
    handler = await start_handler(hass, thermostat)
    thermostat.current_temperature = 20.1
    await handler.async_recalculate()
    freezer.tick(timedelta(minutes=1))
    await handler.async_recalculate()
    assert STORE_KEY not in hass_storage

    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    assert hass_storage[STORE_KEY]["data"]["integral"] == pytest.approx(
        thermostat.prop_algorithm.integral
    )
    assert thermostat.prop_algorithm.integral < 0


async def test_plain_reload_never_applies_defaults(
    hass: HomeAssistant, thermostat: FakeThermostat, caplog: pytest.LogCaptureFixture
) -> None:
    entry = make_tpi_entry(hass, options={"c_int": 4.0, "k_i": 1.0, "tau_leak_h": 8.0})
    await setup_entry(hass, entry)
    thermostat.current_temperature = 20.1  # 0.6 with c_int 4, 0.8 with defaults
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()
    sent: list[float] = []
    thermostat.cycle_scheduler.start_cycle.side_effect = (
        lambda _mode, on_percent, _force: sent.append(on_percent)
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await handler.async_recalculate()

    assert "No tpi_for_sat config entry" not in caplog.text
    assert sent
    assert sent == [pytest.approx(0.6)] * len(sent)


# --- Other-room term ---------------------------------------------------------


def set_other(
    hass: HomeAssistant,
    entity_id: str,
    target: Any,
    current: Any,
    state: str = "heat",
) -> None:
    attributes = {"temperature": target, "current_temperature": current}
    hass.states.async_set(entity_id, state, attributes)


def other_options(*climates: str, **extra: Any) -> dict[str, Any]:
    return {
        "c_int": 2.0,
        "k_i": 1.0,
        "tau_leak_h": 8.0,
        "other_enabled": True,
        "other_climates": list(climates),
        "e_start": 0.5,
        "e_full": 1.0,
        **extra,
    }


async def e_other_of(
    hass: HomeAssistant, thermostat: FakeThermostat, options: dict[str, Any]
) -> float | None:
    await setup_entry(hass, make_tpi_entry(hass, options=options))
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()
    return thermostat.prop_algorithm.e_other  # type: ignore[no-any-return]


async def test_e_other_is_the_largest_error_among_heating_other_climates(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 21.0, 20.5)
    set_other(hass, "climate.b", 21.0, 19.5)  # largest: 1.5
    set_other(hass, "climate.c", 18.0, 21.0)  # negative error
    options = other_options("climate.a", "climate.b", "climate.c")
    assert await e_other_of(hass, thermostat, options) == pytest.approx(1.5)


async def test_e_other_can_be_negative_when_all_other_rooms_are_warm(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 18.0, 21.0)
    assert await e_other_of(
        hass, thermostat, other_options("climate.a")
    ) == pytest.approx(-3.0)


@pytest.mark.parametrize(
    "state",
    [
        ("climate.a", 21.0, 15.0, "off"),
        ("climate.a", 21.0, 15.0, "cool"),
        ("climate.a", 21.0, 15.0, "unavailable"),
        ("climate.a", 21.0, 15.0, "unknown"),
        ("climate.a", None, 15.0, "heat"),
        ("climate.a", 21.0, None, "heat"),
        ("climate.a", "21", 15.0, "heat"),
        ("climate.a", 21.0, "unknown", "heat"),
        ("climate.a", float("nan"), 15.0, "heat"),
        ("climate.a", float("inf"), 15.0, "heat"),
    ],
)
async def test_e_other_skips_other_climates_that_do_not_qualify(
    hass: HomeAssistant, thermostat: FakeThermostat, state: tuple[Any, ...]
) -> None:
    set_other(hass, *state)
    assert await e_other_of(hass, thermostat, other_options("climate.a")) is None


async def test_e_other_skips_climate_without_the_attributes(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    hass.states.async_set("climate.a", "heat", {})
    assert await e_other_of(hass, thermostat, other_options("climate.a")) is None


async def test_e_other_skips_missing_entity_but_uses_the_others(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.b", 21.0, 20.0)
    options = other_options("climate.ghost", "climate.b")
    assert await e_other_of(hass, thermostat, options) == pytest.approx(1.0)


async def test_e_other_is_none_for_empty_list(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 21.0, 15.0)
    assert await e_other_of(hass, thermostat, other_options()) is None


async def test_e_other_is_none_when_term_disabled(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 21.0, 15.0)
    options = other_options("climate.a", other_enabled=False)
    assert await e_other_of(hass, thermostat, options) is None


async def test_legacy_entry_without_other_room_options_still_works(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    # Options as stored before the other-room term existed.
    thermostat.current_temperature = 20.1
    options = {"c_int": 2.0, "k_i": 1.0, "tau_leak_h": 8.0}
    assert await e_other_of(hass, thermostat, options) is None
    assert thermostat.prop_algorithm.on_percent == pytest.approx(0.8)


async def test_cold_other_room_closes_valve_of_this_room_slightly_above_target(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 21.0, 20.0)  # e_other = 1.0 = e_full
    await setup_entry(hass, make_tpi_entry(hass, options=other_options("climate.a")))
    handler = await start_handler(hass, thermostat)
    thermostat.current_temperature = 20.1  # e = -0.1, I = 0: v_p = 1 - 0.2 - 1
    await handler.async_recalculate()
    assert thermostat.prop_algorithm.on_percent == 0.0
    assert thermostat.prop_algorithm.t_o == pytest.approx(-1.0)


async def test_direct_calculate_by_vtherm_reads_current_other_room_state(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 21.0, 20.9)
    await setup_entry(hass, make_tpi_entry(hass, options=other_options("climate.a")))
    await start_handler(hass, thermostat)
    algorithm = thermostat.prop_algorithm
    algorithm.calculate(20.0, 20.1, 5.0, 0.0, "heat")
    assert algorithm.e_other == pytest.approx(0.1)
    set_other(hass, "climate.a", 21.0, 20.0)
    algorithm.calculate(20.0, 20.1, 5.0, 0.0, "heat")
    assert algorithm.e_other == pytest.approx(1.0)


async def test_periodic_tick_picks_up_other_room_changes(
    hass: HomeAssistant, thermostat: FakeThermostat, freezer: Any
) -> None:
    set_other(hass, "climate.a", 21.0, 20.9)
    await setup_entry(hass, make_tpi_entry(hass, options=other_options("climate.a")))
    await start_handler(hass, thermostat)
    set_other(hass, "climate.a", 21.0, 19.0)
    await tick(hass, freezer, 300)
    assert thermostat.prop_algorithm.e_other == pytest.approx(2.0)


async def test_other_room_state_change_alone_triggers_no_recalculation(
    hass: HomeAssistant, thermostat: FakeThermostat
) -> None:
    set_other(hass, "climate.a", 21.0, 20.9)
    await setup_entry(hass, make_tpi_entry(hass, options=other_options("climate.a")))
    handler = await start_handler(hass, thermostat)
    await handler.async_recalculate()
    calls = thermostat.async_control_heating.await_count
    set_other(hass, "climate.a", 21.0, 15.0)
    await hass.async_block_till_done()
    assert thermostat.async_control_heating.await_count == calls
    assert thermostat.prop_algorithm.e_other == pytest.approx(0.1)  # still stale
