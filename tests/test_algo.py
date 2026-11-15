"""Control law tests, written from the "Control law" section of docs/design.md."""

import math
from typing import Any

import pytest

from custom_components.tpi_for_sat.algo import SatTpiAlgorithm

TARGET = 20.0


class Clock:
    """Deterministic clock; `advance` takes hours, the algorithm reads seconds."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, hours: float) -> None:
        self.t += hours * 3600


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def algo(clock: Clock) -> SatTpiAlgorithm:
    return SatTpiAlgorithm(c_int=2.0, k_i=1.0, tau_leak_h=8.0, clock=clock)


def heat(
    a: SatTpiAlgorithm,
    room: float | None,
    target: float | None = TARGET,
    *,
    power_shedding: bool = False,
    off_reason: str | None = None,
) -> None:
    a.calculate(
        target,
        room,
        5.0,
        0.0,
        "heat",
        power_shedding=power_shedding,
        off_reason=off_reason,
    )


def test_no_output_before_first_calculate(algo: SatTpiAlgorithm) -> None:
    assert algo.on_percent is None
    assert algo.calculated_on_percent == 0.0


def test_room_at_target_with_empty_integral_is_fully_open(
    algo: SatTpiAlgorithm,
) -> None:
    heat(algo, 20.0)
    assert algo.on_percent == 1.0
    assert algo.integral == 0.0
    assert algo.e == 0.0


def test_room_well_above_target_shuts_valve(algo: SatTpiAlgorithm) -> None:
    heat(algo, 21.0)  # e=-1, v_p = 1 + 2*(-1) = -1
    assert algo.on_percent == 0.0
    assert algo.v_p == pytest.approx(-1.0)
    assert algo.saturated is True


def test_room_slightly_above_target_partially_closes(algo: SatTpiAlgorithm) -> None:
    heat(algo, 20.3)  # e=-0.3, v_p = 1 - 0.6 = 0.4
    assert algo.on_percent == pytest.approx(0.4)
    assert algo.saturated is False


def test_cold_room_saturates_open(algo: SatTpiAlgorithm) -> None:
    heat(algo, 18.0)  # e=2, v_p = 5
    assert algo.on_percent == 1.0
    assert algo.v_p == pytest.approx(5.0)
    assert algo.saturated is True


def test_integral_step_uses_previous_error_dt_and_leak(
    algo: SatTpiAlgorithm, clock: Clock
) -> None:
    heat(algo, 20.1)  # e=-0.1
    clock.advance(0.25)
    heat(algo, 20.1)
    # I = -0.1*0.25 = -0.025, then leak: -0.025 * (1 - 0.25/8) = -0.02421875
    assert algo.integral == pytest.approx(-0.02421875)
    assert algo.on_percent == pytest.approx(1 - 0.2 - 0.02421875)


def test_integral_uses_error_of_previous_update_not_current(
    algo: SatTpiAlgorithm, clock: Clock
) -> None:
    heat(algo, 20.1)  # e=-0.1 holds during the interval
    clock.advance(0.25)
    heat(algo, 20.4)  # new e=-0.4 must not be integrated yet
    assert algo.integral == pytest.approx(-0.02421875)


def test_first_update_only_records_time(algo: SatTpiAlgorithm, clock: Clock) -> None:
    clock.advance(100)
    heat(algo, 20.1)
    assert algo.integral == 0.0


def test_dt_is_capped(algo: SatTpiAlgorithm, clock: Clock) -> None:
    heat(algo, 20.1)
    clock.advance(10)
    heat(algo, 20.1)
    # dt = 0.5 h: I = -0.1*0.5 = -0.05, leak: -0.05 * (1 - 0.5/8) = -0.046875
    assert algo.integral == pytest.approx(-0.046875)


def test_integral_learns_partial_opening_at_steady_state(clock: Clock) -> None:
    """Room equilibrates at 18 + 4*v, so v = 0.5 holds 20 K.

    At steady state the leak balances integration: e = I/(k_i*tau) = I/8. With
    room = 18 + 4v and v = 1 + 2e + I, the exact solution is I = -16/41 (about
    -0.390), v about 0.512, room about 20.05; the asserts allow for that.
    """
    algo = SatTpiAlgorithm(clock=clock)
    room = 17.0
    step_h = 5 / 60
    for _ in range(int(200 / step_h)):
        heat(algo, room)
        assert algo.on_percent is not None
        clock.advance(step_h)
        room += (18 + 4 * algo.on_percent - room) * (1 - math.exp(-step_h / 1.0))
    heat(algo, room)
    assert algo.integral == pytest.approx(-0.4, abs=0.05)
    assert algo.on_percent == pytest.approx(0.5, abs=0.1)
    assert room == pytest.approx(TARGET, abs=0.2)


def test_no_windup_during_overshoot_valve_reopens_promptly(
    algo: SatTpiAlgorithm, clock: Clock
) -> None:
    for _ in range(120):  # 10 h at 5 K above target, valve shut
        heat(algo, 25.0)
        clock.advance(5 / 60)
    assert algo.on_percent == 0.0
    assert algo.integral == pytest.approx(0.0, abs=1e-12)
    heat(algo, 20.0)
    assert algo.on_percent == pytest.approx(1.0)


def test_no_integration_while_saturated_open_but_leak_still_acts(
    algo: SatTpiAlgorithm, clock: Clock
) -> None:
    algo.integral = -0.3
    heat(algo, 19.8)  # records time; e=0.2, v_p = 1 + 0.4 - 0.3 = 1.1 -> saturated
    assert algo.saturated is True
    clock.advance(0.25)
    heat(algo, 19.8)
    # Only leak: -0.3 * (1 - 0.25/8) = -0.290625 (no +k_i*e*dt)
    assert algo.integral == pytest.approx(-0.290625)


def test_leak_drifts_valve_open_at_target(algo: SatTpiAlgorithm, clock: Clock) -> None:
    algo.integral = -0.5
    heat(algo, 20.0)
    assert algo.on_percent == pytest.approx(0.5)
    clock.advance(0.5)
    heat(algo, 20.0)
    # -0.5 * (1 - 0.5/8) = -0.46875
    assert algo.integral == pytest.approx(-0.46875)
    assert algo.on_percent == pytest.approx(0.53125)
    for _ in range(200):
        clock.advance(0.5)
        heat(algo, 20.0)
    assert algo.on_percent == pytest.approx(1.0, abs=1e-3)


def test_integral_lower_bound_is_minus_one(clock: Clock) -> None:
    algo = SatTpiAlgorithm(k_i=100.0, clock=clock)
    heat(algo, 20.1)  # e=-0.1, v_p=0.8, unsaturated
    clock.advance(0.5)
    heat(algo, 20.1)
    # Raw step is -5; the clamp to -1 is applied after the leak.
    assert algo.integral == -1.0


def test_integral_upper_bound_is_zero(clock: Clock) -> None:
    algo = SatTpiAlgorithm(k_i=100.0, clock=clock)
    algo.integral = -0.1
    heat(algo, 19.96)  # e=0.04, v_p = 1 + 0.08 - 0.1 = 0.98, unsaturated
    clock.advance(0.5)
    heat(algo, 19.96)
    assert algo.integral == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [{"off_reason": "window_open"}, {"power_shedding": True}],
)
def test_not_heating_outputs_zero_and_freezes_integral(
    algo: SatTpiAlgorithm, clock: Clock, kwargs: dict[str, Any]
) -> None:
    heat(algo, 20.1)
    clock.advance(0.25)
    heat(algo, 20.1)
    frozen = algo.integral
    clock.advance(0.25)
    heat(algo, 20.1, **kwargs)
    assert algo.on_percent == 0.0
    assert algo.calculated_on_percent == 0.0
    assert algo.integral == frozen
    assert algo.e is None
    assert algo.v_p is None
    assert algo.saturated is False


def test_non_heat_mode_outputs_zero(algo: SatTpiAlgorithm) -> None:
    algo.calculate(TARGET, 18.0, 5.0, 0.0, "off")
    assert algo.on_percent == 0.0
    algo.calculate(TARGET, 18.0, 5.0, 0.0, "cool")
    assert algo.on_percent == 0.0
    algo.calculate(TARGET, 18.0, 5.0, 0.0, "sleep")
    assert algo.on_percent == 0.0


def test_hvac_mode_object_is_heating_when_str_is_heat(algo: SatTpiAlgorithm) -> None:
    class Mode:
        def __str__(self) -> str:
            return "heat"

    algo.calculate(TARGET, 20.3, 5.0, 0.0, Mode())
    assert algo.on_percent == pytest.approx(0.4)


def test_not_heating_takes_precedence_over_missing_temperature(
    algo: SatTpiAlgorithm,
) -> None:
    algo.calculate(None, None, None, None, "off")
    assert algo.on_percent == 0.0
    heat(algo, None, off_reason="window_open")
    assert algo.on_percent == 0.0
    heat(algo, None, target=None, power_shedding=True)
    assert algo.on_percent == 0.0


@pytest.mark.parametrize(
    ("room", "target"), [(None, TARGET), (20.0, None), (None, None)]
)
def test_missing_temperature_while_heating_gives_none_and_freezes(
    algo: SatTpiAlgorithm, clock: Clock, room: float | None, target: float | None
) -> None:
    heat(algo, 20.1)
    clock.advance(0.25)
    heat(algo, 20.1)
    frozen = algo.integral
    clock.advance(0.25)
    heat(algo, room, target=target)
    assert algo.on_percent is None
    assert algo.calculated_on_percent == 0.0
    assert algo.integral == frozen
    assert algo.e is None
    assert algo.v_p is None
    assert algo.saturated is False


@pytest.mark.parametrize("freeze", ["off", "shedding", "missing"])
def test_frozen_period_is_not_integrated_after_resume(
    algo: SatTpiAlgorithm, clock: Clock, freeze: str
) -> None:
    heat(algo, 20.1)
    clock.advance(0.25)
    heat(algo, 20.1)
    before = algo.integral
    clock.advance(0.1)
    if freeze == "off":
        algo.calculate(TARGET, 20.1, 5.0, 0.0, "off")
    elif freeze == "shedding":
        heat(algo, 20.1, power_shedding=True)
    else:
        heat(algo, None)
    clock.advance(5)
    heat(algo, 20.1)  # resume: only records time
    assert algo.integral == before
    clock.advance(0.25)
    heat(algo, 20.1)  # integrates only the 0.25 h since resume
    assert algo.integral == pytest.approx(
        before * (1 - 0.25 / 8) - 0.1 * 0.25 * (1 - 0.25 / 8)
    )


def test_integral_setter_clamps(algo: SatTpiAlgorithm) -> None:
    algo.integral = -3.0
    assert algo.integral == -1.0
    algo.integral = 2.0
    assert algo.integral == 0.0
    algo.integral = -0.4
    assert algo.integral == -0.4


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_integral_setter_rejects_non_finite(algo: SatTpiAlgorithm, bad: float) -> None:
    algo.integral = -0.4
    with pytest.raises(ValueError):
        algo.integral = bad
    assert algo.integral == -0.4


def test_restore_makes_next_update_only_record_time(
    algo: SatTpiAlgorithm, clock: Clock
) -> None:
    heat(algo, 20.1)
    clock.advance(0.25)
    algo.integral = -0.5
    heat(algo, 20.1)
    assert algo.integral == -0.5
    assert algo.on_percent == pytest.approx(1 - 0.2 - 0.5)


def test_set_parameters_keeps_integral_and_applies_new_values(
    algo: SatTpiAlgorithm,
) -> None:
    algo.integral = -0.5
    heat(algo, 20.0)
    algo.set_parameters(c_int=4.0, k_i=1.0, tau_leak_h=8.0)
    assert algo.integral == -0.5
    heat(algo, 20.1)  # same instant, dt=0 keeps I; e=-0.1, v_p = 1 - 0.4 - 0.5 = 0.1
    assert algo.on_percent == pytest.approx(0.1)


def test_set_parameters_keeps_timestamp(algo: SatTpiAlgorithm, clock: Clock) -> None:
    heat(algo, 20.1)
    algo.set_parameters(2.0, 1.0, 8.0)
    clock.advance(0.25)
    heat(algo, 20.1)
    assert algo.integral == pytest.approx(-0.02421875)


@pytest.mark.parametrize("tau", [0.0, -1.0, math.nan])
def test_non_positive_tau_leak_rejected(algo: SatTpiAlgorithm, tau: float) -> None:
    with pytest.raises(ValueError):
        SatTpiAlgorithm(tau_leak_h=tau)
    with pytest.raises(ValueError):
        algo.set_parameters(2.0, 1.0, tau)


def test_unknown_keyword_arguments_are_ignored(algo: SatTpiAlgorithm) -> None:
    algo.calculate(TARGET, 20.3, 5.0, 0.0, "heat", something_new=1, other="x")
    assert algo.on_percent == pytest.approx(0.4)
