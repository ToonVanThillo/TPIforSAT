"""VTherm handler around SatTpiAlgorithm: parameters, persistence, periodic update."""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.components.climate.const import ATTR_CURRENT_TEMPERATURE, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey
from vtherm_api.interfaces import InterfaceCycleScheduler, InterfaceThermostatRuntime

from .algo import SatTpiAlgorithm
from .const import (
    CONF_OTHER_CLIMATES,
    CONF_OTHER_ENABLED,
    DEFAULT_OPTIONS,
    DEFAULT_PARAMETERS,
    DOMAIN,
    signal_updated,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY_S = 300
UPDATE_INTERVAL = timedelta(minutes=5)

type StoredState = dict[str, float]


@dataclass
class TpiForSatData:
    """Shared runtime state, keyed by thermostat unique_id.

    `integrals` holds I of removed handlers, so the handler VTherm creates on a
    reload continues with it without waiting for the Store write.
    """

    handlers: dict[str, SatTpiHandler] = field(default_factory=dict)
    integrals: dict[str, float] = field(default_factory=dict)


DATA: HassKey[TpiForSatData] = HassKey(DOMAIN)


def get_data(hass: HomeAssistant) -> TpiForSatData:
    """Return the integration data, creating it on first use."""
    return hass.data.setdefault(DATA, TpiForSatData())


class SatTpiHandler:
    """One per thermostat using tpi_for_sat; created by VTherm through the factory."""

    def __init__(self, thermostat: InterfaceThermostatRuntime) -> None:
        """Bind to the thermostat and its store; init_algorithm places the algorithm."""
        self._thermostat = thermostat
        self._hass = thermostat.hass
        self._unique_id = thermostat.unique_id
        self._other_climates: Sequence[str] = ()
        self._algorithm = SatTpiAlgorithm(other_error=self._other_error)
        self._store: Store[StoredState] = Store(
            self._hass, STORAGE_VERSION, f"{DOMAIN}.{self._unique_id}"
        )
        self._stored_integral: float | None = None
        self._save_pending = False
        self._remove_timer: CALLBACK_TYPE | None = None
        self._removed = False

    @property
    def algorithm(self) -> SatTpiAlgorithm:
        """The algorithm placed on thermostat.prop_algorithm."""
        return self._algorithm

    def init_algorithm(self) -> None:
        """Place the algorithm on the thermostat, configured from its entry."""
        self._thermostat.prop_algorithm = self._algorithm
        entry = self._hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, self._unique_id
        )
        self.apply_entry(entry if entry is not None and not entry.disabled_by else None)
        get_data(self._hass).handlers[self._unique_id] = self
        self._notify()

    def apply_entry(self, entry: ConfigEntry | None) -> None:
        """Set the parameters and other climates from the entry's options.

        Uses the defaults if None. The integral is kept. Raises ValueError as
        SatTpiAlgorithm.set_parameters.
        """
        if entry is None:
            _LOGGER.warning(
                "%s - No %s config entry for this thermostat, using defaults",
                self._thermostat.name,
                DOMAIN,
            )
            options = DEFAULT_OPTIONS
        else:
            options = {**DEFAULT_OPTIONS, **entry.options}
        self._algorithm.set_parameters(
            **{key: options[key] for key in DEFAULT_PARAMETERS}
        )
        self._other_climates = (
            options[CONF_OTHER_CLIMATES] if options[CONF_OTHER_ENABLED] else ()
        )

    async def async_added_to_hass(self) -> None:
        """Restore I handed over by a removed handler, else from the Store.

        A Store that can't be read or holds malformed data logs a warning and
        leaves I = 0.
        """
        integral = get_data(self._hass).integrals.pop(self._unique_id, None)
        try:
            if integral is None:
                data = await self._store.async_load()
                if data is None:
                    return
                integral = float(data["integral"])
            self._algorithm.integral = integral
        except Exception:
            _LOGGER.warning(
                "%s - Could not restore the integral, starting from 0",
                self._thermostat.name,
                exc_info=True,
            )
            return
        self._stored_integral = self._algorithm.integral
        self._notify()

    async def async_startup(self) -> None:
        """Start the periodic update if the thermostat is already heating."""
        await self.on_state_changed(True)

    def remove(self) -> None:
        """Stop for good: save I now, hand it over and unregister the handler.

        VTherm may still call other methods afterwards; they no longer start the
        timer, recalculate or save.
        """
        self._removed = True
        self._stop_timer()
        self._hass.async_create_task(self._store.async_save(self._snapshot()))
        data = get_data(self._hass)
        data.integrals[self._unique_id] = self._algorithm.integral
        if data.handlers.get(self._unique_id) is self:
            del data.handlers[self._unique_id]
        self._notify()

    async def control_heating(
        self, timestamp: datetime | None = None, force: bool = False
    ) -> None:
        """Send VTherm's on_percent to the cycle scheduler, as the built-in TPI does."""
        del timestamp
        t = self._thermostat
        scheduler = t.cycle_scheduler
        if t.vtherm_hvac_mode == "off":
            if t.is_device_active:
                await t.async_underlying_entity_turn_off()
            elif scheduler is not None and scheduler.is_cycle_running:
                await scheduler.cancel_cycle()
        elif (on_percent := t.on_percent) is None:
            # As in the built-in TPI: no temperature yet, keep the current position.
            _LOGGER.info("%s - on_percent unknown, skipping cycle", t.name)
        elif scheduler is not None:
            await scheduler.start_cycle(t.vtherm_hvac_mode, on_percent, force)
        self._schedule_save()
        self._notify()

    async def on_state_changed(self, changed: bool) -> None:
        """Run the periodic update only while the thermostat is in heat mode."""
        del changed
        if self._thermostat.vtherm_hvac_mode == "heat" and not self._removed:
            if self._remove_timer is None:
                self._remove_timer = async_track_time_interval(
                    self._hass, self._async_tick, UPDATE_INTERVAL
                )
        else:
            self._stop_timer()

    def on_scheduler_ready(self, scheduler: InterfaceCycleScheduler) -> None:
        """Nothing to bind: the algorithm doesn't use cycle callbacks."""
        del scheduler

    def should_publish_intermediate(self) -> bool:
        """Always publish, as the built-in TPI handler does."""
        return True

    async def async_recalculate(self) -> None:
        """Re-evaluate the algorithm and let VTherm apply the result.

        Needed because VTherm only calls calculate on temperature changes, while
        the leak and parameter changes move the output too. Mirrors VTherm's own
        delayed valve recalculation. Does nothing once removed.
        """
        if self._removed:
            return
        t = self._thermostat
        self._algorithm.calculate(
            t.target_temperature,
            t.current_temperature,
            t.current_outdoor_temperature,
            t.last_temperature_slope,
            t.vtherm_hvac_mode or "off",
            power_shedding=t.is_overpowering_detected,
            off_reason=t.hvac_off_reason,
        )
        self._schedule_save()
        await t.async_control_heating()
        t.update_custom_attributes()
        t.async_write_ha_state()

    def _other_error(self) -> float | None:
        """Return the largest target - current of the other climates, or None.

        Counts climates in heat mode reporting a numeric target and current
        temperature; missing or unavailable entities are skipped.
        """
        errors: list[float] = []
        for entity_id in self._other_climates:
            state = self._hass.states.get(entity_id)
            if state is None or state.state != HVACMode.HEAT:
                continue
            target = state.attributes.get(ATTR_TEMPERATURE)
            current = state.attributes.get(ATTR_CURRENT_TEMPERATURE)
            if isinstance(target, int | float) and isinstance(current, int | float):
                error = float(target - current)
                if math.isfinite(error):
                    errors.append(error)
        return max(errors, default=None)

    async def _async_tick(self, now: datetime) -> None:
        """Periodic update, so the leak reaches the valve in a steady room."""
        del now
        await self.async_recalculate()

    def _stop_timer(self) -> None:
        """Cancel the periodic update, if running."""
        if self._remove_timer is not None:
            self._remove_timer()
            self._remove_timer = None

    def _schedule_save(self) -> None:
        """Write the integral at most SAVE_DELAY_S after it changes.

        Store.async_delay_save postpones its write on every call, so it is only
        called when no write is pending; the pending write picks up later changes.
        """
        if (
            self._removed
            or self._save_pending
            or self._algorithm.integral == self._stored_integral
        ):
            return
        self._save_pending = True
        self._store.async_delay_save(self._snapshot, SAVE_DELAY_S)

    def _snapshot(self) -> StoredState:
        """Return the state to store and record it as stored."""
        self._save_pending = False
        self._stored_integral = self._algorithm.integral
        return {"integral": self._stored_integral}

    def _notify(self) -> None:
        """Tell the diagnostic sensor to refresh."""
        async_dispatcher_send(self._hass, signal_updated(self._unique_id))


class SatTpiHandlerFactory:
    """Factory registered with VThermAPI under the name tpi_for_sat."""

    @property
    def name(self) -> str:
        """Return the proportional function name shown in VTherm."""
        return DOMAIN

    def create(self, thermostat: InterfaceThermostatRuntime) -> SatTpiHandler:
        """Create the handler for one thermostat."""
        return SatTpiHandler(thermostat)
