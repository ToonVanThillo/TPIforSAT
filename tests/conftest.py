"""Test harness: a stub versatile_thermostat integration and a fake thermostat runtime.

Only what the project doesn't own is faked: the VTherm integration (a stub that
loads and sets up) and the VTherm thermostat runtime the handler talks to.
"""

from collections.abc import AsyncGenerator, Callable, Generator
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)
from vtherm_api.vtherm_api import VThermAPI

from custom_components.tpi_for_sat.const import VTHERM_DOMAIN

from .helpers import FakeThermostat


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let HA load custom_components/tpi_for_sat."""


@pytest.fixture(autouse=True)
def _reset_vtherm_api() -> Generator[None]:
    """VThermAPI keeps the hass in a class attribute; don't leak it between tests."""
    yield
    VThermAPI.reset_vtherm_api()  # type: ignore[no-untyped-call]


@pytest.fixture(autouse=True)
def vtherm_stub(hass: HomeAssistant) -> None:
    """Stand in for the versatile_thermostat integration, a manifest dependency."""
    mock_integration(
        hass,
        MockModule(
            VTHERM_DOMAIN,
            async_setup_entry=AsyncMock(return_value=True),
            async_unload_entry=AsyncMock(return_value=True),
        ),
    )


@pytest.fixture
async def thermostat(hass: HomeAssistant) -> AsyncGenerator[FakeThermostat]:
    """A thermostat; its handler is removed afterwards, as VTherm does on removal."""
    thermostat = FakeThermostat(hass)
    yield thermostat
    if thermostat.handler is not None:
        thermostat.handler.remove()
        await hass.async_block_till_done()


@pytest.fixture
def register_climate(hass: HomeAssistant) -> Callable[..., str]:
    """Register a VTherm climate entity on a device; return its entity_id."""

    def _register(unique_id: str = "vt-living", name: str = "Living") -> str:
        vt_entry = MockConfigEntry(domain=VTHERM_DOMAIN)
        vt_entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=vt_entry.entry_id,
            identifiers={(VTHERM_DOMAIN, unique_id)},
            name=name,
        )
        entity = er.async_get(hass).async_get_or_create(
            "climate",
            VTHERM_DOMAIN,
            unique_id,
            config_entry=vt_entry,
            device_id=device.id,
            suggested_object_id=name.lower(),
        )
        hass.states.async_set(entity.entity_id, "heat", {"friendly_name": name})
        return entity.entity_id

    return _register
