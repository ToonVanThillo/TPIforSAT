"""SAT-friendly valve control law for Versatile Thermostat.

Implements "Control law" in docs/design.md. Plain Python without Home Assistant
imports, so it can be tested in isolation and reused in the simulator.
"""

import math
import time
from collections.abc import Callable

DT_CAP_H = 0.5  # cap on the integrated interval, in hours
DEFAULT_C_INT = 2.0  # 1/K
DEFAULT_K_I = 1.0  # 1/(K·h)
DEFAULT_TAU_LEAK_H = 8.0  # h
DEFAULT_E_START = 0.5  # K
DEFAULT_E_FULL = 1.0  # K


class SatTpiAlgorithm:
    """Integral valve controller placed on VTherm's `thermostat.prop_algorithm`."""

    def __init__(
        self,
        c_int: float = DEFAULT_C_INT,
        k_i: float = DEFAULT_K_I,
        tau_leak_h: float = DEFAULT_TAU_LEAK_H,
        e_start: float = DEFAULT_E_START,
        e_full: float = DEFAULT_E_FULL,
        clock: Callable[[], float] | None = None,
        other_error: Callable[[], float | None] | None = None,
    ) -> None:
        """Create the algorithm with I = 0 (fully open).

        `clock` returns seconds; None means time.monotonic, looked up at each call
        so test tools that patch it take effect. `other_error` returns e_other (K),
        which must be finite, or None if no other room qualifies; it is called once
        on every heating update, and None means T_o = 0. Raises ValueError as
        set_parameters.
        """
        self.set_parameters(c_int, k_i, tau_leak_h, e_start, e_full)
        self._clock = clock
        self._other_error = other_error
        self._integral = 0.0
        self._last_time: float | None = None
        self._e: float | None = None
        self._e_other: float | None = None
        self._t_o: float | None = None
        self._v_p: float | None = None
        self._saturated = False
        self._on_percent: float | None = None

    def set_parameters(
        self,
        c_int: float,
        k_i: float,
        tau_leak_h: float,
        e_start: float,
        e_full: float,
    ) -> None:
        """Replace the parameters; the integral and timestamp are kept.

        Raises ValueError if tau_leak_h is not positive or e_full is not greater
        than e_start (NaN included).
        """
        if not tau_leak_h > 0:
            raise ValueError(f"tau_leak_h must be positive, got {tau_leak_h}")
        if not e_full > e_start:
            raise ValueError(f"e_full ({e_full}) must exceed e_start ({e_start})")
        self._c_int = c_int
        self._k_i = k_i
        self._tau_leak_h = tau_leak_h
        self._e_start = e_start
        self._e_full = e_full

    def calculate(
        self,
        target_temp: float | None,
        current_temp: float | None,
        ext_current_temp: float | None,
        slope: float | None,
        hvac_mode: object,
        *,
        power_shedding: bool = False,
        off_reason: str | None = None,
        **kwargs: object,
    ) -> None:
        """Update the integral and on_percent; matches VTherm's `recalculate` call.

        `ext_current_temp`, `slope` and any further keyword arguments (so a later
        VTherm can add some) are unused. `hvac_mode` is VTherm's VThermHvacMode
        (or a str) and counts as heating when str() gives "heat".
        """
        if str(hvac_mode) != "heat" or off_reason is not None or power_shedding:
            self._freeze(0.0)
            return
        if target_temp is None or current_temp is None:
            # As in the built-in TPI, None makes VTherm keep the valve position.
            self._freeze(None)
            return

        e_other = self._other_error() if self._other_error is not None else None
        now = self._clock() if self._clock is not None else time.monotonic()
        if self._last_time is not None and self._e is not None:
            dt = min((now - self._last_time) / 3600, DT_CAP_H)
            self._integrate(dt, self._e, self._saturated)
        self._last_time = now

        e = target_temp - current_temp
        # Same as the design's -clamp(ratio, 0, 1), but without returning -0.0.
        t_o = (
            0.0
            if e_other is None
            else min(
                max((self._e_start - e_other) / (self._e_full - self._e_start), -1.0),
                0.0,
            )
        )
        v_p = 1 + self._c_int * e + min(max(self._integral + t_o, -1.0), 0.0)
        self._e = e
        self._e_other = e_other
        self._t_o = t_o
        self._v_p = v_p
        self._saturated = (v_p <= 0 and e < 0) or (v_p >= 1 and e > 0)
        self._on_percent = min(max(v_p, 0.0), 1.0)

    def _integrate(self, dt: float, e: float, saturated: bool) -> None:
        """Advance I over `dt` hours with the error and saturation of that interval."""
        integral = self._integral
        if not saturated:
            integral += self._k_i * e * dt
        integral -= integral * dt / self._tau_leak_h
        self._integral = min(max(integral, -1.0), 0.0)

    def _freeze(self, on_percent: float | None) -> None:
        """Set the output, keep I and drop the timestamp so this isn't integrated."""
        self._on_percent = on_percent
        self._last_time = None
        self._e = None
        self._e_other = None
        self._t_o = None
        self._v_p = None
        self._saturated = False

    @property
    def on_percent(self) -> float | None:
        """Valve demand in [0, 1]; None before any update or without temperatures."""
        return self._on_percent

    @property
    def calculated_on_percent(self) -> float:
        """on_percent with None as 0; read by VTherm for over_valve thermostats."""
        return self._on_percent or 0.0

    @property
    def integral(self) -> float:
        """Integral state I in [-1, 0]."""
        return self._integral

    @integral.setter
    def integral(self, value: float) -> None:
        """Restore I, clamped to [-1, 0]; the next update only records the timestamp.

        Raises ValueError if value is not finite.
        """
        if not math.isfinite(value):
            raise ValueError(f"integral must be finite, got {value}")
        self._integral = min(max(value, -1.0), 0.0)
        self._last_time = None

    @property
    def e(self) -> float | None:
        """Error target - room (K) of the latest heating update, None while frozen."""
        return self._e

    @property
    def e_other(self) -> float | None:
        """Largest error of the other rooms at the latest heating update, else None."""
        return self._e_other

    @property
    def t_o(self) -> float | None:
        """Other-room term T_o in [-1, 0] of the latest heating update, else None."""
        return self._t_o

    @property
    def v_p(self) -> float | None:
        """Unclamped valve demand of the latest heating update, None while frozen."""
        return self._v_p

    @property
    def saturated(self) -> bool:
        """Whether the latest heating update was saturated; False while frozen."""
        return self._saturated
