"""Fakes and helpers for the HA glue tests."""

from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from vtherm_api.vtherm_api import VThermAPI

from custom_components.tpi_for_sat.const import DOMAIN

TARGET = 20.0


class FakeCycleScheduler:
    """The cycle scheduler as the handler sees it."""

    def __init__(self) -> None:
        self.is_cycle_running = False
        self.start_cycle = AsyncMock()
        self.cancel_cycle = AsyncMock()


class FakeThermostat:
    """VTherm's thermostat runtime, as far as the handler uses it."""

    def __init__(
        self,
        hass: HomeAssistant,
        unique_id: str = "vt-living",
        entity_id: str = "climate.living",
    ) -> None:
        self.hass = hass
        self.name = "Living"
        self.unique_id = unique_id
        self.entity_id = entity_id
        self.prop_algorithm: Any = None
        self.current_temperature: float | None = TARGET
        self.target_temperature: float | None = TARGET
        self.current_outdoor_temperature: float | None = 5.0
        self.last_temperature_slope: float | None = 0.0
        self.vtherm_hvac_mode = "heat"
        self.hvac_off_reason: str | None = None
        self.is_overpowering_detected = False
        self.is_device_active = False
        self.cycle_scheduler = FakeCycleScheduler()
        self.async_underlying_entity_turn_off = AsyncMock()
        self.update_custom_attributes_calls = 0
        self.write_state_calls = 0
        self.async_control_heating = AsyncMock(side_effect=self._control_heating)
        self.handler: Any = None

    @property
    def on_percent(self) -> float | None:
        return self.prop_algorithm.on_percent if self.prop_algorithm else 0.0

    async def _control_heating(self, *args: object, **kwargs: object) -> bool:
        """VTherm's async_control_heating ends up in the handler's control_heating."""
        await self.handler.control_heating()
        return True

    def update_custom_attributes(self) -> None:
        self.update_custom_attributes_calls += 1

    def async_write_ha_state(self) -> None:
        self.write_state_calls += 1


def make_tpi_entry(
    hass: HomeAssistant,
    unique_id: str = "vt-living",
    options: dict[str, Any] | None = None,
    **kwargs: Any,
) -> MockConfigEntry:
    """Add a tpi_for_sat entry for the thermostat; not set up yet."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        title="Living",
        data={},
        options=options or {"c_int": 2.0, "k_i": 1.0, "tau_leak_h": 8.0},
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def start_handler(hass: HomeAssistant, thermostat: FakeThermostat) -> Any:
    """Do what VTherm does for a thermostat whose proportional function is ours."""
    factory = VThermAPI.get_vtherm_api(hass).get_prop_algorithm(DOMAIN)  # type: ignore[no-untyped-call]
    assert factory is not None
    handler = factory.create(thermostat)
    thermostat.handler = handler
    handler.init_algorithm()
    await handler.async_added_to_hass()
    await handler.async_startup()
    await hass.async_block_till_done()
    return handler
